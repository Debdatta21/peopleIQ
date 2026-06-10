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
import time
import json
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

# ── Query log setup ───────────────────────────────────────────────────────────
def _init_query_log():
    """Create query_log table if it doesn't exist."""
    con = sqlite3.connect(DB_PATH)
    con.execute("""
        CREATE TABLE IF NOT EXISTS query_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            asked_at    TEXT    NOT NULL,
            question    TEXT    NOT NULL,
            sql_generated TEXT,
            row_count   INTEGER,
            success     INTEGER NOT NULL DEFAULT 1,
            error_msg   TEXT,
            latency_ms  INTEGER,
            chart_data  TEXT
        )
    """)
    con.commit()
    con.close()


def _log_query(question: str, sql: Optional[str], row_count: int,
               success: bool, error_msg: Optional[str],
               latency_ms: int, chart_data: Optional[dict]):
    try:
        con = sqlite3.connect(DB_PATH)
        con.execute(
            """INSERT INTO query_log
               (asked_at, question, sql_generated, row_count, success, error_msg, latency_ms, chart_data)
               VALUES (datetime('now'), ?, ?, ?, ?, ?, ?, ?)""",
            (question, sql, row_count, 1 if success else 0, error_msg,
             latency_ms, json.dumps(chart_data) if chart_data else None)
        )
        con.commit()
        con.close()
    except Exception as exc:
        log.warning(f"Failed to write query_log: {exc}")

# ── Guard rails: topic classifier + per-topic schema injection ────────────────

_GLOBAL_RULES = """You are the Text-to-SQL engine for PeopleIQ, a hospitality workforce intelligence platform.
Output ONE valid SQLite SELECT query. No markdown, no explanation.

RULES:
- SELECT only. No INSERT/UPDATE/DELETE/DROP/CREATE/ALTER/TRUNCATE/PRAGMA.
- Never return PII: full_name, email, forwarding_address, or any free-text narrative field.
- Always alias columns clearly (AS "Headcount", AS "Turnover %", AS "Property").
- LIMIT 100 unless the question requests fewer.
- Dates spine: 2019-01-01 to 2026-06-09. "this year"=2026, "last year"=2025, "this quarter"=Q2 2026.
- xw_location_active is THE location spine — always JOIN through it to get location_name or property_type.
- For separations: use dim_employee[termination_date] or fact_employee_event — NEVER fact_requisition_fill.
- Exit reasons: ALWAYS exclude reason_name='Other' — removed from all visuals by business rule.
"""

_TOPIC_CONTEXT = {
    "headcount": {
        "schema": """
xw_location_active(location_code PK, location_name, city, state, region, property_type[Property|Corporate], is_active)
dim_employee(employee_id, is_active_current[1|0], hire_date, termination_date, termination_type[Voluntary|Involuntary], standard_position, employment_type[Full-Time|Part-Time|Seasonal|Temporary], job_type[Property|Corporate], location_code)
fact_employee_snapshot_monthly(snapshot_id, employee_id, snapshot_date[month-end], is_active, tenure_months_asof_month_end, location_code, standard_position, employment_type)
""",
        "patterns": """
-- Live headcount:
SELECT COUNT(*) AS "Current Headcount" FROM dim_employee WHERE is_active_current=1

-- Property vs Corporate split:
SELECT job_type AS "Type", COUNT(*) AS "Headcount"
FROM dim_employee WHERE is_active_current=1 GROUP BY job_type ORDER BY "Headcount" DESC

-- Period-end (latest monthly snapshot):
SELECT COUNT(*) AS "Period-End Headcount" FROM fact_employee_snapshot_monthly
WHERE snapshot_date=(SELECT MAX(snapshot_date) FROM fact_employee_snapshot_monthly WHERE is_active=1) AND is_active=1

-- Headcount by region (always join location spine):
SELECT x.region AS "Region", x.property_type AS "Type", COUNT(*) AS "Headcount"
FROM dim_employee d JOIN xw_location_active x ON d.location_code=x.location_code
WHERE d.is_active_current=1 GROUP BY x.region, x.property_type ORDER BY "Headcount" DESC

-- Avg tenure active vs separated:
SELECT 'Active' AS "Status", ROUND(AVG(JULIANDAY('2026-06-09')-JULIANDAY(hire_date))/30.44,1) AS "Avg Tenure (Months)"
FROM dim_employee WHERE is_active_current=1
UNION ALL
SELECT 'Separated (last 12M)', ROUND(AVG(JULIANDAY(termination_date)-JULIANDAY(hire_date))/30.44,1)
FROM dim_employee WHERE is_active_current=0 AND termination_date >= DATE('2026-06-09','-12 months')
"""
    },
    "separations": {
        "schema": """
xw_location_active(location_code PK, location_name, city, state, region, property_type[Property|Corporate], is_active)
dim_employee(employee_id, is_active_current[1|0], hire_date, termination_date, termination_type[Voluntary|Involuntary], standard_position, employment_type, job_type[Property|Corporate], location_code)
fact_employee_event(event_id, employee_id, event_date, event_type[Hire|Termination], termination_type[Voluntary|Involuntary], tenure_days_at_event, location_code, standard_position)
fact_employee_snapshot_monthly(snapshot_id, employee_id, snapshot_date, is_active, tenure_months_asof_month_end, location_code)
""",
        "patterns": """
-- Rolling 12-month turnover rate:
SELECT ROUND(COUNT(DISTINCT e.employee_id)*100.0/
  (SELECT COUNT(DISTINCT s.employee_id) FROM fact_employee_snapshot_monthly s
   WHERE s.snapshot_date >= DATE('2026-06-09','-12 months') AND s.is_active=1),1) AS "Rolling 12M Turnover %"
FROM fact_employee_event e
WHERE e.event_type='Termination' AND e.event_date >= DATE('2026-06-09','-12 months')

-- Voluntary vs involuntary:
SELECT termination_type AS "Type", COUNT(*) AS "Count",
  ROUND(COUNT(*)*100.0/(SELECT COUNT(*) FROM fact_employee_event WHERE event_type='Termination'),1) AS "% of Total"
FROM fact_employee_event WHERE event_type='Termination' GROUP BY termination_type

-- Separations by standard position:
SELECT standard_position AS "Position", COUNT(*) AS "Separations"
FROM fact_employee_event WHERE event_type='Termination'
GROUP BY standard_position ORDER BY "Separations" DESC LIMIT 15

-- Separations by property (table result — many rows expected):
SELECT x.location_name AS "Property", x.state AS "State", COUNT(*) AS "Separations"
FROM fact_employee_event e JOIN xw_location_active x ON e.location_code=x.location_code
WHERE e.event_type='Termination' AND e.event_date >= DATE('2026-06-09','-12 months')
GROUP BY x.location_name, x.state ORDER BY "Separations" DESC
"""
    },
    "recruiting": {
        "schema": """
xw_location_active(location_code PK, location_name, city, state, region, property_type[Property|Corporate], is_active)
dim_requisition(req_id, status[Published|Filled|Draft|Cancelled], publish_date, fill_date, days_to_fill, opp_work_location_code, standard_position)
fact_requisition_fill(fill_id, req_id, days_publish_to_first_hire, hire_date, location_code)
""",
        "patterns": """
-- Open reqs count by status:
SELECT status AS "Status", COUNT(*) AS "Count" FROM dim_requisition GROUP BY status ORDER BY "Count" DESC

-- Open published reqs by property (table result):
SELECT x.location_name AS "Property", x.state AS "State", x.region AS "Region", COUNT(*) AS "Open Reqs"
FROM dim_requisition r JOIN xw_location_active x ON r.opp_work_location_code=x.location_code
WHERE r.status='Published' GROUP BY x.location_name, x.state, x.region ORDER BY "Open Reqs" DESC LIMIT 50

-- Avg days to fill with colour buckets:
SELECT ROUND(AVG(days_publish_to_first_hire),1) AS "Avg Days to Fill",
  SUM(CASE WHEN days_publish_to_first_hire<=30 THEN 1 ELSE 0 END) AS "On Target (0-30d)",
  SUM(CASE WHEN days_publish_to_first_hire BETWEEN 31 AND 60 THEN 1 ELSE 0 END) AS "Watch (31-60d)",
  SUM(CASE WHEN days_publish_to_first_hire>60 THEN 1 ELSE 0 END) AS "Critical (60+d)"
FROM fact_requisition_fill

-- Req aging buckets:
SELECT
  SUM(CASE WHEN JULIANDAY('2026-06-09')-JULIANDAY(publish_date)<=30 THEN 1 ELSE 0 END) AS "0-30 Days",
  SUM(CASE WHEN JULIANDAY('2026-06-09')-JULIANDAY(publish_date) BETWEEN 31 AND 60 THEN 1 ELSE 0 END) AS "31-60 Days",
  SUM(CASE WHEN JULIANDAY('2026-06-09')-JULIANDAY(publish_date)>60 THEN 1 ELSE 0 END) AS "60+ Days"
FROM dim_requisition WHERE status='Published'
"""
    },
    "exit_interviews": {
        "schema": """
dim_exit_reason(reason_id, reason_name)
fact_exit_interview(exit_id, employee_id, exit_date, job_type[Property|Corporate|Unknown], tenure_days, tenure_years, would_recommend[Recommend|Neutral|Would Not Recommend], mgr_dimension_1[1-3], mgr_dimension_2[1-3], mgr_dimension_3[1-3], mgr_dimension_4[1-3], location_code)
bridge_exit_reason(bridge_id, exit_id, reason_id)
xw_exit_interview_property(location_code PK, property_name, property_type)
""",
        "patterns": """
-- Top exit reasons (ALWAYS exclude Other):
SELECT r.reason_name AS "Reason", COUNT(*) AS "Count",
  ROUND(COUNT(*)*100.0/(SELECT COUNT(*) FROM bridge_exit_reason),1) AS "% of Tags"
FROM bridge_exit_reason b JOIN dim_exit_reason r ON b.reason_id=r.reason_id
WHERE r.reason_name != 'Other' GROUP BY r.reason_name ORDER BY "Count" DESC

-- Would recommend breakdown:
SELECT would_recommend AS "Response", COUNT(*) AS "Count",
  ROUND(COUNT(*)*100.0/(SELECT COUNT(*) FROM fact_exit_interview),1) AS "%"
FROM fact_exit_interview GROUP BY would_recommend ORDER BY "Count" DESC

-- Manager rating % positive (scale 1=neg, 2=neutral, 3=pos):
SELECT ROUND(AVG((CAST(mgr_dimension_1-1 AS REAL)+CAST(mgr_dimension_2-1 AS REAL)+
  CAST(mgr_dimension_3-1 AS REAL)+CAST(mgr_dimension_4-1 AS REAL))/8.0)*100,1) AS "Mgr Rating % Positive",
  COUNT(*) AS "Responses"
FROM fact_exit_interview

-- Exit responses by job type:
SELECT job_type AS "Job Type", COUNT(*) AS "Responses",
  ROUND(AVG(tenure_years),1) AS "Avg Tenure (Years)"
FROM fact_exit_interview GROUP BY job_type ORDER BY "Responses" DESC
"""
    },
    "retention": {
        "schema": """
dim_employee(employee_id, is_active_current[1|0], hire_date, termination_date, standard_position, employment_type, job_type[Property|Corporate], location_code)
fact_employee_snapshot_monthly(snapshot_id, employee_id, snapshot_date[month-end], is_active, tenure_months_asof_month_end, location_code)
xw_location_active(location_code PK, location_name, city, state, region, property_type, is_active)
""",
        "patterns": """
-- 12-month new hire retention:
SELECT
  COUNT(DISTINCT CASE WHEN is_active_current=1 THEN employee_id END) AS "Still Active",
  COUNT(DISTINCT employee_id) AS "Total in Cohort",
  ROUND(COUNT(DISTINCT CASE WHEN is_active_current=1 THEN employee_id END)*100.0/COUNT(DISTINCT employee_id),1) AS "12-Month Retention %"
FROM dim_employee
WHERE hire_date <= DATE('2026-06-09','-12 months') AND hire_date >= DATE('2026-06-09','-24 months')

-- Retention by job type:
SELECT job_type AS "Job Type",
  COUNT(DISTINCT CASE WHEN is_active_current=1 THEN employee_id END) AS "Retained",
  COUNT(DISTINCT employee_id) AS "Cohort Size",
  ROUND(COUNT(DISTINCT CASE WHEN is_active_current=1 THEN employee_id END)*100.0/COUNT(DISTINCT employee_id),1) AS "Retention %"
FROM dim_employee
WHERE hire_date <= DATE('2026-06-09','-12 months') AND hire_date >= DATE('2026-06-09','-24 months')
GROUP BY job_type

-- Early attrition (left within 90 days):
SELECT COUNT(*) AS "Left Within 90 Days",
  ROUND(COUNT(*)*100.0/(SELECT COUNT(*) FROM dim_employee WHERE hire_date >= DATE('2026-06-09','-12 months')),1) AS "% of New Hires"
FROM dim_employee
WHERE hire_date >= DATE('2026-06-09','-12 months')
AND termination_date IS NOT NULL
AND JULIANDAY(termination_date)-JULIANDAY(hire_date) <= 90
"""
    },
}


def _classify_topic(question: str, history: list = []) -> str:
    """Keyword classifier — no LLM call, no tokens spent."""
    ctx = question.lower()
    for h in (history or [])[-2:]:
        ctx += " " + h.question.lower()

    if any(w in ctx for w in ["exit interview", "leaving reason", "reason for leaving",
                               "why.*left", "would recommend", "manager rating",
                               "mgr rating", "exit survey", "why people leave"]):
        return "exit_interviews"

    if any(w in ctx for w in ["requisition", " req ", "open role", "open position",
                               "open job", "days to fill", "time to fill", "fill a role",
                               "published", "vacancy", "vacancies", "hiring pipeline"]):
        return "recruiting"

    if any(w in ctx for w in ["retention", "still with us", "still active", "new hire cohort",
                               "12 month retention", "12-month", "early attrition",
                               "90 day", "first 90", "onboard"]):
        return "retention"

    if any(w in ctx for w in ["terminat", "separat", "attrition", "turnover", "who left",
                               "left the company", "quit", "resign", "involuntary",
                               "voluntary exit", "separation", "people left", "who has left"]):
        return "separations"

    return "headcount"


def _build_sql_prompt(topic: str) -> str:
    ctx = _TOPIC_CONTEXT.get(topic, _TOPIC_CONTEXT["headcount"])
    return (
        f"{_GLOBAL_RULES}\n"
        f"Relevant tables for this question:\n{ctx['schema']}\n"
        f"Verified SQL patterns:\n{ctx['patterns']}"
    )


# Keep SCHEMA_DDL as reference (used by health endpoint)
SCHEMA_DDL = "\n".join(
    f"{t}({','.join(set(','.join(v['schema'].split()).split(',')))})"
    for t, v in _TOPIC_CONTEXT.items()
)

# SQL_SYSTEM_PROMPT replaced by _build_sql_prompt(topic) — see guard rails above


ANSWER_SYSTEM_PROMPT = (
    "You are PeopleIQ, a friendly workforce analytics assistant for a hospitality company. "
    "Turn SQL query results into clear, concise answers for a non-technical HR audience.\n"
    "- Write in complete sentences. Use plain English.\n"
    "- Never use SQL, column names, or technical jargon.\n"
    "- Be specific with numbers. Round percentages to one decimal place.\n"
    "- 'Property' employees work at hotel/property locations. 'Corporate' employees work at regional or HQ offices.\n"
    "- Manager ratings are on a 1-3 scale: 1=negative, 2=neutral, 3=positive. When reporting % positive, explain the scale briefly.\n"
    "- If results are empty, say so clearly and suggest a related question.\n"
    "- Keep answers under 150 words unless the data genuinely requires more.\n"
    "- Do not mention individual employee names under any circumstances.\n"
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
app = FastAPI(title="PeopleIQ API", version="2.4.0")

# Ensure query_log table exists on startup
_init_query_log()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class HistoryMessage(BaseModel):
    question: str
    answer: str


class ChatRequest(BaseModel):
    question: str
    history: list[HistoryMessage] = []


class ChatResponse(BaseModel):
    answer: str
    sql: Optional[str] = None
    row_count: int = 0
    chart_data: Optional[dict] = None
    output_type: str = "chart"          # "chart" | "table" | "kpi"
    rows: Optional[list] = None         # raw rows for table rendering


class SummaryMetric(BaseModel):
    key: str
    metric: str
    value_fmt: str
    status: str          # "good" | "watch" | "alert"
    headline: str
    detail: str
    question: str        # pre-written question for the Ask button


class SummaryResponse(BaseModel):
    generated_at: str
    metrics: list[SummaryMetric]


# ── Chart detection ───────────────────────────────────────────────────────────
def _detect_chart(rows: list[dict], row_count: int) -> Optional[dict]:
    """Return chart config when the result is naturally chartable, else None.
    Supports multi-series: finds all numeric columns after the label column.
    Returns: { type, labels, series: [{ name, values }] }
    """
    if row_count < 2 or not rows:
        return None
    keys = list(rows[0].keys())
    if len(keys) < 2:
        return None

    sample = rows[0]

    # Find first string-ish column → X-axis labels
    label_col = None
    for k in keys:
        v = sample.get(k)
        if isinstance(v, str) or (v is not None and not isinstance(v, (int, float))):
            label_col = k
            break
    if label_col is None:
        label_col = keys[0]

    def _is_id_col(k: str) -> bool:
        kl = k.lower().strip()
        return kl == "id" or kl.endswith(" id") or kl.endswith("_id") or kl == "person id"

    # Find ALL meaningful numeric columns → one series each
    value_cols = []
    for k in keys:
        if k == label_col or _is_id_col(k):
            continue
        try:
            float(rows[0][k])
            value_cols.append(k)
        except (TypeError, ValueError):
            continue

    if not value_cols:
        return None

    # Extract data (up to 20 rows)
    try:
        sample_rows = rows[:20]
        labels = [str(r[label_col]) for r in sample_rows]
        series = []
        for col in value_cols:
            values = [float(r[col]) if r[col] is not None else 0.0 for r in sample_rows]
            series.append({"name": col, "values": values})
    except (TypeError, ValueError):
        return None

    if len(series[0]["values"]) < 2:
        return None

    time_keywords = ("year", "date", "month", "quarter", "period", "week")
    chart_type = "line" if any(k in label_col.lower() for k in time_keywords) else "bar"

    return {
        "type": chart_type,
        "labels": labels,
        "series": series,
    }


# ── Output type router ───────────────────────────────────────────────────────
def _decide_output_type(rows: list[dict], row_count: int,
                        question: str, chart_data: Optional[dict]) -> str:
    """Decide how the frontend should render the result."""
    if row_count == 0:
        return "kpi"

    # Single aggregate number → KPI card
    if row_count == 1 and rows and len(rows[0]) == 1:
        return "kpi"

    # Location / property breakdowns → always table (30+ rows, bar chart is unreadable)
    location_words = ("property", "properties", "location", "locations",
                      "region", "state", "city", "site", "by propert")
    q_lower = question.lower()
    if any(w in q_lower for w in location_words) and row_count > 5:
        return "table"

    # More than 4 columns → table
    if rows and len(rows[0]) > 4:
        return "table"

    # More than 20 rows → table
    if row_count > 20:
        return "table"

    # No chartable structure → table
    if chart_data is None and row_count > 1:
        return "table"

    return "chart"


# ── Summary (6 canned metrics — uses new schema) ─────────────────────────────
def _compute_summary() -> list[dict]:
    TODAY = "2026-06-09"
    con = sqlite3.connect(DB_PATH)
    metrics = []

    try:
        # 1. Current headcount
        hc = con.execute(
            "SELECT COUNT(*) FROM dim_employee WHERE is_active_current=1"
        ).fetchone()[0]
        prop = con.execute(
            "SELECT COUNT(*) FROM dim_employee WHERE is_active_current=1 AND job_type='Property'"
        ).fetchone()[0]
        corp = hc - prop
        metrics.append(dict(
            key="headcount", metric="Current Headcount",
            value_fmt=f"{hc:,} employees",
            status="good",
            headline=f"Active headcount at {hc:,} employees",
            detail=f"{prop} property / {corp} corporate across all locations.",
            question="What is the split between property and corporate employees?",
        ))

        # 2. Rolling 12-month turnover
        terms_12m = con.execute(f"""
            SELECT COUNT(DISTINCT employee_id) FROM fact_employee_event
            WHERE event_type='Termination' AND event_date >= DATE('{TODAY}','-12 months')
        """).fetchone()[0]
        avg_hc_12m = con.execute(f"""
            SELECT COUNT(DISTINCT employee_id) FROM fact_employee_snapshot_monthly
            WHERE snapshot_date >= DATE('{TODAY}','-12 months') AND is_active=1
        """).fetchone()[0]
        turnover = round(terms_12m * 100.0 / avg_hc_12m, 1) if avg_hc_12m else 0
        vol_12m = con.execute(f"""
            SELECT COUNT(*) FROM fact_employee_event
            WHERE event_type='Termination' AND termination_type='Voluntary'
            AND event_date >= DATE('{TODAY}','-12 months')
        """).fetchone()[0]
        vol_pct = round(vol_12m * 100 / terms_12m) if terms_12m else 0
        metrics.append(dict(
            key="turnover", metric="Rolling 12M Turnover",
            value_fmt=f"{turnover}%",
            status="alert" if turnover > 40 else "watch" if turnover > 25 else "good",
            headline=f"Rolling 12-month turnover at {turnover}%",
            detail=f"{terms_12m} separations in past 12 months. {vol_pct}% voluntary.",
            question="What is our rolling 12-month turnover rate and which properties are highest?",
        ))

        # 3. 12-month new hire retention
        cohort_total = con.execute(f"""
            SELECT COUNT(*) FROM dim_employee
            WHERE hire_date <= DATE('{TODAY}','-12 months')
            AND hire_date >= DATE('{TODAY}','-24 months')
        """).fetchone()[0]
        cohort_retained = con.execute(f"""
            SELECT COUNT(*) FROM dim_employee
            WHERE hire_date <= DATE('{TODAY}','-12 months')
            AND hire_date >= DATE('{TODAY}','-24 months')
            AND is_active_current=1
        """).fetchone()[0]
        retention = round(cohort_retained * 100.0 / cohort_total, 1) if cohort_total else 0
        metrics.append(dict(
            key="retention", metric="12-Month Retention",
            value_fmt=f"{retention}%",
            status="good" if retention >= 70 else "watch" if retention >= 55 else "alert",
            headline=f"{retention}% of hires from 12–24 months ago are still active",
            detail=f"{cohort_retained} of {cohort_total} employees in that cohort retained.",
            question="What percentage of employees hired 12 months ago are still with us?",
        ))

        # 4. Open requisitions
        open_reqs = con.execute(
            "SELECT COUNT(*) FROM dim_requisition WHERE status='Published'"
        ).fetchone()[0]
        ttf = con.execute(
            "SELECT ROUND(AVG(days_publish_to_first_hire),1) FROM fact_requisition_fill"
        ).fetchone()[0] or 0
        metrics.append(dict(
            key="open_reqs", metric="Open Requisitions",
            value_fmt=f"{open_reqs} open",
            status="watch" if open_reqs > 50 else "good",
            headline=f"{open_reqs} positions currently open",
            detail=f"Average time to fill a role is {ttf} days.",
            question="Which properties have the most open requisitions right now?",
        ))

        # 5. Top exit reason
        top_reason = con.execute("""
            SELECT r.reason_name, COUNT(*) AS cnt
            FROM bridge_exit_reason b JOIN dim_exit_reason r ON b.reason_id=r.reason_id
            WHERE r.reason_name != 'Other'
            GROUP BY r.reason_name ORDER BY cnt DESC LIMIT 1
        """).fetchone()
        total_exits = con.execute("SELECT COUNT(*) FROM fact_exit_interview").fetchone()[0]
        reason_name = top_reason[0] if top_reason else "N/A"
        reason_cnt  = top_reason[1] if top_reason else 0
        reason_pct  = round(reason_cnt * 100.0 / total_exits, 1) if total_exits else 0
        metrics.append(dict(
            key="exit_reason", metric="Top Exit Reason",
            value_fmt=reason_name,
            status="watch",
            headline=f'"{reason_name}" is the leading reason for leaving',
            detail=f"Cited in {reason_pct}% of {total_exits} exit interviews.",
            question="What are the top reasons employees are leaving?",
        ))

        # 6. Manager rating
        mgr_positive = con.execute("""
            SELECT ROUND(AVG(
              (CAST(mgr_dimension_1-1 AS REAL) + CAST(mgr_dimension_2-1 AS REAL) +
               CAST(mgr_dimension_3-1 AS REAL) + CAST(mgr_dimension_4-1 AS REAL)) / 8.0
            ) * 100, 1) FROM fact_exit_interview
        """).fetchone()[0] or 0
        metrics.append(dict(
            key="mgr_rating", metric="Manager Rating",
            value_fmt=f"{mgr_positive}% positive",
            status="good" if mgr_positive >= 65 else "watch" if mgr_positive >= 50 else "alert",
            headline=f"Manager ratings {mgr_positive}% positive in exit surveys",
            detail="Based on 4 leadership dimensions rated by departing employees.",
            question="How are managers rated in exit interviews and which dimensions score lowest?",
        ))

    finally:
        con.close()

    return metrics


# ── Core functions ────────────────────────────────────────────────────────────
def _format_history(history: list) -> str:
    """Format last N Q&A turns into a compact context block for the prompt."""
    if not history:
        return ""
    lines = ["Recent conversation context (use this to resolve follow-up references):"]
    for i, h in enumerate(history[-3:], 1):  # last 3 turns max
        lines.append(f"Turn {i}:")
        lines.append(f"  Q: {h.question}")
        # Truncate long answers to save tokens
        answer_preview = h.answer[:300] + "…" if len(h.answer) > 300 else h.answer
        lines.append(f"  A: {answer_preview}")
    return "\n".join(lines)


def generate_sql(question: str, history: list = [], error_context: str = "", topic: str = "headcount") -> str:
    system_prompt = _build_sql_prompt(topic)
    history_block = _format_history(history)
    if error_context:
        user_content = (f"{history_block}\n\n" if history_block else "") + (
            f"Question: {question}\n\n"
            f"The previous SQL query failed with this error:\n{error_context}\n\n"
            "Please generate a corrected SQL query that avoids this error."
        )
    else:
        user_content = (f"{history_block}\n\n" if history_block else "") + f"Question: {question}"
    raw = call_groq(system_prompt, user_content)
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


def generate_answer(question: str, rows: list[dict], row_count: int, history: list = []) -> str:
    if not rows:
        results_text = "The query returned no results."
    elif row_count == 1:
        results_text = _rows_to_csv(rows)
    else:
        sample = rows[:10]
        results_text = _rows_to_csv(sample)
        if row_count > 10:
            results_text += f"\n... ({row_count} total rows, showing top 10)"
    history_block = _format_history(history)
    user_content = (
        f"{history_block}\n\n" if history_block else ""
    ) + f"Question: {question}\n\nResults ({row_count} row(s)):\n{results_text}"
    log.info(f"[answer] passing {min(row_count,10)}/{row_count} rows to LLM")
    return call_groq(ANSWER_SYSTEM_PROMPT, user_content)


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

    t_start = time.monotonic()
    error_context = ""
    last_sql = None

    history = request.history or []
    topic   = _classify_topic(question, history)
    log.info(f"[topic] {topic!r} for: {question!r}")

    for attempt in range(1, MAX_RETRIES + 1):
        log.info(f"[attempt {attempt}/{MAX_RETRIES}] Generating SQL (topic={topic})")
        try:
            sql = generate_sql(question, history, error_context, topic)
            last_sql = sql
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

        answer      = generate_answer(question, rows, row_count, history)
        chart_data  = _detect_chart(rows, row_count)
        output_type = _decide_output_type(rows, row_count, question, chart_data)
        latency_ms  = int((time.monotonic() - t_start) * 1000)
        log.info(f"Answer ({output_type}, {latency_ms}ms): {answer[:80]}...")
        _log_query(question, sql, row_count, True, None, latency_ms, chart_data)
        # Send raw rows to frontend only for table rendering (cap at 100)
        table_rows = rows[:100] if output_type == "table" else None
        return ChatResponse(
            answer=answer, sql=sql, row_count=row_count,
            chart_data=chart_data, output_type=output_type, rows=table_rows
        )

    latency_ms = int((time.monotonic() - t_start) * 1000)
    log.error(f"All {MAX_RETRIES} attempts failed for: {question!r}")
    _log_query(question, last_sql, 0, False, error_context or "All retries failed", latency_ms, None)
    return FALLBACK_RESPONSE

# ── Summary endpoint ─────────────────────────────────────────────────────────
@app.get("/summary", response_model=SummaryResponse)
async def summary():
    from datetime import date
    try:
        metrics = _compute_summary()
    except Exception as exc:
        log.error(f"Summary compute failed: {exc}")
        raise HTTPException(status_code=500, detail=f"Summary error: {exc}")
    return SummaryResponse(
        generated_at=date.today().isoformat(),
        metrics=[SummaryMetric(**m) for m in metrics],
    )


# ── Admin endpoints ───────────────────────────────────────────────────────────

@app.get("/admin/logs")
async def admin_logs(limit: int = 200, offset: int = 0):
    """Return query log entries, newest first."""
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            """SELECT id, asked_at, question, sql_generated, row_count,
                      success, error_msg, latency_ms, chart_data
               FROM query_log
               ORDER BY id DESC
               LIMIT ? OFFSET ?""",
            (limit, offset)
        ).fetchall()
        total = con.execute("SELECT COUNT(*) FROM query_log").fetchone()[0]
    finally:
        con.close()
    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "logs": [dict(r) for r in rows],
    }


@app.get("/admin/stats")
async def admin_stats():
    """Aggregate stats for the admin dashboard."""
    con = sqlite3.connect(DB_PATH)
    try:
        total      = con.execute("SELECT COUNT(*) FROM query_log").fetchone()[0]
        successes  = con.execute("SELECT COUNT(*) FROM query_log WHERE success=1").fetchone()[0]
        avg_lat    = con.execute("SELECT ROUND(AVG(latency_ms),0) FROM query_log WHERE success=1").fetchone()[0] or 0
        avg_rows   = con.execute("SELECT ROUND(AVG(row_count),1) FROM query_log WHERE success=1").fetchone()[0] or 0
        today      = con.execute(
            "SELECT COUNT(*) FROM query_log WHERE asked_at >= date('now')"
        ).fetchone()[0]
    finally:
        con.close()
    return {
        "total_queries": total,
        "success_count": successes,
        "fail_count": total - successes,
        "success_rate_pct": round(successes * 100 / total, 1) if total else 0,
        "avg_latency_ms": int(avg_lat),
        "avg_row_count": avg_rows,
        "queries_today": today,
    }


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
