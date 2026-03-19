"""
AI-based Validation for Lead Enrichment.

Uses LLMs to intelligently validate:
- Contact names (real person vs. garbage)
- Email addresses (belongs to company?)
- LinkedIn matches (currently employed?)
- Overall candidate quality

Replaces rigid rule-based validation with contextual AI understanding.
"""

import logging
from typing import Optional, List, Dict, Any
from dataclasses import dataclass

from clients.llm_client import get_llm_client, ModelTier
from utils.cost_tracker import track_llm

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    """Result of a validation check."""
    valid: bool
    reason: str
    confidence: float = 0.9


@dataclass
class CandidateValidation:
    """Full validation result for a candidate."""
    name: str
    name_valid: bool
    name_reason: str
    title: Optional[str]            # Carried through from source (job page, team page, etc.)
    role_category: str              # "department_head"|"executive"|"hr"|"other"|"invalid"
    inferred_title: Optional[str]   # Clean title string inferred by LLM (e.g. "CTO", "IT-Leiter")
    email: Optional[str]
    email_valid: bool
    email_reason: str
    overall_valid: bool
    relevance_score: int  # 0-100
    validation_notes: str


async def validate_person_name(name: str) -> ValidationResult:
    """
    Validate if a string is a real person name.

    Checks:
    - Is it a real first + last name?
    - Not an HTML artifact or menu item?
    - Not a generic placeholder?

    Args:
        name: The name to validate

    Returns:
        ValidationResult with valid flag and reason
    """
    if not name or len(name.strip()) < 3:
        return ValidationResult(
            valid=False,
            reason="Name zu kurz",
            confidence=1.0
        )

    name = name.strip()

    # Quick heuristic: single-word entries are usually invalid (nav items, headings),
    # but first names from team pages are handled upstream — fall through to AI check.
    words = name.split()
    if len(words) < 2:
        # Let the AI decide — a first name like "Thomas" could be valid in context
        pass

    # Quick check for obvious non-names (save API call)
    obvious_invalid_patterns = [
        'weitere', 'möglichkeiten', 'helfen', 'navigation', 'menü',
        'kontakt', 'impressum', 'startseite', 'übersicht', 'angebot',
        'unsere', 'unser team', 'mehr erfahren', 'weiterlesen',
        'hier klicken', 'jetzt bewerben', 'alle rechte', 'datenschutz',
        'cookie', 'agb', 'nutzungsbedingungen'
    ]

    name_lower = name.lower()
    for pattern in obvious_invalid_patterns:
        if pattern in name_lower:
            return ValidationResult(
                valid=False,
                reason=f"Enthält ungültiges Muster: '{pattern}'",
                confidence=0.98
            )

    # For less obvious cases, use AI
    llm = get_llm_client()

    prompt = f"""Ist "{name}" ein echter deutscher Personenname?

Prüfe:
1. Ist es ein Vor- und Nachname einer echten Person?
2. Keine Überschrift, Menüpunkt oder generischer Text?
3. Keine Firma oder Organisation?

Antworte als JSON:
{{"valid": true/false, "reason": "Kurze Begründung"}}"""

    result = await llm.call_json(prompt, tier=ModelTier.FAST)
    track_llm("candidate_validate", tier="haiku")  # Name validation uses Haiku

    if result and isinstance(result, dict):
        return ValidationResult(
            valid=result.get("valid", False),
            reason=result.get("reason", "KI-Validierung"),
            confidence=0.9
        )

    # Fallback: assume valid if AI call fails
    logger.warning(f"Name validation LLM failed for '{name}', assuming valid")
    return ValidationResult(
        valid=True,
        reason="Keine KI-Validierung möglich, angenommen gültig",
        confidence=0.5
    )


async def validate_email_for_company(
    email: str,
    company_name: str,
    company_domain: Optional[str]
) -> ValidationResult:
    """
    Validate if an email address belongs to a company.

    Context-aware validation:
    - Exact domain match = valid
    - Subdomain or related domain = valid (e.g., subsidiary)
    - Completely different company = invalid

    Args:
        email: Email address to validate
        company_name: Company name for context
        company_domain: Company domain if known

    Returns:
        ValidationResult with valid flag and reason
    """
    if not email or '@' not in email:
        return ValidationResult(
            valid=False,
            reason="Keine gültige E-Mail-Adresse",
            confidence=1.0
        )

    email_domain = email.split('@')[1].lower()

    # Quick check: exact domain match
    if company_domain:
        clean_domain = company_domain.lower().replace('www.', '')
        if email_domain == clean_domain:
            return ValidationResult(
                valid=True,
                reason="Domain stimmt exakt überein",
                confidence=1.0
            )

        # Subdomain check
        if email_domain.endswith('.' + clean_domain):
            return ValidationResult(
                valid=True,
                reason="Subdomain der Firmendomain",
                confidence=0.95
            )

    # For more complex cases (subsidiaries, parent companies, etc.), use AI
    llm = get_llm_client()

    prompt = f"""Gehört die E-Mail "{email}" zur Firma "{company_name}" (Domain: {company_domain or 'unbekannt'})?

Prüfe kontextabhängig:
1. Stimmt die Domain überein?
2. Könnte es eine Subdomain oder Tochterfirma sein?
3. Ist es eine komplett andere Firma?

Beispiele:
- "max@dkms.de" bei "DKMS Group" = VALID (gleiche Firma)
- "anna@social.dkms.de" bei "DKMS Group" = VALID (Subdomain)
- "anna@freewheel.com" bei "Diakoneo" = INVALID (andere Firma!)

Antworte als JSON:
{{"valid": true/false, "reason": "Kurze Begründung"}}"""

    result = await llm.call_json(prompt, tier=ModelTier.FAST)
    track_llm("email_match", tier="haiku")  # Email validation uses Haiku

    if result and isinstance(result, dict):
        return ValidationResult(
            valid=result.get("valid", False),
            reason=result.get("reason", "KI-Validierung"),
            confidence=0.85
        )

    # Fallback: be conservative - reject if unsure and domains don't match
    if company_domain and email_domain != company_domain.lower().replace('www.', ''):
        return ValidationResult(
            valid=False,
            reason="Domain stimmt nicht überein (Fallback)",
            confidence=0.6
        )

    return ValidationResult(
        valid=True,
        reason="Keine Validierung möglich",
        confidence=0.5
    )


async def validate_linkedin_match(
    linkedin_snippet: str,
    linkedin_title: str,
    person_name: str,
    company_name: str
) -> ValidationResult:
    """
    Validate if a LinkedIn search result matches and is current.

    Checks:
    - Does the name match?
    - Does the person CURRENTLY work at this company?
    - Watch for "former", "ex-", "previously" indicators

    Args:
        linkedin_snippet: Search result snippet
        linkedin_title: Search result title
        person_name: Name we're looking for
        company_name: Company we expect them to work at

    Returns:
        ValidationResult with is_current assessment
    """
    if not linkedin_snippet and not linkedin_title:
        return ValidationResult(
            valid=False,
            reason="Keine LinkedIn-Daten zum Validieren",
            confidence=0.5
        )

    llm = get_llm_client()

    prompt = f"""Analysiere dieses LinkedIn-Suchergebnis:

Gesuchte Person: "{person_name}"
Erwartete Firma: "{company_name}"

LinkedIn-Titel: {linkedin_title}
LinkedIn-Snippet: {linkedin_snippet}

Prüfe:
1. Stimmt der Name überein? (Teilübereinstimmung ok)
2. Arbeitet die Person AKTUELL bei dieser Firma?
   - "bei/at {company_name}" = aktuell
   - "ehemalig/former/ex-" = NICHT aktuell
   - "bis 2024" = NICHT aktuell

Antworte als JSON:
{{
    "name_matches": true/false,
    "is_current": true/false,
    "reason": "Kurze Begründung",
    "confidence": 0.0-1.0
}}"""

    result = await llm.call_json(prompt, tier=ModelTier.FAST)
    track_llm("linkedin_match", tier="haiku")  # LinkedIn validation uses Haiku

    if result and isinstance(result, dict):
        # Both name must match AND be current employee
        is_valid = result.get("name_matches", False) and result.get("is_current", False)
        return ValidationResult(
            valid=is_valid,
            reason=result.get("reason", "KI-Validierung"),
            confidence=result.get("confidence", 0.8)
        )

    return ValidationResult(
        valid=False,
        reason="LinkedIn-Validierung fehlgeschlagen",
        confidence=0.5
    )


async def validate_and_rank_candidates(
    candidates: List[Dict[str, Any]],
    company_name: str,
    company_domain: Optional[str],
    job_category: Optional[str] = None,
    target_titles: List[str] = [],
    department: Optional[str] = None,
) -> List[CandidateValidation]:
    """
    Validate and rank all candidates in one AI call.

    Validates:
    - Name is a real person
    - Email belongs to company
    - Ranks by relevance for the job with department-head-first scoring

    Args:
        candidates: List of candidate dicts with name, email, title, source
        company_name: Company name
        company_domain: Company domain
        job_category: Job category for relevance scoring
        target_titles: Ideal decision-maker titles for this job
        department: Detected department (e.g. "IT", "Finance")

    Returns:
        List of validated candidates, sorted by relevance (highest first)
    """
    if not candidates:
        return []

    # Filter out obvious invalid candidates first.
    # Allow first-name-only contacts (e.g. "Thomas") when they come from a trusted
    # source like a team page and are accompanied by a job title — common practice
    # at German SMEs who only show first names on their /ueber-uns page.
    filtered_candidates = []
    for c in candidates:
        name = c.get("name", "").strip()
        title = c.get("title", "") or ""
        source = c.get("source", "") or ""
        if not name or len(name) < 2:
            continue
        is_full_name = len(name.split()) >= 2
        is_first_name_with_title = len(name.split()) == 1 and bool(title)
        if is_full_name or is_first_name_with_title:
            filtered_candidates.append(c)

    if not filtered_candidates:
        logger.info("No valid candidates after initial filter")
        return []

    # Build name→title lookup so the source title survives through LLM output
    title_lookup = {
        c.get("name", "").strip().lower(): c.get("title")
        for c in filtered_candidates
    }

    llm = get_llm_client()

    category_context = f"\nDie Stelle ist im Bereich: {department or job_category}" if (department or job_category) else ""
    titles_context = f"\nGesuchte Entscheider-Titel: {', '.join(target_titles[:5])}" if target_titles else ""

    prompt = f"""Validiere und bewerte diese Kontakt-Kandidaten für "{company_name}" (Domain: {company_domain or 'unbekannt'}).{category_context}{titles_context}

Kandidaten:
{filtered_candidates}

Prüfe für JEDEN Kandidaten:

1. name_valid: Ist der Name ein echter Personenname?
   - UNGÜLTIG: Überschriften, Menüpunkte, Platzhalter, Firmennamen
   - GÜLTIG: Echte Vor- und Nachnamen
   - GÜLTIG: Auch Vornamen allein (z.B. "Thomas", "Veronica") wenn ein Jobtitel angegeben ist — viele deutsche KMU zeigen auf ihrer Team-Seite nur Vornamen

2. email_valid: Gehört die E-Mail zur Firma?
   - GÜLTIG: Gleiche Domain, Subdomain, Mutter-/Tochterfirma
   - UNGÜLTIG: Komplett andere Firma (z.B. @freewheel.com bei Diakoneo)

3. relevance_score: 0-100 Punkte
   - Titel entspricht gesuchtem Profil (Abteilungsleiter passend zur Stelle): 95
   - Geschäftsführer/CEO/Inhaber: 65
   - HR/Personal/Recruiting: 45
   - Sonstige benannte Kontakte: 35
   - Ungültige Kandidaten: 0
   - Kandidaten aus mehreren Quellen (multi_source_verified=true): +10 Bonuspunkte

4. overall_valid: true wenn name_valid UND (keine E-Mail ODER email_valid)

5. role_category: Kategorisiere die Rolle
   - "department_head": Titel passt zur gesuchten Abteilung (CTO bei IT, CFO bei Finanzen)
   - "executive": Geschäftsführer/CEO/Inhaber (nicht abteilungsspezifisch)
   - "hr": HR/Personal/Recruiting
   - "other": Sonstige gültige Kontakte
   - "invalid": Ungültiger Kandidat

6. inferred_title: Position/Titel des Kandidaten (z.B. "CTO", "IT-Leiter", "Geschäftsführer") oder null
   - Nur der Titel selbst, KEINE Erklärung oder Satz

Vertrauenswürdigkeit der Quelle beachten:
- job_url, impressum, team_page, description_hiring_manager: vertrauenswürdig (Firmenwebsite/Stellenanzeige)
- linkedin_fallback: ungeprüft (Google-Suche) — bei gleichem Titel bevorzuge die vertrauenswürdigere Quelle

Antworte als JSON-Array, sortiert nach relevance_score (höchste zuerst):
[{{
    "name": "...",
    "name_valid": true/false,
    "name_reason": "...",
    "email": "..." oder null,
    "email_valid": true/false,
    "email_reason": "...",
    "overall_valid": true/false,
    "relevance_score": 0-100,
    "role_category": "department_head|executive|hr|other|invalid",
    "inferred_title": "CTO" oder null,
    "validation_notes": "Kurze Zusammenfassung"
}}]"""

    result = await llm.call_json(prompt, tier=ModelTier.BALANCED)
    track_llm("candidate_validate", tier="sonnet")  # Batch validation uses Sonnet

    if not result or not isinstance(result, list):
        logger.warning(f"Candidate validation LLM failed (type={type(result)}), returning {len(filtered_candidates)} unvalidated")
        # Fallback: return candidates without validation
        return [
            CandidateValidation(
                name=c.get("name", ""),
                name_valid=True,
                name_reason="Keine Validierung",
                title=c.get("title"),
                role_category="other",
                inferred_title=None,
                email=c.get("email"),
                email_valid=True,
                email_reason="Keine Validierung",
                overall_valid=True,
                relevance_score=50,
                validation_notes="Fallback - keine KI-Validierung"
            )
            for c in filtered_candidates
        ]

    # Parse results
    validated = []
    for item in result:
        if not isinstance(item, dict):
            continue

        validated.append(CandidateValidation(
            name=item.get("name", ""),
            name_valid=item.get("name_valid", False),
            name_reason=item.get("name_reason", ""),
            title=title_lookup.get(item.get("name", "").strip().lower()),
            role_category=item.get("role_category", "other"),
            inferred_title=item.get("inferred_title"),
            email=item.get("email"),
            email_valid=item.get("email_valid", True),
            email_reason=item.get("email_reason", ""),
            overall_valid=item.get("overall_valid", False),
            relevance_score=item.get("relevance_score", 0),
            validation_notes=item.get("validation_notes", "")
        ))

    # Sort by relevance score (should already be sorted, but ensure)
    validated.sort(key=lambda x: x.relevance_score, reverse=True)

    # Filter to only valid candidates
    valid_candidates = [c for c in validated if c.overall_valid]

    logger.info(f"Validated {len(validated)} candidates, {len(valid_candidates)} are valid")

    return valid_candidates


async def quick_validate_contact(
    name: str,
    email: Optional[str],
    company_name: str,
    company_domain: Optional[str]
) -> bool:
    """
    Quick validation of a single contact.

    Returns True if contact passes all checks.
    """
    # Validate name
    name_result = await validate_person_name(name)
    if not name_result.valid:
        logger.info(f"Name validation failed: {name} - {name_result.reason}")
        return False

    # Validate email if provided
    if email:
        email_result = await validate_email_for_company(email, company_name, company_domain)
        if not email_result.valid:
            logger.info(f"Email validation failed: {email} - {email_result.reason}")
            return False

    return True
