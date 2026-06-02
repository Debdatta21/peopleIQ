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
MODEL = "llama-3.3-70b-versatile"
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
def _extract_schema_ddl() -> str:
    if not os.path.exists(DB_PATH):
        log.warning(f"Database not found at {DB_PATH}. Run generate_data.py first.")
        return ""
    con = sqlite3.connect(DB_PATH)
    cur = con.execute(
        "SELECT name, sql FROM sqlite_master "
        "WHERE type='table' AND sql IS NOT NULL ORDER BY name"
    )
    rows = cur.fetchall()
    con.close()
    return "\n\n".join(sql for _, sql in rows)


SCHEMA_DDL = _extract_schema_ddl()

SQL_SYSTEM_PROMPT = f"""You are the Text-to-SQL engine for PeopleIQ, a workforce intelligence platform.
Convert the user's natural language HR question into a single valid SQLite SELECT query.

Database schema (SQLite):
{SCHEMA_DDL}

Hard rules — follow every one, no exceptions:
1. Output ONLY the raw SQL query. No markdown fences, no explanation, no comments.
2. Use only SELECT. Never use INSERT, UPDATE, DELETE, DROP, CREATE, ALTER, TRUNCATE, PRAGMA, or ATTACH.
3. Never reference columns: full_name, email (PII — always excluded).
4. Use clear column aliases (e.g. AS "Headcount", AS "Attrition Rate %") so results are self-explanatory.
5. Always LIMIT results to 100 rows unless the question asks for a specific smaller count.
6. The dim_date spine is 2019-01-01 to 2025-12-31. Interpret dates as follows:
   - "today" / "current" / "now"  → use the latest available date: 2025-12-31
   - "this year"                  → year = 2025
   - "last year"                  → year = 2024
   - "this quarter" / "Q4"        → quarter = 4 AND year = 2025
   - "last quarter" / "Q3"        → quarter = 3 AND year = 2025

Key query patterns:
- Current headcount: COUNT(*) FROM fact_headcount_snapshot WHERE date_id = (SELECT MAX(date_id) FROM fact_headcount_snapshot WHERE is_active = 1)
- Attrition rate for a year: (terminated / avg_active_headcount) * 100, both for that year
- Terminations: fact_employment_event WHERE event_type = 'Termination'
- Time to fill: AVG(days_to_fill) FROM fact_requisition WHERE status = 'Filled'
- Pipeline funnel: GROUP BY stage_name, COUNT and conversion rate from fact_recruiting_pipeline
- Always join dim_position, dim_org_unit, dim_work_location for readable names in GROUP BY queries
"""

ANSWER_SYSTEM_PROMPT = (
    "You are PeopleIQ, a friendly workforce analytics assistant. "
    "Turn SQL query results into clear, concise answers for a non-technical HR audience.\n"
    "- Write in complete sentences. Use plain English.\n"
    "- Never use SQL, column names, or technical jargon.\n"
    "- Be specific with numbers. Round percentages to one decimal place.\n"
    "- If results are empty, say so clearly and suggest a related question.\n"
    "- Keep answers under 150 words unless the data genuinely requires more.\n"
    "- Do not mention employee names under any circumstances."
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
    resp = requests.post(GROQ_URL, headers=headers, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"].strip()


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


def generate_answer(question: str, rows: list[dict], row_count: int) -> str:
    results_text = str(rows[:50]) if rows else "The query returned no results."
    return call_groq(
        ANSWER_SYSTEM_PROMPT,
        f"Question asked: {question}\n\nQuery returned {row_count} row(s):\n{results_text}"
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
    table_count = len(re.findall(r"CREATE TABLE", SCHEMA_DDL, re.IGNORECASE))
    return {
        "status": "ok",
        "db_exists": db_exists,
        "schema_tables_loaded": table_count,
        "model": MODEL,
    }
