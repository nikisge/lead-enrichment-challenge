import json
import re
import logging
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Optional, List, Tuple

from config import get_settings
from models import WebhookPayload, ParsedJobPosting
from clients.llm_client import get_llm_client, ModelTier

logger = logging.getLogger(__name__)


@dataclass
class _ParseState:
    """Per-request parse state (thread/task-safe via ContextVar)."""
    used_fallback: bool = False
    warning: Optional[str] = None


_parse_state: ContextVar[_ParseState] = ContextVar('_parse_state')


def _get_parse_state() -> _ParseState:
    """Get or create per-context parse state (lazy init avoids shared mutable default)."""
    try:
        return _parse_state.get()
    except LookupError:
        state = _ParseState()
        _parse_state.set(state)
        return state


def get_last_parse_warnings() -> List[str]:
    """Get warnings from the last parse operation."""
    state = _get_parse_state()
    warnings = []
    if state.used_fallback:
        warnings.append("primary_api_key_failed")
        warnings.append("used_fallback_api_key")
    if state.warning:
        warnings.append(state.warning)
    return warnings

def reset_parse_warnings():
    """Reset warnings for new parse operation."""
    _parse_state.set(_ParseState())


SYSTEM_PROMPT = """Du bist ein Experte für die Analyse von Stellenanzeigen im DACH-Raum.
Extrahiere strukturierte Informationen aus der Stellenanzeige.

WICHTIG für company_domain:
- Extrahiere die Domain NUR wenn eine WEBSITE explizit im Text erwähnt wird (z.B. "www.firma.de", "firma.de")
- IGNORIERE Email-Domains komplett! Email-Adressen sind oft von Personalvermittlungen, nicht vom Unternehmen.
- Beispiel: Bei "Bewerbung an m.jaeger@pletschacher.de" für "Gröber Holzbau GmbH" → company_domain = null (NICHT pletschacher.de!)
- Setze company_domain auf null wenn du dir nicht 100% sicher bist

Regeln:
- Suche nach genannten Ansprechpartnern (oft am Ende: "Ihr Ansprechpartner", "Kontakt", "Bewerbung an")
- Extrahiere E-Mail-Adressen falls vorhanden (für Kontakt, NICHT für Domain!)
- Extrahiere Telefonnummern falls vorhanden (Format: +49, 0049, oder 0xxx)
- Bestimme den idealen fachlichen Entscheider für DIESE Stelle (z.B. CTO für IT, CFO für Finanzen). HR nur wenn die Stelle selbst im HR-Bereich ist.

Antworte NUR mit validem JSON im folgenden Format (keine anderen Texte):
{
    "company_name": "Firmenname",
    "company_domain": "firma.de (NUR aus Website-Erwähnung, NICHT aus Email!) oder null",
    "contact_name": "Vorname Nachname oder null",
    "contact_email": "email@firma.de oder null",
    "contact_phone": "+49 123 456789 oder null",
    "target_titles": ["HR Manager", "Personalleiter"],
    "department": "HR/Personal/IT/etc oder null",
    "location": "Stadt, Land oder null"
}"""


async def parse_job_posting(payload: WebhookPayload) -> ParsedJobPosting:
    """
    Use LLM to extract structured info from job posting via OpenRouter.
    Falls back to regex extraction if LLM fails.
    """
    reset_parse_warnings()

    try:
        result = await _llm_parse(payload)
        return result
    except Exception as e:
        logger.warning(f"LLM parsing failed: {e}")

    # LLM failed - use regex fallback
    logger.warning("All API keys failed, using regex fallback")
    _get_parse_state().warning = "llm_parse_failed_used_regex_fallback"
    return _regex_parse(payload)


async def _llm_parse(payload: WebhookPayload) -> ParsedJobPosting:
    """Parse job posting using OpenRouter LLM client."""
    llm = get_llm_client()

    user_content = f"""Stellenanzeige:
Firma: {payload.company}
Titel: {payload.title}
Ort: {payload.location or 'Nicht angegeben'}

Beschreibung:
{payload.description[:6000]}"""

    result = await llm.call_json(
        prompt=user_content,
        tier=ModelTier.SMART,
        system_prompt=SYSTEM_PROMPT,
        max_tokens=1024,
    )

    if not result or not isinstance(result, dict):
        raise ValueError(f"LLM returned invalid result: {type(result)}")

    data = result

    # Ensure target_titles has defaults if empty
    if not data.get("target_titles"):
        data["target_titles"] = _get_default_titles(payload.title)

    # Remove any extra fields not in ParsedJobPosting
    allowed_fields = {"company_name", "company_domain", "contact_name", "contact_email",
                      "contact_phone", "target_titles", "department", "location"}
    data = {k: v for k, v in data.items() if k in allowed_fields}

    return ParsedJobPosting(**data)


def _regex_parse(payload: WebhookPayload) -> ParsedJobPosting:
    """
    Fallback regex-based parsing.

    WICHTIG: Dies ist nur der Fallback wenn AI komplett fehlschlägt.
    Wir setzen domain=None und lassen die Pipeline später via Google Search
    die richtige Domain finden. Besser keine Domain als eine falsche!
    """
    description = payload.description
    company_name = payload.company

    # Extract email
    email_pattern = r'[\w\.-]+@[\w\.-]+\.\w+'
    emails = re.findall(email_pattern, description)
    contact_email = emails[0] if emails else None

    # Extract phone numbers (German formats)
    phone_pattern = r'(?:\+49|0049|0)\s*[\d\s\-/()]{8,20}'
    phones = re.findall(phone_pattern, description)
    contact_phone = None
    if phones:
        # Clean and take first valid phone
        for phone in phones:
            cleaned = re.sub(r'[^\d+]', '', phone)
            if len(cleaned) >= 10:
                contact_phone = phone.strip()
                break

    # NICHT automatisch Domain extrahieren im Fallback!
    # Das war der Bug: Email-Domain von Personalvermittlung wurde verwendet.
    # Besser: Domain = None setzen, Pipeline macht dann Google Search.
    domain = None
    logger.warning(f"Regex fallback: Setting domain=None for '{company_name}' - Pipeline will use Google Search")

    # Extract contact name (common patterns)
    contact_name = None
    patterns = [
        r'[Aa]nsprechpartner(?:in)?[:\s]+([A-ZÄÖÜ][a-zäöüß]+\s+[A-ZÄÖÜ][a-zäöüß]+)',
        r'[Kk]ontakt[:\s]+([A-ZÄÖÜ][a-zäöüß]+\s+[A-ZÄÖÜ][a-zäöüß]+)',
        r'[Ii]hr[e]?\s+[Aa]nsprechpartner(?:in)?[:\s]+([A-ZÄÖÜ][a-zäöüß]+\s+[A-ZÄÖÜ][a-zäöüß]+)',
    ]
    for pattern in patterns:
        match = re.search(pattern, description)
        if match:
            contact_name = match.group(1).strip()
            break

    return ParsedJobPosting(
        company_name=payload.company,
        company_domain=domain,  # None - let Pipeline find via Google
        contact_name=contact_name,
        contact_email=contact_email,
        contact_phone=contact_phone,
        target_titles=_get_default_titles(payload.title),
        department=_detect_department(payload.title, payload.category),
        location=payload.location
    )


def _get_default_titles(job_title: str) -> List[str]:
    """Get relevant decision maker titles based on job posting."""
    job_lower = job_title.lower()

    # HR/Personnel related
    if any(x in job_lower for x in ['hr', 'personal', 'recruiting', 'talent']):
        return [
            "HR Manager", "HR-Manager", "Personalleiter", "Personalleiterin",
            "Head of HR", "HR Director", "Leiter Personal",
            "Recruiting Manager", "Head of Recruiting"
        ]

    # IT related — use word boundary for 'it' to avoid false positives like 'hospitality', 'digital'
    if re.search(r'\bit\b', job_lower) or any(x in job_lower for x in ['software', 'developer', 'engineer', 'tech', 'entwickl']):
        return [
            "CTO", "IT-Leiter", "Head of IT", "IT Manager",
            "Leiter Softwareentwicklung", "Head of Engineering",
        ]

    # Sales related
    if any(x in job_lower for x in ['sales', 'vertrieb', 'account', 'verkauf']):
        return [
            "Vertriebsleiter", "Vertriebsleiterin", "Head of Sales", "Sales Director",
            "Leiter Vertrieb", "CSO",
        ]

    # Finance related
    if any(x in job_lower for x in ['finance', 'finanz', 'finanzen', 'buchhal', 'controlling', 'accounting']):
        return [
            "CFO", "Finanzleiter", "Finanzleiterin", "Head of Finance",
            "Kaufmännischer Leiter", "Controller", "Leiter Finanzen",
        ]

    # Healthcare/Pflege related
    if any(x in job_lower for x in ['pflege', 'arzt', 'medizin', 'klinik', 'gesundheit', 'krankenpflege', 'altenpflege']):
        return [
            "Pflegedienstleitung", "Ärztlicher Direktor", "Medizinischer Leiter",
            "Geschäftsführer", "Geschäftsführerin", "CEO",
        ]

    # Marketing related
    if any(x in job_lower for x in ['marketing', 'kommunikation', 'brand']):
        return [
            "CMO", "Marketingleiter", "Marketingleiterin", "Head of Marketing",
            "Leiter Marketing", "Leiter Kommunikation",
        ]

    # Default: CEO-first, HR is fallback only
    return [
        "Geschäftsführer", "Geschäftsführerin", "CEO", "Inhaber",
        "HR Manager", "Personalleiter", "Personalleiterin",
        "Head of HR", "Leiter Personal"
    ]


def _detect_department(job_title: str, category: Optional[str]) -> Optional[str]:
    """Detect department from job title or category."""
    text = f"{job_title} {category or ''}".lower()

    if any(x in text for x in ['hr', 'personal', 'recruiting', 'talent']):
        return "HR"
    # Use word boundary for 'it' to avoid false positives like 'hospitality', 'digital'
    if re.search(r'\bit\b', text) or any(x in text for x in ['software', 'tech', 'developer', 'entwickl']):
        return "IT"
    if any(x in text for x in ['sales', 'vertrieb', 'verkauf']):
        return "Sales"
    if any(x in text for x in ['marketing', 'kommunikation']):
        return "Marketing"
    if any(x in text for x in ['finance', 'finanz', 'accounting', 'buchhal', 'controlling']):
        return "Finance"
    if any(x in text for x in ['logistik', 'logistic', 'supply chain', 'transport', 'versand', 'lagerwirt']):
        return "Logistik"
    if any(x in text for x in ['einkauf', 'procurement', 'beschaffung', 'purchasing']):
        return "Einkauf"
    if any(x in text for x in ['produktion', 'fertigung', 'manufacturing', 'operations']):
        return "Produktion"
    if any(x in text for x in ['pflege', 'altenpflege', 'krankenpflege']):
        return "Pflege"
    if any(x in text for x in ['arzt', 'medizin', 'klinik', 'gesundheit', 'pharmaz']):
        return "Medizin"
    if any(x in text for x in ['recht', 'jurist', 'anwalt', 'legal', 'compliance']):
        return "Recht"
    if any(x in text for x in ['beratung', 'consulting', 'consultant', 'berater']):
        return "Consulting"

    return None
