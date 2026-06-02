# ◈ PeopleIQ — Workforce Intelligence Platform

> **Ask anything about your workforce. Get answers in seconds.**

Natural language people analytics for HR leaders, CHROs, and executives — no SQL, no dashboards, no data analyst required.

**🔴 Live Demo:** [people-iq.vercel.app](https://people-iq.vercel.app) *(synthetic data only — no real employee data)*

---

## What is PeopleIQ?

PeopleIQ is a chat interface for HR data. You type a question. It answers.

```
"What is our attrition rate this quarter?"     →  4.2%, down from 6.1% last quarter.
"Which locations have the highest turnover?"   →  Sacramento 18%, San Antonio 16%, Pittsburgh 12%.
"How long does it take us to fill a role?"     →  43 days on average. Engineering roles take 58 days.
"Which departments grew the most this year?"   →  HR +46, Talent Acquisition +39, Marketing +38.
"How many people left in their first 90 days?" →  47 employees — 9.4% of total headcount.
```

Behind every answer: a dimensional data model, a Text-to-SQL engine powered by an LLM, and a clean frontend. Every answer shows plain-English **Data sources** so stakeholders know exactly what data was used.

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│  LAYER 1 — Data Sources                                  │
│  Databricks SQL  │  Microsoft Fabric  │  CSV flat-file   │
│  (HR views)      │  (OneLake/dbt)     │  (any HRIS)      │
└──────────────────────────┬──────────────────────────────┘
                           │ normalize to canonical schema
┌──────────────────────────▼──────────────────────────────┐
│  LAYER 2 — Connector & Canonical HR Schema               │
│  12-table star schema (6 dims + 6 facts)                 │
│  Python connector — Databricks ODBC / CSV ingestion      │
└──────────────────────────┬──────────────────────────────┘
                           │ SQL query execution
┌──────────────────────────▼──────────────────────────────┐
│  LAYER 3 — Intelligence Core (FastAPI + Groq LLM)        │
│  Text-to-SQL  →  SQL Validator  →  Query Engine          │
│  →  Answer Generator (plain English + data sources)      │
└──────────────────────────┬──────────────────────────────┘
                           │ REST API
┌──────────────────────────▼──────────────────────────────┐
│  LAYER 4 — User Interface (Next.js · Vercel)             │
│  HR Director │ CHRO │ HR Business Partner │ Exec         │
└─────────────────────────────────────────────────────────┘
```

### Technology Partners Integration Path

PeopleIQ is designed to sit **on top of existing Databricks/Fabric infrastructure**, not replace it:

| Term | Approach |
|------|----------|
| **Short term** | CSV exports from any HRIS → PeopleIQ. Demo-ready immediately. |
| **Medium term** | Expose 2–3 HR views in Databricks SQL → PeopleIQ queries them directly. Data stays in your warehouse. |
| **Long term** | Power BI for structured reporting. PeopleIQ for ad-hoc natural language questions. Same data, two interfaces. |

> *Power BI answers the questions we already know to ask. PeopleIQ answers the questions people type into a search box.*

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend | Next.js 14 — deployed on Vercel |
| Backend | FastAPI (Python 3.14) — deployed on Render |
| LLM | Groq API — `llama-3.3-70b-versatile` (free tier) |
| Database (dev) | SQLite — 12-table star schema, synthetic data |
| Database (prod) | Databricks SQL / Microsoft Fabric |
| Data Generation | Python — Faker, pandas, numpy |
| Security | PII stripped before LLM, read-only SQL validation |

---

## Data Model

System-agnostic dimensional star schema. All source systems map to this schema.

**Dimension tables:** `dim_person`, `dim_position`, `dim_org_unit`, `dim_work_location`, `dim_company`, `dim_date`

**Fact tables:** `fact_headcount_snapshot`, `fact_employment_event`, `fact_position_assignment`, `fact_requisition`, `fact_recruiting_pipeline`, `fact_exit_interview`

---

## Running Locally

Requires Python 3.11+ and Node 18+.

```bash
# 1. Clone and generate synthetic data
git clone https://github.com/Debdatta21/peopleIQ.git
cd peopleIQ
pip install faker pandas numpy
python generate_data.py

# 2. Start the backend
cd backend
pip install -r requirements.txt
cp .env.example .env          # Add your GROQ_API_KEY
uvicorn main:app --reload --port 8000

# 3. Start the frontend (new terminal)
cd ../frontend
npm install
cp .env.local.example .env.local   # Set NEXT_PUBLIC_API_URL=http://localhost:8000
npm run dev
```

Open http://localhost:3000. Get a free Groq API key at https://console.groq.com.

---

## Project Status

| Phase | Description | Status |
|-------|-------------|--------|
| **Phase 1** | Data Foundation — canonical schema, synthetic data generator, 12-table SQLite | ✅ Complete |
| **Phase 2** | Chat Interface — FastAPI, Text-to-SQL, Next.js, Render + Vercel deployment | ✅ Complete |
| **Phase 3** | Live Data Connector — Databricks SQL (primary), CSV fallback, HRIS OAuth | ⬜ Planned |
| **Phase 4** | Multi-tenant Product — role-based access, Workday connector, first customer | ⬜ Planned |

---

## Security & Privacy

- All demo data is **100% synthetic** — no real employees, no real organizations
- PII columns (`full_name`, `email`) stripped from all result sets before reaching the LLM
- SQL validator enforces read-only queries — no mutations possible
- API keys stored in environment variables only, never committed to the repo
- Every answer shows a plain-English **Data sources** label for stakeholder transparency

---

## Documentation

See `PeopleIQ_PRD_v03.docx` for the full Product Requirements & Architecture Document including the Databricks/Fabric integration plan.

---

Built by **Debdatta Gupta** — Analytics Engineer at Technology Partners, St. Louis MO
