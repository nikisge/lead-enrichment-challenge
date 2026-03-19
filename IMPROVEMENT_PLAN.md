# Lead Enrichment — Decision Maker Matching: Final Implementation Plan

All claims verified against actual source code. Structure: Issues → Fixes → Priority Table → Test Strategy.

---

## Part 1: What the Competitor Did Correctly (Keep As-Is)

| File | What They Fixed |
|------|----------------|
| `clients/ai_validator.py` | Added `target_titles` + `department` params; scoring: dept head=95, CEO=65, HR=45, other=35 |
| `clients/linkedin_search.py` | Added `target_titles` param; inverted search order (job titles → CEO → HR last); dynamic query from `target_titles[:6]` |
| `pipeline.py` (lines 695–770) | Passes `parsed.target_titles` and `parsed.department` at all ranking/search call sites |
| `llm_parser.py` | Added Finance + Healthcare to `_get_default_titles()`; default fallback now CEO-first |

These are **correct and will be kept**. We implement them in our codebase and add our improvements on top.

---

## Part 2: Issues the Competitor Missed

### Issue 1 — `_get_default_titles()` still appends HR to non-HR categories
**File:** `llm_parser.py` — confirmed in competitor's version

IT, Finance, Healthcare lists still end with `"HR Manager", "Personalleiter"`.
These feed directly into `find_multiple_decision_makers()` as `target_titles[:6]`,
making the first LinkedIn search query include HR for Finance jobs (5-entry list → HR at index 3).

### Issue 2 — `SYSTEM_PROMPT` still guides LLM toward HR/Personal titles
**File:** `llm_parser.py` line 64 — UNCHANGED by competitor:
```python
"- Bestimme relevante Titel für Entscheider (HR, Personal, Geschäftsführung)"
```
When the LLM generates `target_titles` from context, it's still instructed to prioritize HR.

### Issue 3 — `_is_valid_name()` rejects valid German surnames via substring matching
**Files:** `clients/job_scraper.py` lines 361–368, `clients/impressum.py` lines 287–309

Both files use `any(p in name_lower for p in patterns)` with patterns like `'leiter'`, `'ag'`, `'manager'`.
Python `in` does substring matching, not word matching:
- `'leiter' in 'leitner'` → True → "Karl Leitner" silently rejected
- `'ag' in 'haagen'` → True → "Hans Haagen" silently rejected

Both files need the same fix. The competitor touched neither.

### Issue 4 — `pipeline.py` never passes `target_titles` to `discover_team_contacts()`
**File:** `pipeline.py` lines 540–543 — UNCHANGED by competitor:
```python
team_discovery_task = discover_team_contacts(
    company_name=parsed.company_name,
    domain=company_info.domain,
    job_category=payload.category     # ← raw category only, no target_titles
)
```

### Issue 5 — `_detect_department()` covers only 5 of ~12 DACH categories
**File:** `llm_parser.py` lines 290–305

Handles: HR, IT, Sales, Marketing, Finance.
Missing: Logistik, Einkauf, Operations, Produktion, Pflege, Medizin, Recht, Consulting.
The competitor added these to `_get_default_titles()` but never updated `_detect_department()`.
For all missing categories `parsed.department = None`, so scoring has no context.

### Issue 5b — `_detect_department()` and `_get_default_titles()` have a substring matching bug for short keywords
**File:** `llm_parser.py` lines 290–305, 253–286

Both functions use `'it' in job_lower` which matches false positives:
- `'it' in 'hospitality'` → True → "Hospitality Manager" incorrectly detected as IT
- `'it' in 'digital'` → True → "Digital Marketing Manager" incorrectly detected as IT

Short tokens like `'it'` need word-boundary matching via regex `\b`.

### Issue 6 — No hard exclusion gate: HR can still win as a fallback
Scoring HR=45 is better than HR=100, but if a CTO (95) fails LinkedIn verification in Phase 5,
the next candidate could still be an HR manager. The README says Geschäftsführer/CEO should
be preferred over HR at all times. A gate enforces this at code level.

Pure score-threshold gating (e.g., drop if `score < 60`) is fragile because LLM integer
scoring can swing ±15–20 across runs, especially with Gemini Flash. A **categorical label**
from the LLM is deterministic and immune to scoring drift.

### Issue 7 — `extract_contacts_with_priority()` is dead code with wrong scoring and redundant LLM calls
**File:** `clients/ai_extractor.py` lines 304–372

This function is **never called**. `team_discovery.py` imports and calls
`extract_contacts_from_page()` (line 415), not this. The function has the wrong scoring
table (HR=100) but zero runtime impact.

Additionally, the current implementation makes **two** LLM calls: one to extract contacts
(`extract_contacts_from_page()`), then a second to re-score them. But those contacts then
flow into `validate_and_rank_candidates()` which makes a **third** LLM call to score again.
The middle scoring call is wasted. The fix is to **merge extraction + scoring into a single
LLM call**, eliminating the redundant step.

### Issue 8 — `DecisionMaker.title` is never enforced (README minimum requirement #4)
**File:** `pipeline.py` lines 1007, 1064, 1089 — all 3 `DecisionMaker()` construction sites

README states: *"The title/position of the found contact person is critical context — it
must always be included."* All 3 sites use `title=candidate_data.get("title")` which is
`None` whenever the source didn't extract a title.

Additionally, `CandidateValidation` (the dataclass returned by the validator) has **no
`title` field**. Title is silently dropped after validation and the code has to re-lookup
by name-matching from `all_candidates` — which fails on partial name matches.

### Issue 9 — `_extract_contact_title()` only captures HR and C-suite titles
**File:** `clients/job_scraper.py` lines 410–416

Patterns only match: `Personalleiter`, `HR Manager`, `Recruiter`, `Talent Acquisition`,
`Geschäftsführer`, `CEO|CTO|CFO|COO`. Missing: `IT-Leiter`, `Head of IT`,
`Vertriebsleiter`, `Head of Sales`, `Finanzleiter`, `Head of Engineering`, etc.
When an IT-Leiter is found via the job page, their title is captured as `None`.

### Issue 10 — Validator prompt has no source-trust awareness
**File:** `clients/ai_validator.py` lines 331–364

The README explicitly states: candidates from `job_url`, `llm_parse`, `team_page`,
`impressum` are **trusted** (company's own website), while `linkedin_fallback` (Google search)
is **untrusted**. The candidate dicts already carry a `source` field, but the validator prompt
never mentions source trust. A CTO from the company's own team page should score higher than
the same title found via Google. This is a free tiebreaker — zero code change, just a prompt tweak.

---

## Part 3: Gaps Neither Solution Addresses

### Gap A — Job description is never mined for "reports to" hiring manager signals
The description is already in memory (`payload.description`). No HTTP call needed.
German job postings frequently name the direct hiring manager:
- `"Sie berichten direkt an den CTO"` — confirms CTO is the target
- `"Ihre zukünftige Führungskraft: Thomas Müller"` — names the person directly
- `"Bei fachlichen Fragen: Tobias Bauer (Leiter Vertrieb)"` — name + title

This is distinct from the existing `extract_job_posting_contact()` which looks for
application contacts (HR). A separate extraction looks for who the candidate would
*report to* — a fundamentally different and higher-value signal.
Runs in the parallel Phase 2 block at zero extra latency.

### Gap B — Schema.org / JSON-LD on company websites is never parsed
The HTML is already fetched by `team_discovery.py`. A JSON parse costs nothing.
Many DACH company websites embed `<script type="application/ld+json">` with
`@type: Person`, employee arrays, or contactPoint data. Free, fast, structured.

### Gap C — Multi-source triangulation is unused
If "Thomas Müller" appears in both Impressum AND team page, that's free cross-source
corroboration. Deduplication prevents duplicates but never rewards multi-source matches.

---

## Part 4: The Fix Plan

### Fix 1 — Clean `_get_default_titles()`, fix `SYSTEM_PROMPT`, fix keyword matching (addresses Issues 1, 2, 5, 5b)
**File:** `llm_parser.py`

- Remove `"HR Manager"` and `"Personalleiter"` from all non-HR category lists
- Update `SYSTEM_PROMPT` line 64:
  ```python
  # BEFORE
  "- Bestimme relevante Titel für Entscheider (HR, Personal, Geschäftsführung)"
  # AFTER
  "- Bestimme den idealen fachlichen Entscheider für DIESE Stelle (z.B. CTO für IT, "
  "CFO für Finanzen). HR nur wenn die Stelle selbst im HR-Bereich ist."
  ```
- Extend `_detect_department()` to cover all categories present in `_get_default_titles()`:
  add Logistik, Einkauf, Produktion, Pflege, Medizin, Recht, Consulting/Beratung
- Fix word-boundary matching for short tokens in both `_detect_department()` and
  `_get_default_titles()`:
  ```python
  import re

  # BEFORE — matches 'it' inside 'hospitality', 'digital', etc.
  if any(x in job_lower for x in ['it', 'software', ...]):

  # AFTER — use \b word boundary for short tokens, substring for long ones
  if re.search(r'\b(?:it)\b', job_lower) or any(x in job_lower for x in ['software', 'developer', 'engineer', 'tech']):
  ```
  Short tokens (`'it'`) need `\b` boundaries. Longer tokens (`'software'`, `'developer'`)
  are safe with substring matching because they don't appear inside unrelated words.

---

### Fix 2 — Fix `_is_valid_name()` substring bug in both files (addresses Issue 3)
**Files:** `clients/job_scraper.py` line 361, `clients/impressum.py` lines 274 AND 287

Use a **two-pass approach**: word-set intersection for single-token patterns (the ones that
cause false positives on surnames), and the original substring check for multi-word patterns
(which can't collide with surnames).

**`job_scraper.py`** — simpler pattern set, single-pass word-set is sufficient:
```python
# BEFORE — rejects "Karl Leitner" because 'leiter' is a substring of 'leitner'
if any(p in name_lower for p in invalid_patterns):
    return False

# AFTER — only reject if pattern is a complete word in the name
words_in_name = set(name_lower.split())
legal_suffixes = {'gmbh', 'ag', 'kg', 'ug', 'mbh', 'ohg', 'gbr'}
job_title_words = {'ceo', 'cto', 'cfo', 'coo', 'leiter', 'leiterin',
                   'manager', 'direktor', 'vorstand', 'inhaber', 'chef',
                   'präsident', 'teamleiter',
                   'geschäftsführer', 'geschäftsführerin',  # full words, NOT prefix 'geschäftsführ'
                   'personal', 'recruiting', 'team', 'abteilung'}
if words_in_name & (legal_suffixes | job_title_words):
    return False
```

**Why full words, not the prefix `'geschäftsführ'`:** Word-set intersection requires exact
matches. The original code used `'geschäftsführ'` as a prefix to catch all inflections
(Geschäftsführer, Geschäftsführerin, Geschäftsführung) via substring matching. But
`set.intersection` is not substring-based — `'geschäftsführ' != 'geschäftsführer'`, so the
prefix would silently fail. We list the two common full-word forms instead.

**`impressum.py`** — has TWO separate pattern blocks that both need fixing:

**Block 1 (lines 274–283): `invalid_patterns`** — contains `'ag'`, `'kg'`, `'ug'`, `'weg'`
which cause false positives on surnames like "Haagen", "Wegner", "Lugner":
```python
# BEFORE (lines 274–283) — 'ag' in 'haagen' → True → "Hans Haagen" rejected
invalid_patterns = [
    'kontakt', 'email', 'telefon', 'adresse', 'impressum',
    'gmbh', 'ag', 'kg', 'mbh', 'ohg', 'ug',
    'straße', 'str.', 'platz', 'weg',
    '@', 'www', 'http', '.de', '.com',
    'mehr erfahren', 'weiterlesen', 'zum profil'
]
text_lower = text.lower()
if any(p in text_lower for p in invalid_patterns):
    return False

# AFTER — two-pass: word-set for short tokens that collide with surnames,
# substring for everything else (safe patterns that can't appear in names)
words_in_name = set(text_lower.split())

# Short tokens that cause false positives — must be exact word matches
short_token_invalid = {'gmbh', 'ag', 'kg', 'mbh', 'ohg', 'ug', 'weg'}
if words_in_name & short_token_invalid:
    return False

# Safe patterns: these can't appear as substrings of real German surnames
safe_substring_invalid = [
    'kontakt', 'email', 'telefon', 'adresse', 'impressum',
    'straße', 'str.', 'platz',
    '@', 'www', 'http', '.de', '.com',
    'mehr erfahren', 'weiterlesen', 'zum profil'
]
if any(p in text_lower for p in safe_substring_invalid):
    return False
```

"Hans Haagen" → words: `{'hans', 'haagen'}` → no match with `{'ag', ...}` → passes correctly.
"Max AG" → words: `{'max', 'ag'}` → `'ag'` matches → rejected correctly.
"Maria Wegner" → words: `{'maria', 'wegner'}` → no match with `{'weg', ...}` → passes correctly.

**Block 2 (lines 287–309): `job_title_patterns`** — same two-pass as before:
```python
# BEFORE (lines 287–309) — 'leiter' in 'leitner' → True → "Karl Leitner" rejected
if any(p in text_lower for p in job_title_patterns):
    return False

# AFTER — two-pass: word-set for single tokens, substring for multi-word/compound patterns
# (words_in_name already computed above in Block 1 fix)

# Pass 1: single-token patterns via word-set intersection (safe for surnames)
single_token_invalid = {
    'präsident', 'vizepräsident', 'vize',
    'teamleiter', 'abteilungsleiter', 'bereichsleiter', 'gruppenleiter',
    'vorstand', 'aufsichtsrat', 'beirat',
    'direktor', 'director',
    'manager', 'leiter', 'leiterin',
    'chef', 'chefin',
    'senior', 'junior',
    'assistent', 'assistentin',
    'mitarbeiter', 'angestellte',
    'partner', 'gesellschafter',
    'inhaber', 'inhaberin',
    'gründer', 'gründerin', 'founder',
    'ceo', 'cto', 'cfo', 'coo', 'cmo', 'cio',
    'managing', 'executive', 'officer',
    'consultant', 'berater', 'beraterin',
    'entwickler', 'developer', 'engineer',
}
if words_in_name & single_token_invalid:
    return False

# Pass 2: multi-word/compound patterns via substring (can't collide with surnames)
multi_word_invalid = [
    'head of', 'und team', 'unser team', 'das team',
    'geschäftsführ', 'geschäftsleitung',  # compound words safe for substring
    'sekretär',  # safe for substring — not a surname component
    'eigentümer',  # safe for substring — not a surname component
]
if any(p in text_lower for p in multi_word_invalid):
    return False
```

**Why two blocks need fixing:** The `'ag'` pattern that rejects "Hans Haagen" lives in the
**first** `invalid_patterns` block (line 276), NOT in the `job_title_patterns` block. If we
only fix Block 2, "Karl Leitner" is fixed but "Hans Haagen" is still silently rejected.
Both blocks must be updated.

"Karl Leitner" → words: `{'karl', 'leitner'}` → no match in either block → passes correctly.
"Max Leiter" → words: `{'max', 'leiter'}` → Block 2 catches `'leiter'` → rejected correctly.
"Hans Haagen" → words: `{'hans', 'haagen'}` → no match in either block → passes correctly.
"Head of Sales" → Block 2 Pass 2 catches `'head of'` → rejected correctly.
"Firma AG" → words: `{'firma', 'ag'}` → Block 1 catches `'ag'` → rejected correctly.

---

### Fix 3 — Rewrite + activate `extract_contacts_with_priority()` as single LLM call (addresses Issue 7)
**Files:** `clients/ai_extractor.py`, `clients/team_discovery.py`

**Step A** — In `clients/ai_extractor.py`, rewrite `extract_contacts_with_priority()` to
merge extraction and scoring into a **single LLM call** (eliminates the redundant two-step
approach where extraction and scoring were separate LLM calls):

```python
async def extract_contacts_with_priority(
    page_text: str,
    company_name: str,
    job_category: Optional[str] = None,
    target_titles: List[str] = []
) -> List[ExtractedContact]:
    """Single LLM call: extract contacts AND score by relevance."""
    if not page_text or len(page_text.strip()) < 50:
        logger.info(f"Page text too short for extraction: {len(page_text)} chars")
        return []

    # Reuse the existing smart truncation (60% start, 40% end with marker)
    text = truncate_text(page_text)

    llm = get_llm_client()

    titles_hint = f"\nGesuchte Titel: {', '.join(target_titles[:4])}" if target_titles else ""
    category_hint = f"\nStelle im Bereich: {job_category}" if job_category else ""

    prompt = f"""Extrahiere Kontaktpersonen von der Team-/Über-uns-Seite von "{company_name}".{titles_hint}{category_hint}

Für jede Person gib an:
- name: Vollständiger Name
- title: Position/Titel (falls erkennbar)
- email: E-Mail (falls vorhanden)
- phone: Telefon (falls vorhanden)
- priority: Relevanz 1-100
  - Titel entspricht gesuchtem Profil: 100
  - Geschäftsführer/CEO/Inhaber: 75
  - HR/Personal: 40
  - Sonstige: 20

Seiteninhalt:
{text}

Antworte als JSON-Array, sortiert nach priority (höchste zuerst):
[{{"name": "...", "title": "...", "email": null, "phone": null, "priority": 80}}]"""

    # Keep BALANCED tier — extracting contacts from unstructured team page HTML is harder
    # than simple scoring. The original extract_contacts_from_page() used BALANCED for this
    # reason. We already save one LLM call by merging; no need to downgrade quality too.
    result = await llm.call_json(prompt, tier=ModelTier.BALANCED)
    track_llm("contact_extract_priority", tier="sonnet")

    if not result or not isinstance(result, list):
        logger.warning(f"AI priority extraction returned invalid result: type={type(result)}")
        return []

    contacts = []
    for item in result:
        if not isinstance(item, dict):
            continue

        name = item.get("name", "").strip()
        if not name or len(name) < 3 or len(name.split()) < 2:
            continue

        contacts.append(ExtractedContact(
            name=name,
            title=item.get("title"),
            email=item.get("email"),
            phone=item.get("phone"),
            source="team",
            confidence=min(item.get("priority", 50) / 100, 1.0)
        ))
    return contacts
```

**Why BALANCED, not FAST:** The original `extract_contacts_from_page()` used
`ModelTier.BALANCED` (Sonnet-class) because extracting contacts from unstructured team page
HTML is a harder task than simple scoring. Downgrading to FAST risks missing contacts on
complex page layouts. The merged function already saves one full LLM call — the tier should
stay at BALANCED to maintain extraction quality.

**Why `truncate_text()`, not `[:8000]`:** The original function used the `truncate_text()`
helper which does smart truncation (60% from start, 40% from end, with a marker). A hard
`[:8000]` chop loses content from the end of the page — where team member bios and contact
sections often live. Reusing `truncate_text()` preserves both head and tail content.

This eliminates one LLM call per team page scrape. The downstream
`validate_and_rank_candidates()` still does the authoritative scoring, but now team page
contacts arrive pre-sorted with titles already extracted, helping the validator make better decisions.

**Step B** — In `clients/team_discovery.py`, update the import and wire up the function:
```python
# BEFORE (line at top of file)
from clients.ai_extractor import extract_contacts_from_page, ExtractedContact

# AFTER
from clients.ai_extractor import extract_contacts_with_priority, ExtractedContact

# BEFORE (line 415 in _scrape_team_page)
return await extract_contacts_from_page(text, company_name, "team")

# AFTER
return await extract_contacts_with_priority(
    text, company_name,
    job_category=self._job_category,
    target_titles=self._target_titles
)
```

`discover_and_extract()` signature gains `target_titles: List[str] = []` and stores it
as `self._target_titles` for use in `_scrape_team_page()`.

---

### Fix 4 — Pass `target_titles` to `discover_team_contacts()` (addresses Issue 4)
**File:** `pipeline.py` lines 540–543, `clients/team_discovery.py` lines 519–523

```python
# pipeline.py — BEFORE
team_discovery_task = discover_team_contacts(
    company_name=parsed.company_name,
    domain=company_info.domain,
    job_category=payload.category
)

# AFTER
team_discovery_task = discover_team_contacts(
    company_name=parsed.company_name,
    domain=company_info.domain,
    job_category=parsed.department or payload.category,
    target_titles=parsed.target_titles   # NEW
)
```

Add `target_titles: List[str] = []` to the `discover_team_contacts()` convenience
function signature and thread it through to `TeamDiscovery.discover_and_extract()`.

**Note:** Fix 3 and Fix 4 are one end-to-end chain. Both must be implemented together:
`pipeline.py` → `discover_team_contacts()` → `discover_and_extract()` → `_scrape_team_page()` → `extract_contacts_with_priority()`.

---

### Fix 5 — Hybrid exclusion gate: categorical label + score floor (addresses Issue 6)
**Files:** `clients/ai_validator.py`, `pipeline.py`

Pure score-threshold gating is fragile because LLM scoring varies ±15–20 across runs.
Instead, add a **categorical `role_category`** field to the LLM response (deterministic
classification) and use the score as a safety floor only.

**Step A** — Amend the validator prompt in `ai_validator.py` to add two fields:
```
5. role_category: Kategorisiere die Rolle
   - "department_head": Titel passt zur gesuchten Abteilung (CTO bei IT, CFO bei Finanzen)
   - "executive": Geschäftsführer/CEO/Inhaber (nicht abteilungsspezifisch)
   - "hr": HR/Personal/Recruiting
   - "other": Sonstige gültige Kontakte
   - "invalid": Ungültiger Kandidat

6. inferred_title: Position/Titel des Kandidaten (z.B. "CTO", "IT-Leiter", "Geschäftsführer") oder null
   - Nur der Titel selbst, KEINE Erklärung oder Kontext
```

And update the JSON response schema to include both new fields:
```json
{
    "name": "...",
    "name_valid": true,
    "name_reason": "...",
    "email": "...",
    "email_valid": true,
    "email_reason": "...",
    "overall_valid": true,
    "relevance_score": 95,
    "role_category": "department_head",
    "inferred_title": "CTO",
    "validation_notes": "..."
}
```

**Step B** — Add `role_category` and `inferred_title` to `CandidateValidation` dataclass:
```python
@dataclass
class CandidateValidation:
    name: str
    name_valid: bool
    name_reason: str
    title: Optional[str]            # NEW (Fix 6) — carry title through validation
    role_category: str              # NEW — "department_head"|"executive"|"hr"|"other"|"invalid"
    inferred_title: Optional[str]   # NEW — clean title string from LLM (e.g. "CTO", not a sentence)
    email: Optional[str]
    email_valid: bool
    email_reason: str
    overall_valid: bool
    relevance_score: int
    validation_notes: str
```

When constructing `CandidateValidation` from LLM response:
```python
validated.append(CandidateValidation(
    ...
    role_category=item.get("role_category", "other"),      # NEW
    inferred_title=item.get("inferred_title"),              # NEW
    ...
))
```

Fallback construction (when LLM fails) defaults to `role_category="other"` and
`inferred_title=None`.

**Step C** — Gate logic in `pipeline.py`, after **both** calls to `validate_and_rank_candidates()`:
```python
has_dept_head = any(
    c.role_category == "department_head" and c.relevance_score >= 70
    for c in validated_candidates
)
if has_dept_head:
    validated_candidates = [
        c for c in validated_candidates
        if c.role_category != "hr"
    ]
```

The categorical label handles the gating decision (deterministic), and the `>= 70` score
floor prevents a false-positive dept-head classification from triggering the gate. This is
robust to ±20 points of LLM scoring variance.

---

### Fix 6 — Add `title` to `CandidateValidation` and enforce `DecisionMaker.title` (addresses Issues 8, 9)
**Files:** `clients/ai_validator.py`, `clients/job_scraper.py`, `pipeline.py`

**Step A** — Add `title` to `CandidateValidation` dataclass in `ai_validator.py`
(shown combined with `role_category` in Fix 5 Step B above).

Inside `validate_and_rank_candidates()`, after parsing the LLM JSON response, look up
the title from the original input candidates by name and store it:
```python
# Build a name→title lookup from the input before calling LLM
title_lookup = {
    c.get("name", "").strip().lower(): c.get("title")
    for c in filtered_candidates
}

# When constructing CandidateValidation objects:
validated.append(CandidateValidation(
    name=item.get("name", ""),
    title=title_lookup.get(item.get("name", "").strip().lower()),  # NEW — from source
    role_category=item.get("role_category", "other"),              # NEW (Fix 5)
    inferred_title=item.get("inferred_title"),                     # NEW (Fix 5) — clean LLM title
    ...
))
```

**Step B** — Enforce `DecisionMaker.title` at all 3 construction sites in `pipeline.py`
using a small helper. The fallback chain uses `inferred_title` from the LLM (a clean title
string like "CTO"), then `role_category` for honest category labels:
```python
def _resolve_title(
    candidate_title: Optional[str],
    inferred_title: Optional[str],
    role_category: str
) -> Optional[str]:
    """Resolve a display title for the decision maker. Never fabricates a specific title."""
    # 1. Use the actual title from the source (job page, team page, etc.)
    if candidate_title:
        return candidate_title
    # 2. Use the LLM's inferred title — a clean string like "CTO" or "IT-Leiter"
    if inferred_title:
        return inferred_title
    # 3. Honest category label — useful context without guessing a specific title
    category_labels = {
        "department_head": "Fachbereichsleitung",
        "executive": "Geschäftsführung",
        "hr": "HR / Personal",
    }
    return category_labels.get(role_category)

decision_maker = DecisionMaker(
    name=candidate.name,
    title=_resolve_title(candidate.title, candidate.inferred_title, candidate.role_category),
    ...
)
```

**Why `inferred_title` instead of parsing `validation_notes`:** The previous approach tried
to extract a title from `validation_notes` via substring matching (e.g., check if "cto" appears
in the notes). But `validation_notes` is a "Kurze Zusammenfassung" — a full sentence like
`"CTO bei SAP, zuständig für Cloud-Infrastruktur"`. Returning that as a title is messy. By
having the LLM return a separate `inferred_title` field (just the clean title string), we get
`"CTO"` directly — no parsing needed, no risk of returning a sentence as a title.

This gives the sales team useful context ("CTO", "Fachbereichsleitung", "Geschäftsführung")
without assigning a potentially wrong specific title or returning a sentence fragment.

**Step C** — Expand `_extract_contact_title()` patterns in `job_scraper.py`:
```python
title_patterns = [
    r'(CEO|CTO|CFO|COO|CMO|CIO)',
    r'(Geschäftsführer(?:in)?)',
    r'(Inhaber(?:in)?)',
    r'(IT[\s-]Leiter(?:in)?)',
    r'(Head\s+of\s+(?:IT|Engineering|Tech(?:nology)?))',
    r'(Leiter(?:in)?\s+(?:IT|Software|Technik|Entwicklung))',
    r'(VP\s+Engineering)',
    r'(Vertriebsleiter(?:in)?)',
    r'(Head\s+of\s+Sales)',
    r'(Sales\s+Director)',
    r'(Leiter(?:in)?\s+Vertrieb)',
    r'(Finanzleiter(?:in)?)',
    r'(Head\s+of\s+Finance)',
    r'(Kaufmännische(?:r)?\s+Leiter(?:in)?)',
    r'(Personalleiter(?:in)?)',
    r'(HR[\s-]Manager(?:in)?)',
    r'(Head\s+of\s+HR)',
    r'(Recruiter(?:in)?)',
    r'(Abteilungsleiter(?:in)?)',
    r'(Bereichsleiter(?:in)?)',
]
```

---

### Fix 7 — Mine job description for hiring manager signals (addresses Gap A)
**File:** New function `extract_hiring_manager_from_description()` in `clients/ai_extractor.py`

```python
async def extract_hiring_manager_from_description(
    description: str,
    company_name: str,
    job_title: str
) -> Optional[ExtractedContact]:
    """
    Find hiring manager signals in job posting body.
    Distinct from extract_job_posting_contact() which finds the HR application contact.
    Patterns: "berichten an", "Führungskraft", "fachliche Fragen", "Team leitet".
    """
```

Add as a 4th parallel task in `pipeline.py` Phase 2. Reads `payload.description` which
is already in memory — zero extra HTTP calls, zero latency overhead:

```python
hiring_manager_task = extract_hiring_manager_from_description(
    payload.description, parsed.company_name, payload.title
)

job_contact, impressum_result, team_result, hiring_manager = await asyncio.gather(
    job_contact_task, impressum_task, team_discovery_task, hiring_manager_task,
    return_exceptions=True
)
```

If found, add as the first entry in `all_candidates` with `source="description_hiring_manager"`
and the highest `priority` value — the company named this person directly.

---

### Fix 8 — Add source-trust signaling to the validator prompt (addresses Issue 10)
**File:** `clients/ai_validator.py`

Zero-code-change improvement: the candidate dicts already carry a `source` field. Add
source trust guidance to the validator prompt:

```
Vertrauenswürdigkeit der Quelle beachten:
- job_url, impressum, team_page, description_hiring_manager: vertrauenswürdig (Firmenwebsite/Stellenanzeige)
- linkedin_fallback: ungeprüft (Google-Suche) — bei gleichem Titel bevorzuge die vertrauenswürdigere Quelle
```

This gives the LLM a tiebreaking signal that aligns with the README's trust model.
A CTO found on the company's own team page should score higher than the same title
found via Google search.

---

### Fix 9 — Add Schema.org/JSON-LD extraction (addresses Gap B) — Bonus
**File:** `clients/team_discovery.py`

Add `_extract_json_ld_contacts()` at the start of `_scrape_team_page()`, before the AI
extraction. No LLM, no HTTP — pure JSON parse of HTML already fetched:

```python
def _extract_json_ld_contacts(self, html: str) -> List[ExtractedContact]:
    soup = BeautifulSoup(html, 'lxml')
    contacts = []
    for tag in soup.find_all('script', type='application/ld+json'):
        try:
            data = json.loads(tag.string or '')
            # Handle @type: Person — extract name, jobTitle, email, telephone
            # Handle @type: Organization — extract employee[] arrays
            # Handle contactPoint — extract name and role
        except (json.JSONDecodeError, AttributeError):
            continue
    return contacts
```

If results found, prepend to the contact list — structured data is more reliable than AI extraction.

---

### Fix 10 — Multi-source triangulation (addresses Gap C) — Bonus
**File:** `pipeline.py`, before calling `validate_and_rank_candidates()`

Merge duplicate names across sources and annotate with `source_count`:
```python
from collections import Counter

name_sources = Counter()
for candidate in all_candidates:
    name_key = candidate.get('name', '').strip().lower()
    name_sources[name_key] += 1

for candidate in all_candidates:
    name_key = candidate.get('name', '').strip().lower()
    count = name_sources[name_key]
    if count > 1:
        candidate['source_count'] = count
        candidate['multi_source_verified'] = True
```

Pass in validator prompt as a tiebreaker hint:
```
- Kandidaten die aus mehreren Quellen stammen (multi_source_verified=true) erhalten +10 Bonuspunkte
```

---

## Part 5: Prioritized Change Table

| Priority | File(s) | Fix | Addresses |
|----------|---------|-----|-----------|
| **P0** | `llm_parser.py` | Remove HR from non-HR `_get_default_titles()`; fix `SYSTEM_PROMPT`; extend `_detect_department()`; fix `\b` word boundary for `'it'` | Issues 1, 2, 5, 5b |
| **P0** | `job_scraper.py`, `impressum.py` | Fix `_is_valid_name()` two-pass word-boundary bug in both files | Issue 3 |
| **P0** | `ai_validator.py`, `pipeline.py` | Add `title` + `role_category` + `inferred_title` to `CandidateValidation`; enforce `DecisionMaker.title` with honest labels | Issues 6, 8 |
| **P1** | `ai_extractor.py`, `team_discovery.py` | Rewrite `extract_contacts_with_priority()` as single LLM call; update import | Issue 7 |
| **P1** | `pipeline.py`, `team_discovery.py` | Pass `target_titles` + `parsed.department` to `discover_team_contacts()` | Issue 4 |
| **P1** | `pipeline.py` | Hybrid exclusion gate (categorical label + score floor) after both validator calls | Issue 6 |
| **P1** | `job_scraper.py` | Expand `_extract_contact_title()` patterns to cover dept head titles | Issue 9 |
| **P1** | `ai_extractor.py`, `pipeline.py` | Add `extract_hiring_manager_from_description()` as Phase 2 parallel task | Gap A |
| **P1** | `ai_validator.py` | Add source-trust guidance to validator prompt | Issue 10 |
| **P2** | `team_discovery.py` | Add `_extract_json_ld_contacts()` before AI extraction | Gap B |
| **P2** | `pipeline.py` | Multi-source triangulation bonus | Gap C |
| Keep | `ai_validator.py` | Scoring already correct (competitor's fix) | — |
| Keep | `linkedin_search.py` | Search order + dynamic query already correct (competitor's fix) | — |
| Keep | `pipeline.py` (695–770) | `target_titles` + `department` already passed (competitor's fix) | — |

---

## Part 6: Files Touched and What Changes in Each

| File | Changes |
|------|---------|
| `llm_parser.py` | `SYSTEM_PROMPT` line 64; `_get_default_titles()` remove HR from non-HR + add `\b` for `'it'`; `_detect_department()` add 8 categories + `\b` for `'it'` |
| `clients/job_scraper.py` | `_is_valid_name()` word-set fix (single-pass); `_extract_contact_title()` expanded patterns |
| `clients/impressum.py` | `_is_valid_name()` two-pass fix in BOTH pattern blocks: Block 1 `invalid_patterns` (lines 274–283: `'ag'`/`'weg'`/`'ug'` → word-set) and Block 2 `job_title_patterns` (lines 287–309: `'leiter'`/`'manager'` → word-set) |
| `clients/ai_validator.py` | Add `title` + `role_category` + `inferred_title` to `CandidateValidation`; add `role_category` + `inferred_title` to LLM prompt schema; add source-trust guidance to prompt; thread title through via name→title lookup |
| `clients/ai_extractor.py` | Rewrite `extract_contacts_with_priority()` as single merged LLM call; add new `extract_hiring_manager_from_description()` |
| `clients/team_discovery.py` | Update import to `extract_contacts_with_priority`; add `target_titles` to `discover_and_extract()` + `discover_team_contacts()`; wire up new function; add `_extract_json_ld_contacts()` (bonus) |
| `pipeline.py` | Pass `target_titles` + `parsed.department` to `discover_team_contacts()`; add hybrid exclusion gate after both validator calls; add `_resolve_title()` helper (uses `inferred_title` → category labels fallback); enforce `DecisionMaker.title` at 3 construction sites; add hiring manager task to Phase 2 parallel block; add multi-source annotation (bonus) |
| `clients/ai_validator.py` (scoring) | Already fixed by competitor — no change |
| `clients/linkedin_search.py` | Already fixed by competitor — no change |

---

## Part 7: One-Sentence Production Impact Per Fix

| Fix | What changes in production |
|-----|---------------------------|
| Clean `_get_default_titles()` + `SYSTEM_PROMPT` + `\b` | LinkedIn's first search no longer queries for HR on IT/Finance jobs; "Hospitality Manager" no longer misclassified as IT |
| Fix `_is_valid_name()` in 2 files (two-pass) | Surnames like "Leitner" / "Haagen" no longer silently discarded; multi-word patterns like "Head of" still correctly rejected |
| `DecisionMaker.title` enforcement (`inferred_title` + honest labels) | `decision_maker.title` is never `None`; LLM infers clean title first, falls back to category labels — no sentence fragments |
| Rewrite `extract_contacts_with_priority()` (single LLM call) | Team page contacts extracted and pre-ranked in one LLM call instead of two — lower latency, lower cost |
| Pass `target_titles` to team discovery | Team scraper looks for specific titles, not just any names |
| Extend `_detect_department()` | Logistik/Pflege/Recht jobs have department context, not `None` |
| Hybrid exclusion gate (categorical + score) | CEO/Geschäftsführer becomes the fallback when dept head fails — never HR; robust to LLM scoring variance |
| Expand `_extract_contact_title()` | IT-Leiter / Vertriebsleiter title captured when found on job pages |
| Source-trust in validator prompt | CTO from company team page scores higher than same title from Google search |
| Mine description for "reports to" | Direct hiring manager names from job text become highest-trust candidates |
| Schema.org/JSON-LD (bonus) | Free structured data parsed before LLM when available |
| Multi-source triangulation (bonus) | Candidates appearing in multiple sources get a scoring bonus |

---

## Part 8: Test Strategy

### Unit Tests — Add to `test_dm_discovery.py`

| Test Case | Input | Expected Result |
|-----------|-------|-----------------|
| IT job prioritizes dept head | Job title: "Senior Software Engineer" | CTO/IT-Leiter ranks above HR |
| Finance job prioritizes CFO | Job title: "Buchhalter" | CFO/Finanzleiter ranks above HR |
| Name fix: valid surname (leiter) | Candidate: "Karl Leitner" | Passes `_is_valid_name()` in both files |
| Name fix: valid surname (ag) | Candidate: "Hans Haagen" | Passes `_is_valid_name()` in impressum (Block 1 fix) |
| Name fix: valid surname (weg) | Candidate: "Maria Wegner" | Passes `_is_valid_name()` in impressum (Block 1 fix) |
| Name fix: actual title | Candidate: "Max Leiter" | Rejected by `_is_valid_name()` |
| Name fix: actual legal suffix | Candidate: "Firma AG" | Rejected by `_is_valid_name()` (impressum Block 1) |
| Name fix: multi-word pattern | Candidate: "Head of Sales" | Rejected by `_is_valid_name()` (impressum Block 2) |
| Hiring manager extraction | Description: "Sie berichten an den CTO Thomas Müller" | Extracts "Thomas Müller" with title "CTO" |
| HR-only fallback | All candidates are HR, no dept head | HR still selected (gate does not fire) |
| Default category fallback | Unknown job category | CEO/Geschäftsführer ranked before HR |
| Word boundary: `'it'` | Job title: "Hospitality Manager" | NOT detected as IT department |
| Title enforcement: inferred | Candidate with no source title, `inferred_title="CTO"` | `DecisionMaker.title` = "CTO" |
| Title enforcement: fallback | Candidate with no title, no `inferred_title`, `role_category="department_head"` | `DecisionMaker.title` = "Fachbereichsleitung" |
| Source trust tiebreaker | Same title from team_page vs linkedin_fallback | Team page candidate scores higher |

### Integration Test — `test_full_flow.py`

Run end-to-end with 3 representative inputs:
1. **IT job** — "Senior Software Developer" at a mid-size company → expect CTO or IT-Leiter
2. **Finance job** — "Buchhalter" at a mid-size company → expect CFO or Finanzleiter
3. **Generic job** — Unknown category → expect CEO/Geschäftsführer, not HR

Verify for each:
- `decision_maker.title` is not `None`
- `decision_maker.name` is a real person name (not a menu item or placeholder)
- HR is not selected when a department head or CEO exists

---

## Part 9: Implementation Order

Execute in this order to minimize broken intermediate states:

1. **Fix 1** — `llm_parser.py` changes (self-contained, no dependencies)
2. **Fix 2** — `_is_valid_name()` in both files (self-contained)
3. **Fix 5 Step A+B + Fix 6 Step A + Fix 8** — All `ai_validator.py` changes in one pass:
   add `title`, `role_category`, `inferred_title` to `CandidateValidation` dataclass;
   amend LLM prompt with `role_category` + `inferred_title` schema + source-trust guidance;
   thread title through via name→title lookup. *(These are combined because they all modify
   the same file and dataclass — doing them separately creates merge conflicts.)*
4. **Fix 6 Step C** — Expand `_extract_contact_title()` in `job_scraper.py`
5. **Fix 3** — Rewrite `extract_contacts_with_priority()` in `ai_extractor.py`
6. **Fix 4** — Pass `target_titles` through pipeline → team_discovery → extractor (requires Fix 3)
7. **Fix 5 Step C** — Exclusion gate in `pipeline.py` (requires step 3)
8. **Fix 6 Step B** — `_resolve_title()` + enforce title at 3 DM sites (requires step 3)
9. **Fix 7** — Hiring manager extraction + Phase 2 parallel task
10. **Fix 9** — JSON-LD extraction (bonus)
11. **Fix 10** — Multi-source triangulation (bonus)
12. **Tests** — Add test cases from Part 8
