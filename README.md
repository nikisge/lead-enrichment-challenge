# Lead Enrichment System - Developer Challenge

## What This System Does

This is a **Lead Enrichment Service** built for a recruitment agency (Personalvermittlung) in the DACH region (Germany, Austria, Switzerland).

**Input:** A job posting (title, company, description, URL)
**Output:** The best **decision maker** (contact person) at the company + their phone number and email

The system runs as a FastAPI service and is called by an n8n automation workflow.

---

## The Pipeline (7 Phases)

```
Job Posting Input
    |
    v
1. LLM PARSE (llm_parser.py)
   Extract company name, domain, contact info, target_titles, department
    |
    v
2. DOMAIN DISCOVERY (pipeline.py)
   Find the company's website domain
    |
    v
3. PARALLEL SCRAPING (pipeline.py + clients/)
   Scrape job URL, impressum page, team pages simultaneously
    |
    v
4. CANDIDATE COLLECTION & RANKING  <-- YOUR FOCUS
   Collect candidates from 5 sources, validate with AI, rank by relevance
    |
    v
5. LINKEDIN VERIFICATION (apify_linkedin.py)
   Verify the candidate still works at the company
    |
    v
6. PHONE ENRICHMENT (pipeline.py)
   Find phone number via BetterContact / FullEnrich / Kaspr
    |
    v
7. COMPANY RESEARCH (company_research.py)
   Generate sales brief about the company
```

---

## Your Task: Improve Decision Maker Matching

### The Problem

Currently, the system **always prioritizes HR managers and CEOs** as the decision maker, regardless of the job type. This is wrong for a recruitment agency:

- For a **Software Developer** job → we want the **CTO / Head of IT**, not HR
- For a **Accountant** job → we want the **CFO / Head of Finance**, not HR
- For a **Sales Manager** job → we want the **Head of Sales / VP Sales**, not HR
- HR Manager should be the **fallback**, not the default

### What's Already There (But Unused!)

The LLM parser (`llm_parser.py`) already extracts two useful fields:
- **`target_titles`** — A list of ideal decision maker titles for this job (e.g., `["CTO", "IT-Leiter", "Head of IT"]`)
- **`department`** — The department this job belongs to (e.g., `"IT"`, `"Finance"`, `"Sales"`)

**But these fields are never used downstream!** They are extracted and then ignored.

### Where the Relevant Code Lives

| File | What It Does | Key Lines |
|------|-------------|-----------|
| `llm_parser.py` | Extracts `target_titles` and `department` from job posting | `_get_default_titles()` function |
| `pipeline.py` | Main orchestration — collects candidates from 5 sources | Search for `# --- Phase` comments |
| `clients/ai_validator.py` | **`validate_and_rank_candidates()`** — AI scores candidates by relevance | The LLM prompt with scoring rules |
| `clients/linkedin_search.py` | **`find_multiple_decision_makers()`** — Google search for DMs | Search queries and `_get_category_query()` |
| `models.py` | Data models — `ParsedJobPosting`, `DecisionMaker`, etc. | |

### Current Scoring (in `ai_validator.py`)

```
HR/Personal/Recruiting:                    100 points
Department head matching job category:      80 points
CEO/Geschäftsführer/Inhaber:               60 points
Other named contacts:                       40 points
```

**This should be smarter** — if the job is in IT, the CTO should score higher than HR.

### What You Should Improve

**Minimum (required):**
1. Pass `target_titles` and `department` from the parser through to the ranking and search functions
2. Make the candidate scoring job-aware (department heads matching the job should rank higher than generic HR)
3. Adjust the DM fallback search to search for job-relevant titles first, HR as fallback

**Bonus (optional, shows initiative):**
- Improve the `_get_default_titles()` mapping in `llm_parser.py`
- Add smarter logic to match candidates to job types
- Propose new data sources or approaches for finding the right DM
- Any other improvements you think would help

### How to Test Your Changes

```bash
# Run the existing test scripts
python test_dm_discovery.py
python test_full_flow.py

# Or start the server and send a test request
python -m uvicorn main:app --reload --port 8000

# Test request (example)
curl -X POST http://localhost:8000/webhook/enrich/sync \
  -H "Content-Type: application/json" \
  -d '{
    "id": "test-1",
    "title": "Senior Software Engineer",
    "company": "SAP SE",
    "description": "We are looking for a Senior Software Engineer to join our cloud team...",
    "category": "it",
    "url": "https://www.sap.com/careers",
    "location": "Walldorf, Germany"
  }'
```

---

## Setup Instructions

### 1. Fork This Repo

Click **"Fork"** in the top right corner of this GitHub page. This creates your own copy.

### 2. Clone Your Fork

```bash
git clone https://github.com/YOUR-USERNAME/lead-enrichment-challenge.git
cd lead-enrichment-challenge
```

### 3. Create Virtual Environment

```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

### 4. Set Up Environment

```bash
cp .env.example .env
# The .env.example already contains a working OpenRouter API key for testing
# Do NOT share this key or use it for other projects
```

### 5. Run the Service

```bash
python -m uvicorn main:app --reload --port 8000
```

Visit `http://localhost:8000/health` to verify it's running.

---

## How to Submit Your Solution

1. Create a new branch on your fork: `git checkout -b improve-dm-matching`
2. Make your changes
3. Commit with clear messages explaining what you changed and why
4. Push to your fork: `git push origin improve-dm-matching`
5. Go to the **original repo** on GitHub and click **"New Pull Request"**
6. Select your fork and branch as the source
7. Write a clear description of your approach

### What We Evaluate

| Criteria | What We Look For |
|----------|-----------------|
| **Code Quality** | Does it fit the existing code style? Clean, readable? |
| **Understanding** | Did you understand the pipeline and the problem? |
| **Result Quality** | Does your solution actually find better decision makers? |
| **Communication** | Is your PR well-explained? Did you document your approach? |
| **Initiative** | Did you go beyond the minimum? Any creative ideas? |

---

## Key Technical Details

- **Language:** Python 3.9+
- **Framework:** FastAPI + async/await
- **LLM Access:** Via OpenRouter API (`clients/llm_client.py`) — uses Gemini Flash (fast), Haiku (balanced), Sonnet (smart)
- **Region Focus:** DACH (Germany, Austria, Switzerland) — many German job titles and company structures
- **Trust Model:** Candidates from `job_url`, `llm_parse`, `team_page`, `impressum` are trusted. Candidates from `linkedin_fallback` (Google search) are untrusted and require LinkedIn verification.

### Important Files You Probably Don't Need to Touch

- Phone enrichment clients (`kaspr.py`, `bettercontact.py`, `fullenrich.py`) — not part of this task
- `company_research.py` — not part of this task
- `impressum.py` — scraping logic, not part of this task
- `config.py` — configuration, should stay as-is

---

## Questions?

If you have questions about the codebase or the task, open an **Issue** on this repo. Do not contact the client directly.

**Deadline:** [TO BE FILLED]

Good luck!
