# PeopleIQ — Workforce Intelligence Platform

> Ask anything about your workforce. Get answers in seconds.

<!-- Screenshot placeholder — add after Phase 2 UI is built -->

---

## What is PeopleIQ?

PeopleIQ is a natural language analytics platform that lets HR leaders ask plain-English questions about their workforce data and get instant, accurate answers — no SQL, no dashboards, no data analysts required. It is built for HR Directors, CHROs, and People Analytics teams at mid-size companies who need workforce intelligence without the infrastructure overhead.

---

## Why I built this

Every company I've worked with has the same problem: the data exists, but getting a real answer from it takes days. An HR leader wants to know which departments have the highest 90-day attrition — a question that should take ten seconds — and instead waits a week for someone to build a report. I built PeopleIQ to collapse that gap entirely. The intelligence is in the data model and the query engine. The chat interface makes it accessible to anyone who can type a sentence. This is what people analytics should feel like.

---

## How it works

```
User asks a question
        ↓
Text-to-SQL engine (Claude API) converts it to SQL
        ↓
SQL executes against the star schema database
        ↓
Claude converts the result set into a plain-English answer
```

The backend is a FastAPI application that receives a natural language question, assembles a prompt containing the full database schema, sends it to the Anthropic Claude API to generate a SQL query, executes that query against a SQLite database built on a purpose-designed star schema, and then passes the result set back to Claude to produce a human-readable answer. The frontend is a single-page Next.js application deployed on Vercel. The data layer is a 12-table dimensional model — six dimension tables and six fact tables — populated with 500 synthetic employees across 7 years of workforce history.

---

## Live Demo

> Link to Vercel deployment — coming after Phase 2 deployment

**Example questions you can ask:**

- What is our current total headcount?
- What is our attrition rate this quarter?
- Which locations have the highest turnover?
- How long does it take us to fill a role on average?
- Which departments grew the most this year?
- How many people left within their first 90 days?
- What percentage of terminations were voluntary last year?
- Which positions take the longest to fill?
- How does turnover compare across regions?
- What is the tenure distribution of our active workforce?

---

## Tech Stack

| Layer | Technology |
|---|---|
| Frontend | Next.js 14, deployed on Vercel |
| Backend | FastAPI (Python 3.11+) |
| Query Engine | Anthropic Claude API — Text-to-SQL |
| Data Layer | Star schema — 6 dimensions, 6 fact tables |
| Database | SQLite (dev) / PostgreSQL (prod) |
| Data | Synthetic — 500 employees, 7 years, 12 tables |
| Data Generation | Python — Faker, pandas, numpy |

---

## Data Model

PeopleIQ uses a canonical dimensional star schema designed to be system-agnostic — it does not assume UKG, Workday, ADP, or any specific HRIS. All source system fields map to this schema; the intelligence core always queries against it.

**Dimension tables** (reference data — who, where, what):

| Table | Grain | Key Fields |
|---|---|---|
| `dim_person` | One row per employee | person_id, status, hire_date, termination_date, employment_type |
| `dim_position` | One row per standard role | position_id, position_title, job_family, job_level |
| `dim_org_unit` | One row per department | org_unit_id, org_unit_name, parent_org_unit_id, org_level |
| `dim_work_location` | One row per site | location_id, location_name, city, state, region, location_type |
| `dim_company` | One row per legal entity | company_id, company_name, company_code |
| `dim_date` | One row per calendar day | date_id, year, quarter, month, is_month_end, fiscal_year |

**Fact tables** (measurements and events):

| Table | Grain | Key Measures |
|---|---|---|
| `fact_headcount_snapshot` | Person × Month-end | is_active, tenure_months, tenure_band |
| `fact_employment_event` | Person × Event | event_type (Hire/Termination/Transfer), tenure_days_at_event |
| `fact_position_assignment` | Person × Role × Date range | effective_start, effective_end, is_current |
| `fact_requisition` | One row per open role | status, days_to_fill, hires_count |
| `fact_recruiting_pipeline` | Candidate × Stage | stage_name, stage_date, conversion_flag |
| `fact_exit_interview` | One row per respondent | reason_name, manager_rating_avg, voluntary_flag |

See `PeopleIQ_PRD_v02.docx` for the full data model specification.

---

## Running Locally

Assumes Python 3.11+ and Node 18+ are installed.

### 1. Clone and generate data

```bash
git clone https://github.com/Debdatta21/peopleIQ.git
cd peopleIQ
pip install -r requirements.txt
python generate_data.py
# Creates outputs/ with 12 CSVs and peopleiq_dev.db
```

### 2. Start the backend

```bash
cd backend
pip install -r requirements.txt
cp .env.example .env
# Edit .env and add your ANTHROPIC_API_KEY
uvicorn main:app --reload --port 8000
# API running at http://localhost:8000
```

### 3. Start the frontend

```bash
cd frontend
npm install
cp .env.local.example .env.local
npm run dev
# UI running at http://localhost:3000
```

Open [http://localhost:3000](http://localhost:3000) and start asking questions.

---

## Project Status

| Phase | Description | Status |
|---|---|---|
| Phase 1 | Data Foundation — canonical schema, synthetic data generator, 12-table star schema | ✅ Complete |
| Phase 2 | Chat Interface — FastAPI backend, Text-to-SQL engine, Next.js frontend, Vercel deployment | 🔄 In Progress |
| Phase 3 | Live HRIS Connector — UKG People Fabric API, CSV flat-file connector, OAuth 2.0 | ⬜ Planned |
| Phase 4 | Multi-tenant Product — multi-company, role-based access, first paying customer | ⬜ Planned |

---

## About

Built by **Debdatta Gupta** — Data Analyst & Analytics Engineer at Technology Partners, St. Louis MO.

[LinkedIn](https://www.linkedin.com/in/debdattagupta) · [GitHub](https://github.com/Debdatta21)
