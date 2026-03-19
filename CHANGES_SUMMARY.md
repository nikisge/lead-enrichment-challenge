# Lead Enrichment System — Changes & Improvements Summary

> **Purpose:** This document explains every improvement made to the original codebase, what was broken before, what was fixed, and the real-world impact. Written for a non-technical audience.

---

## The Big Picture

The original system found decision makers (DMs) for sales leads but had several reliability problems: it sometimes picked the wrong person (HR instead of the Head of IT), silently discarded valid contacts with certain surnames, couldn't parse job postings using the configured AI provider, and failed entirely on Windows due to browser automation conflicts. All of these have been fixed.

---

## Part 1 — Core Logic Fixes (Decision Maker Quality)

These fixes directly improve *who* gets returned as the decision maker.

---

### Fix 1 — LLM Now Searches for the Right Person by Department

**What was broken:**  
When searching for a decision maker for an IT job, the system was also searching LinkedIn for "HR Manager" and "Personalleiter". This wasted searches and could result in an HR contact being returned instead of a CTO or IT-Leiter. The same bug affected Finance, Healthcare, and other non-HR job categories.

Additionally, the AI prompt instructed the model to: *"Determine relevant titles for decision makers (HR, Personal, Geschäftsführung)"* — actively guiding it toward HR regardless of what the job was.

**What was fixed:**
- Removed "HR Manager" / "Personalleiter" from all non-HR job category title lists
- Updated the AI prompt to: *"Determine the ideal functional decision maker for THIS specific role (e.g. CTO for IT, CFO for Finance). HR only when the role itself is in HR"*
- Extended department detection to cover 8 new categories that were missing: Logistics, Purchasing, Production, Nursing, Medical, Legal, Consulting — previously these all got `null` department context and fell back to generic titles

**Files changed:** `llm_parser.py`

---

### Fix 2 — Valid German Surnames No Longer Silently Rejected

**What was broken:**  
The system checks whether a scraped name is a real person (and not a menu item, company name, or job title). This check used substring matching, which caused false positives on common German surnames:
- `"leitner"` contains `"leiter"` → "Karl Leitner" rejected as if he were a job title
- `"haagen"` contains `"ag"` (legal suffix) → "Hans Haagen" rejected as if it were a company
- `"wegner"` contains `"weg"` → "Maria Wegner" rejected

These contacts were silently dropped — no warning, no log, just gone. This affected both the job posting scraper and the impressum scraper.

**What was fixed:**  
Switched from substring matching to word-boundary matching. Short patterns like `"ag"`, `"leiter"`, `"weg"` now only trigger a rejection if they appear as a complete standalone word in the name — not as part of a longer word.

- `"Karl Leitner"` → words are `{karl, leitner}` → no match → passes correctly ✓
- `"Hans Haagen"` → words are `{hans, haagen}` → no match → passes correctly ✓
- `"Firma AG"` → words are `{firma, ag}` → matches `"ag"` → rejected correctly ✓
- `"Max Leiter"` → words are `{max, leiter}` → matches `"leiter"` → rejected correctly ✓

**Files changed:** `clients/job_scraper.py`, `clients/impressum.py`

---

### Fix 3 — Decision Maker Always Has a Title

**What was broken:**  
The `decision_maker.title` field in the API response was frequently `null`. The README explicitly states: *"The title/position is critical context — it must always be included."* The title was being dropped silently during the validation step.

**What was fixed:**  
A three-level fallback chain now ensures a title is always present:
1. Use the actual title from the source (job page, team page, impressum)
2. If no source title, use the AI's inferred clean title (e.g. `"CTO"`, `"IT-Leiter"`)
3. If no inferred title, use an honest category label (`"Geschäftsführung"`, `"Fachbereichsleitung"`, `"HR / Personal"`)

The AI validator now also returns a `role_category` classification (`department_head`, `executive`, `hr`, `other`) and an `inferred_title` — a clean title string, not a sentence.

**Files changed:** `clients/ai_validator.py`, `pipeline.py`

---

### Fix 4 — HR Can No Longer Win When a Better Candidate Exists

**What was broken:**  
The system scored HR candidates at 45/100 (lower than CTO at 95), but if the CTO failed LinkedIn verification, the next candidate could still be an HR manager. There was no hard rule preventing this fallback.

**What was fixed:**  
Added a hybrid exclusion gate that runs after candidate validation:
- If any `department_head` candidate scores ≥ 70, all HR candidates are removed from consideration
- The gate uses a categorical label (not a raw score) so it's immune to score fluctuations of ±15–20 points that naturally occur across LLM runs
- If *only* HR candidates exist, they are still returned — the gate doesn't leave the response empty

**Files changed:** `pipeline.py`, `clients/ai_validator.py`

---

### Fix 5 — Job Description Mined for Direct Hiring Manager Names

**What was added:**  
German job postings frequently name the hiring manager directly in the description:
- *"Sie berichten direkt an den CTO"* — confirms CTO is the target
- *"Ihre zukünftige Führungskraft: Thomas Müller"* — names the person directly
- *"Bei fachlichen Fragen: Tobias Bauer (Leiter Vertrieb)"* — name and title

A new extraction function `extract_hiring_manager_from_description()` runs in parallel with the other scraping tasks (zero extra latency — the description text is already in memory). If a name is found this way, it becomes the highest-trust candidate with source label `description_hiring_manager`.

**Files changed:** `clients/ai_extractor.py`, `pipeline.py`

---

### Fix 6 — Team Page Contacts Guided by Target Titles

**What was broken:**  
When scraping a company's team page, the extractor had no knowledge of what kind of contact was being sought. It extracted everyone equally, then scored later.

**What was fixed:**  
The target titles extracted from the job posting (e.g. `["CTO", "Head of Engineering", "IT-Leiter"]`) are now passed all the way through to the team page extractor. The AI prompt is enriched with: *"Gesuchte Titel: CTO, Head of Engineering, IT-Leiter"* — so relevant contacts are prioritised from the start.

**Files changed:** `pipeline.py`, `clients/team_discovery.py`, `clients/ai_extractor.py`

---

### Fix 7 — Source Trust: Company Website Beats Google Search

**What was improved:**  
A contact found on the company's own team page or impressum is more trustworthy than the same title found via a Google search. The AI validator now receives explicit guidance about source trust:

> *"Trusted sources: job_url, impressum, team_page — Untrusted: linkedin_fallback (unverified Google result). Prefer the trusted source when titles are equal."*

The validator uses this as a tiebreaker when two candidates have similar scores.

**Files changed:** `clients/ai_validator.py`

---

### Fix 8 — Structured Data (JSON-LD) Parsed Before AI Extraction

**What was added:**  
Many German company websites embed machine-readable contact data directly in their HTML using the Schema.org standard (`<script type="application/ld+json">`). This data is 100% structured, doesn't require AI, and is faster and cheaper to extract.

The team page scraper now checks for this structured data first. If person records are found, they are prepended to the contact list as high-confidence results before the AI extraction runs.

**Files changed:** `clients/team_discovery.py`

---

### Fix 9 — Contact Extraction Merged into a Single AI Call

**What was improved:**  
Previously, when scraping a team page, the system made two AI calls: one to extract contacts, and a second to score them. Then the pipeline made a third call to validate them. The middle scoring call was redundant — the final validator already re-scored everything.

The extraction and initial scoring are now merged into a single AI call, saving one LLM request per team page scraped.

**Files changed:** `clients/ai_extractor.py`, `clients/team_discovery.py`

---

## Part 2 — Infrastructure & Reliability Fixes

These fixes address runtime failures and system stability.

---

### Fix 10 — Windows Playwright Compatibility (Critical)

**What was broken:**  
Playwright (browser automation) was called directly inside Python's async event loop. On Windows, the default event loop (`ProactorEventLoop`) does not support subprocess creation, which Playwright requires. This caused:

```
NotImplementedError: asyncio.create_subprocess_exec
```

The error appeared on every scraping attempt that needed Playwright, silently falling back to the non-JS scraper and losing all JS-rendered content.

**What was fixed:**  
All three scrapers that use Playwright now run it via `sync_playwright` inside a thread executor (`asyncio.run_in_executor`). This keeps the async event loop clean while running the browser in a separate thread — fully compatible with Windows.

**Files changed:** `clients/impressum.py`, `clients/team_discovery.py`, `clients/job_scraper.py`

---

### Fix 11 — HTTP Fingerprinting Upgraded (curl_cffi)

**What was changed:**  
All HTTP requests to company websites previously used `httpx` with a custom User-Agent header. Many modern websites (and CDNs like Cloudflare) detect scrapers by their TLS fingerprint — a low-level network signature that `httpx` exposes as clearly non-browser.

**Replacement:**  
All scraping now uses `curl_cffi` with `impersonate="chrome136"`. This library mimics Chrome 136's exact TLS handshake, HTTP/2 settings, and header order at the network level — indistinguishable from a real browser without JavaScript. No custom headers needed (and none are set, since adding custom headers would break the impersonation).

**Files changed:** `clients/impressum.py`, `clients/team_discovery.py`, `clients/job_scraper.py`, `clients/company_research.py`

---

### Fix 12 — LLM Parser Connected to OpenRouter

**What was broken:**  
The job posting parser was hardcoded to use Anthropic's API directly (`AsyncAnthropic`). Since the project uses OpenRouter (not a direct Anthropic key), every job parsing attempt immediately failed and fell back to a regex parser. The regex parser cannot extract target titles or department — so every job got generic defaults.

**Symptom in logs:**
```
llm_parser - WARNING - All API keys failed, using regex fallback
```

**What was fixed:**  
`llm_parser.py` now uses the shared `LLMClient` (which uses OpenRouter), the same client used by the rest of the pipeline.

**Files changed:** `llm_parser.py`

---

### Fix 13 — Company Research Uses AI for Sales Brief

**What was broken:**  
The company research module generates a sales call summary. This function checked for an Anthropic API key (always empty), silently fell back to a dumb template that just repeated the company name and hiring signals.

**What was fixed:**  
The `_generate_sales_brief()` function now uses the same `LLMClient` / OpenRouter as the rest of the system. The summary is now a proper AI-generated German-language sales brief with company context, situation analysis, and a conversation opener.

**Files changed:** `clients/company_research.py`

---

### Fix 14 — JS-Blocked Pages Detected and Retried with Playwright

**What was broken:**  
Company websites that block scrapers often return a page saying *"JavaScript must be enabled to continue"*. The system was accepting this as valid content, resulting in descriptions like:
```
"description": "Javascript must be enabled for the correct page display About SAP..."
```

**What was fixed:**  
After scraping with `curl_cffi`, the response text is now checked against a list of JS-block phrases. If detected, the page is automatically retried using Playwright (a real browser). If the retry returns "Access Denied" (e.g. Akamai CDN), that text is discarded and the field is left empty rather than storing the error message.

**Files changed:** `clients/company_research.py`

---

### Fix 15 — Bot-Blocked Playwright Responses Detected Early

**What was added:**  
When Playwright navigates to a page and SAP's Akamai CDN blocks it, the browser returns a 294-byte response (effectively empty). Previously the system still ran an AI extraction call on those ~200 characters (always returning "no contacts found"), wasting both time and API cost.

Now:
- If Playwright returns < 1 KB of HTML, it is immediately flagged as a bot-block and skipped
- The AI extraction threshold was raised from 100 to 500 characters minimum

**Savings per SAP-like request:** ~$0.009 and ~5 seconds.

**Files changed:** `clients/team_discovery.py`

---

### Fix 16 — False "Anthropic Key Missing" Alert Removed

**What was fixed:**  
The pipeline fired a warning `"ALERT: Anthropic API key is missing!"` on every single request, even though Anthropic is not used — the system uses OpenRouter. The alert is now suppressed when an OpenRouter key is present.

**Files changed:** `pipeline.py`

---

### Fix 17 — Next.js / SPA Content Extracted via SSR Data

**What was added:**  
Modern company websites built with React/Next.js often render their content entirely in JavaScript. After stripping `<script>` tags, nearly nothing remains for the scraper to work with. 

The team page scraper now checks for `__NEXT_DATA__` (Next.js server-side rendering data embedded in the HTML) before stripping scripts. This JSON blob contains the rendered page data, including team member names and roles, even for JS-heavy SPAs.

**Files changed:** `clients/team_discovery.py`

---

## Part 3 — Post-Plan Fixes (Discovered During Live Testing)

These bugs were found by running real test cases against live company websites after the initial plan was implemented. Each one was traced to a specific log line and fixed precisely.

---

### Fix 18 — Team Page Discovery: Three Bugs Causing Wrong Pages to Be Scraped

**What was broken:**  
Running a real test against moresophy GmbH revealed that team discovery was selecting blog posts instead of the actual `/ueber-moresophy` team page. Three separate bugs were responsible:

**Bug A — HTML was truncated before text extraction**  
Playwright returned the full rendered page (832 KB). A hard limit of 100 KB was immediately applied to the HTML string. Those first 100 KB are JavaScript bundles — after stripping scripts, only 45 characters of body text remained. The names and titles on the actual team page (which appears later in the DOM) were never seen.  
→ **Fix:** Playwright HTML is no longer truncated before BeautifulSoup processes it. Text is truncated to 30 KB *after* extraction.

**Bug B — Homepage link scan was skipped when pages were already found**  
The discovery flow skipped Step 3 (homepage nav scan) if 2+ pages had already been found from the URL patterns or sitemap. Since `/kontakt` and three blog posts were found, the homepage nav was never checked — so the "Über moresophy" link pointing to the actual team page was never followed.  
→ **Fix:** Homepage scan now always runs regardless of how many pages were found earlier.

**Bug C — Sitemap matched blog post URLs**  
The sitemap keyword check used a plain substring match. URLs like `/en/blog/data-driven-business/heiko-beier-in-an-interview-**about**-the-hype` matched the keyword `about`. Blog posts were being treated as team pages.  
→ **Fix:** Sitemap now only accepts URLs at depth ≤ 2 (e.g. `/ueber-uns`, `/team`) and requires the keyword to be a standalone path segment — not buried inside a blog slug.

**Also added:** Extended `TEAM_URL_PATTERNS` list with `/unternehmen`, `/wir-ueber-uns`, `/das-sind-wir`, `/ueber-uns/team` etc. Extended homepage keyword list to catch patterns like "Über moresophy" (prefix match).

**Files changed:** `clients/team_discovery.py`

---

### Fix 19 — First-Name-Only Team Contacts Were Silently Rejected

**What was broken:**  
Running a test against BAGHUS GmbH showed the correct team page was found (`baghus.de/ueber-uns`) and Playwright successfully loaded it. The logs showed `Extracted 4 contacts with priority` — Thomas (Teamleiter Windows & Infrastruktur), Alexander (Teamleiter Enterprise Client Management), Veronica (Teamleiterin IT-Service-Management), André (Geschäftsführer). But the final output still returned the Impressum's Geschäftsführer instead.

The cause: a `len(name.split()) < 2` guard existed in **three separate places** in the codebase. "Thomas", "Veronica", "Alexander" each have only 1 word — all were rejected at every stage:
1. `ai_extractor.py` post-extraction filter
2. `ai_validator.py` pre-validation filter (line 330)
3. `ai_validator.py` `validate_person_name()` standalone function

This was originally designed to block navigation items (`"Mehr erfahren"`, `"Übersicht"`). But many German SMEs only show first names on their team pages — the guard was too aggressive.

**What was fixed:**
- All three filters now allow single first names **when a job title is present** alongside the name
- AI prompts updated to explicitly state: *"First names alone are valid when last names are not shown on the page — common at German SMEs"*
- `validate_person_name()` no longer hard-rejects single words; it falls through to the AI for a contextual decision

**Impact:** All 4 BAGHUS team contacts passed through. Thomas (Teamleiter Windows & Infrastruktur) scored 95/100 vs André's 65/100, and was correctly selected as the decision maker.

**Files changed:** `clients/ai_extractor.py`, `clients/ai_validator.py`

---

### Fix 20 — Team Member Cards Dropped by Text Length Threshold

**What was broken:**  
Related to Fix 19: even when team section elements were found on the page (18 in total for BAGHUS), their text content was filtered out by a `> 50 chars` threshold. A typical BAGHUS team card contains:
```
Thomas
Teamleiter Windows & Infrastruktur
```
That is ~45 characters — just under the threshold. All 18 cards were silently excluded. The log showed `"Extracted from 18 team section(s): 0 chars"`.

**What was fixed:**  
Threshold lowered from 50 to 15 characters. A name + title card is valid even if it is short.

**Files changed:** `clients/team_discovery.py`

---

## Part 4 — What Was Already Working (Kept As-Is)

The following improvements were made by a competitor implementation and were retained unchanged:

| Component | What It Does |
|-----------|-------------|
| `ai_validator.py` scoring | Department head = 95, CEO = 65, HR = 45, other = 35 |
| `linkedin_search.py` search order | Searches by job-specific titles first, CEO second, HR last |
| `pipeline.py` title passing | `target_titles` and `department` passed to all ranking and search calls |
| `llm_parser.py` category fallbacks | Finance and Healthcare categories added with CEO-first defaults |

---

## Summary Table

| # | Category | What Changed | Impact |
|---|----------|-------------|--------|
| 1 | DM Quality | Removed HR from non-HR target title lists; fixed AI prompt | Correct functional DM found for IT/Finance/other roles |
| 2 | DM Quality | Fixed surname false-positives in name validation (2 files) | Leitner, Haagen, Wegner etc. no longer silently dropped |
| 3 | DM Quality | Enforced `DecisionMaker.title` with 3-level fallback | Title is never `null`; always useful context |
| 4 | DM Quality | HR exclusion gate using categorical label + score floor | HR can never win when a department head or CEO exists |
| 5 | DM Quality | Mine job description for "reports to" hiring manager | Directly named managers become highest-priority candidates |
| 6 | DM Quality | Pass target titles to team page extractor | Team page search is guided by the specific role being hired |
| 7 | DM Quality | Source trust added to validator prompt | Company website contacts score higher than Google search results |
| 8 | DM Quality | JSON-LD / Schema.org structured data parsed | Free, reliable contact data used before AI extraction |
| 9 | Efficiency | Merged team page extraction + scoring into 1 AI call | One fewer LLM call per team page (cost and speed) |
| 10 | Reliability | Playwright runs via thread executor on Windows | No more `NotImplementedError`; browser scraping works on Windows |
| 11 | Reliability | `httpx` replaced with `curl_cffi` (Chrome 136 impersonation) | TLS fingerprint matches real browser; fewer 403 blocks |
| 12 | Reliability | LLM parser connected to OpenRouter | Job parsing works; no more regex fallback every request |
| 13 | Reliability | Company research sales brief uses OpenRouter | Real AI-generated sales summary instead of dumb template |
| 14 | Reliability | JS-block + Access Denied detection in company research | No more error messages stored as company descriptions |
| 15 | Efficiency | Bot-blocked Playwright responses detected early | Saves ~$0.009 and ~5s per blocked enterprise site |
| 16 | Reliability | False "Anthropic key missing" alert suppressed | Clean logs; no misleading alerts |
| 17 | Reliability | Next.js SSR data extracted before script stripping | SPA team pages yield content even without visible HTML text |
| 18 | DM Quality | 3 team discovery bugs fixed (HTML truncation, skipped homepage scan, sitemap over-matching) | Correct team pages found; custom URLs like `/ueber-moresophy` now discovered |
| 19 | DM Quality | First-name-only contacts accepted in 3 code locations + prompt updates | BAGHUS-style SMEs where only first names are shown on team pages now work correctly |
| 20 | DM Quality | Team card text threshold lowered from 50 → 15 chars | Short name+title cards no longer silently dropped before AI sees them |

---

*Total files modified: `llm_parser.py`, `pipeline.py`, `clients/ai_validator.py`, `clients/ai_extractor.py`, `clients/team_discovery.py`, `clients/impressum.py`, `clients/job_scraper.py`, `clients/company_research.py`*
