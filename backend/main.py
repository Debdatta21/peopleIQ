"""
PeopleIQ Phase 2 — FastAPI Backend (Groq edition)
==================================================
Single endpoint: POST /chat
  Input:  { "question": "string" }
  Output: { "answer": "string", "sql": "string|null", "row_count": int }

Uses Groq free tier (llama-3.3-70b-versatile) for Text-to-SQL and answer generation.
No private or real employee data is used. All data is synthetic.
"""

import os
import re
import sqlite3
import logging
from typing import Optional

import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("peopleiq")

# ── Config ────────────────────────────────────────────────────────────────────
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = "llama-3.1-8b-instant"   # 20k TPM free tier vs 6k for 70b — far fewer 429s
MAX_RETRIES = 3

DB_PATH = os.getenv(
    "PEOPLEIQ_DB_PATH",
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "outputs",
        "peopleiq_dev.db",
    ),
)

PII_COLUMNS = {"full_name", "email", "first_name", "last_name"}

# ── Schema extraction ─────────────────────────────────────────────────────────
# Compact schema — table(columns) only. Saves ~1000 tokens vs full DDL.
SCHEMA_DDL = """
dim_company(company_id, company_name, company_code)
dim_date(date_id, full_date, year, quarter, month, month_name, day_of_week, is_weekend, fiscal_year)
dim_org_unit(org_unit_id, org_unit_name, parent_org_unit_id, org_level, company_id)
dim_person(person_id, status, hire_date, termination_date, termination_type, employment_type, company_id)
dim_position(position_id, position_title, job_family, job_level, company_id)
dim_work_location(location_id, location_name, city, state, region, location_type, company_id)
fact_compensation(compensation_id, person_id, position_id, company_id, date_id, effective_date, base_amount, compensation_type, change_reason, change_pct, is_current)
fact_employment_event(event_id, person_id, date_id, event_type, termination_type, tenure_days_at_event, position_id, org_unit_id, location_id, company_id)
fact_exit_interview(exit_id, person_id, position_id, org_unit_id, location_id, company_id, date_id, exit_date, tenure_days, reason_name, manager_rating_avg, voluntary_flag)
fact_headcount_snapshot(snapshot_id, person_id, date_id, position_id, org_unit_id, location_id, company_id, is_active, employment_type, tenure_days, tenure_months, tenure_band)
fact_position_assignment(assignment_id, person_id, position_id, org_unit_id, location_id, company_id, effective_start, effective_end, is_current, promotion_flag)
fact_recruiting_pipeline(pipeline_id, req_id, candidate_id, stage_name, stage_date, date_id, conversion_flag, company_id)
fact_requisition(req_id, position_id, org_unit_id, location_id, company_id, status, published_date, fill_date, days_to_fill, hires_count)
"""

SQL_SYSTEM_PROMPT = f"""You are the Text-to-SQL engine for PeopleIQ, a workforce intelligence platform.
Output ONE valid SQLite SELECT query. No markdown, no explanation.

Schema:
{SCHEMA_DDL}

Rules:
- SELECT only. No INSERT/UPDATE/DELETE/DROP/CREATE/ALTER/TRUNCATE/PRAGMA.
- Never use columns: full_name, email (PII).
- Always alias columns clearly (AS "Headcount", AS "Attrition Rate %").
- LIMIT 100 unless user asks for fewer.
- Dates: spine is 2019-01-01 to 2026-06-04. "this year"=2026, "last year"=2025, "this quarter"=Q2 2026, "last quarter"=Q1 2026.

Table routing — use the correct table or the answer will be wrong:
- headcount / workforce size / how many employees → fact_headcount_snapshot
- attrition / turnover / terminations / who left → fact_employment_event
- time to fill / open roles / recruiting → fact_requisition or fact_recruiting_pipeline
- promotions / role changes / position history → fact_position_assignment (use promotion_flag=1 for promotions)
- compensation / salary / pay / raises / comp by level → fact_compensation
- exit reasons / why people left → fact_exit_interview

Patterns:

1. Current headcount:
SELECT COUNT(*) AS "Current Headcount" FROM fact_headcount_snapshot
WHERE is_active=1 AND date_id=(SELECT MAX(date_id) FROM fact_headcount_snapshot WHERE is_active=1)

2. Attrition rate for a period:
SELECT ROUND(COUNT(DISTINCT e.person_id)*100.0/
  (SELECT COUNT(DISTINCT person_id) FROM fact_headcount_snapshot
   WHERE is_active=1 AND date_id IN (SELECT date_id FROM dim_date WHERE year=2026 AND quarter=2)),1
) AS "Attrition Rate %"
FROM fact_employment_event e JOIN dim_date d ON e.date_id=d.date_id
WHERE e.event_type='Termination' AND d.year=2026 AND d.quarter=2

3. Time to fill: SELECT ROUND(AVG(days_to_fill),1) AS "Avg Days to Fill" FROM fact_requisition WHERE status='Filled'

4. Turnover by location (default year=2026):
SELECT l.location_name AS "Location",
  ROUND(COUNT(DISTINCT e.person_id)*100.0/
    (SELECT COUNT(DISTINCT person_id) FROM fact_headcount_snapshot h
     JOIN dim_date d2 ON h.date_id=d2.date_id
     WHERE h.is_active=1 AND d2.year=2026 AND h.location_id=l.location_id),1) AS "Turnover Rate %"
FROM fact_employment_event e
JOIN dim_work_location l ON e.location_id=l.location_id
JOIN dim_date d ON e.date_id=d.date_id
WHERE e.event_type='Termination' AND d.year=2026
GROUP BY l.location_name, l.location_id ORDER BY "Turnover Rate %" DESC

5. Compensation BY job level — always GROUP BY job_level (use for "salary by level", "pay by level", "average salary per level", "pay range by level"):
SELECT p.job_level AS "Job Level",
  COUNT(DISTINCT fc.person_id) AS "Employees",
  ROUND(AVG(fc.base_amount),0) AS "Avg Salary",
  MIN(fc.base_amount) AS "Min Salary",
  MAX(fc.base_amount) AS "Max Salary",
  ROUND(AVG(CASE WHEN pr BETWEEN 0.45 AND 0.55 THEN fc.base_amount END),0) AS "Median Salary"
FROM (SELECT fc.*, p2.job_level,
        PERCENT_RANK() OVER (PARTITION BY p2.job_level ORDER BY fc.base_amount) AS pr
      FROM fact_compensation fc JOIN dim_position p2 ON fc.position_id=p2.position_id
      WHERE fc.is_current=1 AND fc.compensation_type='Salary') fc
JOIN dim_position p ON fc.position_id=p.position_id
GROUP BY p.job_level ORDER BY AVG(fc.base_amount) DESC

5b. Pay RANGE / spread between highest and lowest job levels:
SELECT
  MAX(avg_sal) - MIN(avg_sal) AS "Pay Spread ($)",
  MAX(job_level) AS "Highest Level",  -- alphabetically last; use MAX(avg_sal) logic below
  MIN(job_level) AS "Lowest Level"
FROM (
  SELECT p.job_level, ROUND(AVG(fc.base_amount),0) AS avg_sal
  FROM fact_compensation fc JOIN dim_position p ON fc.position_id=p.position_id
  WHERE fc.is_current=1 AND fc.compensation_type='Salary'
  GROUP BY p.job_level
)
-- BETTER: show full table from Pattern 5 and let the answer layer describe the spread.

6. Employees with NO salary increase in the past N years — compute cutoff as today minus N years:
-- "past 2 years" → cutoff = DATE('2026-06-04','-2 years') = '2024-06-04'
-- "past 1 year"  → cutoff = DATE('2026-06-04','-1 year')  = '2025-06-04'
SELECT COUNT(DISTINCT p.person_id) AS "Employees With No Raise"
FROM dim_person p
WHERE p.status='Active'
AND p.person_id NOT IN (
  SELECT DISTINCT fc.person_id FROM fact_compensation fc
  WHERE fc.effective_date >= DATE('2026-06-04','-2 years')
  AND fc.change_reason IN ('Annual Review','Merit Increase','Market Adjustment','Promotion')
)

7. Average merit increase by reason:
SELECT change_reason AS "Reason", ROUND(AVG(change_pct),1) AS "Avg % Increase", COUNT(*) AS "Events"
FROM fact_compensation WHERE change_reason != 'Hire'
GROUP BY change_reason ORDER BY "Avg % Increase" DESC

8. Promotions with pay increase (cross-table) — join on YEAR only (not month), compensation review date and assignment start date are in the same year but not same month:
SELECT pa.person_id AS "Person ID",
  pos_old.job_level AS "Previous Level", pos_new.job_level AS "New Level",
  ROUND(fc.change_pct,1) AS "Pay Increase %",
  pa.effective_start AS "Promotion Date"
FROM fact_position_assignment pa
JOIN dim_position pos_new ON pa.position_id=pos_new.position_id
JOIN fact_compensation fc ON pa.person_id=fc.person_id AND fc.change_reason='Promotion'
  AND SUBSTR(fc.effective_date,1,4)=SUBSTR(pa.effective_start,1,4)
JOIN dim_position pos_old ON pos_old.position_id=(
  SELECT position_id FROM fact_position_assignment
  WHERE person_id=pa.person_id AND effective_start < pa.effective_start
  ORDER BY effective_start DESC LIMIT 1)
WHERE pa.promotion_flag=1
ORDER BY pa.effective_start DESC

9. Promotion rate: SELECT ROUND(COUNT(DISTINCT person_id)*100.0/(SELECT COUNT(*) FROM dim_person WHERE status='Active'),1) AS "Promotion Rate %" FROM fact_position_assignment WHERE promotion_flag=1

10. Contractor vs full-time pay comparison — always show compensation_type separately with units note; NEVER compare Contract Rate (hourly $/hr) directly to Salary (annual $) as a ratio:
SELECT compensation_type AS "Comp Type",
  COUNT(DISTINCT person_id) AS "Employees",
  ROUND(AVG(base_amount),0) AS "Avg Pay",
  MIN(base_amount) AS "Min", MAX(base_amount) AS "Max"
FROM fact_compensation WHERE is_current=1
GROUP BY compensation_type ORDER BY AVG(base_amount) DESC

Rules for compensation:
- is_current=1 → current state questions ("what is comp today")
- Filter by effective_date year → historical questions ("what were salaries in 2025")
- compensation_type: 'Salary' (annual $, W2 Salaried), 'Hourly' ($/hr, W2 Hourly), 'Contract Rate' ($/hr, Contingent/Agency)
- Salary is annual dollars. Hourly and Contract Rate are dollars per hour. Never compare them as if same units.
- change_reason values: 'Hire','Annual Review','Merit Increase','Market Adjustment','Promotion'
- Never mix is_current=1 with a year filter
- "past N years/months" → use DATE('2026-06-04','-N years') for the cutoff, not a hardcoded January date
"""


ANSWER_SYSTEM_PROMPT = (
    "You are PeopleIQ, a friendly workforce analytics assistant. "
    "Turn SQL query results into clear, concise answers for a non-technical HR audience.\n"
    "- Write in complete sentences. Use plain English.\n"
    "- Never use SQL, column names, or technical jargon.\n"
    "- Be specific with numbers. Round percentages to one decimal place.\n"
    "- Compensation units: 'Salary' values are annual dollars (e.g. $74,000/yr). "
    "'Hourly' and 'Contract Rate' values are dollars per hour (e.g. $35/hr). "
    "NEVER compare annual salary to hourly rates as if they are the same unit — always state the unit.\n"
    "- If results are empty, say so clearly and suggest a related question.\n"
    "- Keep answers under 150 words unless the data genuinely requires more.\n"
    "- Do not mention employee names under any circumstances.\n"
    "- End every answer with a new line starting with 'Data sources:' followed by a plain-English "
    "comma-separated list of the data sources used (e.g. 'Data sources: Employee records, Location data, Termination events'). "
    "Use plain business language, not table names."
)

# ── Groq API call ─────────────────────────────────────────────────────────────
def call_groq(system_prompt: str, user_content: str) -> str:
    if not GROQ_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="GROQ_API_KEY is not configured. Add it to backend/.env",
        )
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.1,
        "max_tokens": 1024,
    }
    import time
    resp = None
    for attempt in range(3):
        resp = requests.post(GROQ_URL, headers=headers, json=payload, timeout=30)
        if resp.status_code == 429:
            # Honour Groq's Retry-After header when present; else back off 20/40/60s
            retry_after = resp.headers.get("Retry-After")
            wait = int(retry_after) if retry_after and retry_after.isdigit() else 20 * (attempt + 1)
            log.warning(f"Groq 429 rate limit — waiting {wait}s (attempt {attempt+1}/3)")
            time.sleep(wait)
            continue
        resp.raise_for_status()
        break
    else:
        # All 3 retries exhausted on 429 — surface a clean error instead of crashing
        raise HTTPException(
            status_code=429,
            detail="Groq rate limit reached. Please wait 30–60 seconds and try again.",
        )
    data = resp.json()
    usage = data.get("usage", {})
    log.info(
        f"[tokens] prompt={usage.get('prompt_tokens','?')} "
        f"completion={usage.get('completion_tokens','?')} "
        f"total={usage.get('total_tokens','?')}"
    )
    return data["choices"][0]["message"]["content"].strip()


# ── FastAPI app ───────────────────────────────────────────────────────────────
app = FastAPI(title="PeopleIQ API", version="2.3.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    question: str


class ChatResponse(BaseModel):
    answer: str
    sql: Optional[str] = None
    row_count: int = 0


# ── Core functions ────────────────────────────────────────────────────────────
def generate_sql(question: str, error_context: str = "") -> str:
    user_content = question
    if error_context:
        user_content = (
            f"Question: {question}\n\n"
            f"The previous SQL query failed with this error:\n{error_context}\n\n"
            "Please generate a corrected SQL query that avoids this error."
        )
    raw = call_groq(SQL_SYSTEM_PROMPT, user_content)
    raw = re.sub(r"^```(?:sql)?\s*\n?", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"\s*```$", "", raw)
    return raw.strip()


def validate_sql(sql: str) -> tuple[bool, str]:
    cleaned = sql.strip().upper()
    if not cleaned.startswith("SELECT"):
        return False, "Query does not begin with SELECT"
    for kw in ["INSERT", "UPDATE", "DELETE", "DROP", "CREATE", "ALTER",
               "TRUNCATE", "REPLACE", "ATTACH", "DETACH", "PRAGMA"]:
        if re.search(rf"\b{re.escape(kw)}\b", cleaned):
            return False, f"Query contains forbidden keyword: {kw}"
    return True, "ok"


def execute_query(sql: str) -> tuple[list[dict], int]:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        cur = con.execute(sql)
        raw_rows = cur.fetchall()
    finally:
        con.close()
    rows = []
    for raw in raw_rows:
        row_dict = dict(raw)
        for col in list(row_dict.keys()):
            if col.lower() in PII_COLUMNS:
                del row_dict[col]
        rows.append(row_dict)
    return rows, len(rows)


def _rows_to_csv(rows: list[dict]) -> str:
    """Convert rows to compact CSV — ~30% fewer tokens than dict repr."""
    if not rows:
        return ""
    headers = list(rows[0].keys())
    lines = [",".join(headers)]
    for r in rows:
        lines.append(",".join(str(r.get(h, "")) for h in headers))
    return "\n".join(lines)


def generate_answer(question: str, rows: list[dict], row_count: int) -> str:
    if not rows:
        results_text = "The query returned no results."
    elif row_count == 1:
        # Aggregate result — pass everything
        results_text = _rows_to_csv(rows)
    else:
        # Multi-row — top 10 is enough for the LLM to generate an insight
        sample = rows[:10]
        results_text = _rows_to_csv(sample)
        if row_count > 10:
            results_text += f"\n... ({row_count} total rows, showing top 10)"
    log.info(f"[answer] passing {min(row_count,10)}/{row_count} rows to LLM")
    return call_groq(
        ANSWER_SYSTEM_PROMPT,
        f"Question: {question}\n\nResults ({row_count} row(s)):\n{results_text}"
    )


# ── /chat endpoint ────────────────────────────────────────────────────────────
FALLBACK_RESPONSE = ChatResponse(
    answer=(
        "I wasn't able to answer that question. "
        "Try rephrasing it, or ask something related — "
        "for example: 'What is our current headcount?' or 'What is our attrition rate this year?'"
    ),
    sql=None,
    row_count=0,
)


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    question = request.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question cannot be empty.")

    error_context = ""
    for attempt in range(1, MAX_RETRIES + 1):
        log.info(f"[attempt {attempt}/{MAX_RETRIES}] Generating SQL for: {question!r}")
        try:
            sql = generate_sql(question, error_context)
        except HTTPException:
            raise  # propagate 429 / 503 directly — don't wrap in 502
        except Exception as exc:
            log.error(f"Groq call failed: {exc}")
            raise HTTPException(status_code=502, detail=f"Groq API error: {exc}")

        log.info(f"Generated SQL: {sql}")
        is_valid, reason = validate_sql(sql)
        if not is_valid:
            error_context = f"SQL validation failed: {reason}. Query was: {sql}"
            log.warning(f"Attempt {attempt}: validation failed — {reason}")
            continue

        try:
            rows, row_count = execute_query(sql)
        except Exception as exc:
            error_context = str(exc)
            log.warning(f"Attempt {attempt}: execute failed — {exc}")
            continue

        answer = generate_answer(question, rows, row_count)
        log.info(f"Answer: {answer[:120]}...")
        return ChatResponse(answer=answer, sql=sql, row_count=row_count)

    log.error(f"All {MAX_RETRIES} attempts failed for: {question!r}")
    return FALLBACK_RESPONSE

# ── Health check ──────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    db_exists = os.path.exists(DB_PATH)
    table_count = len([l for l in SCHEMA_DDL.strip().splitlines() if l.strip()])
    return {
        "status": "ok",
        "db_exists": db_exists,
        "schema_tables_loaded": table_count,
        "model": MODEL,
    }
