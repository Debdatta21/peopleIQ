"""
PeopleIQ Phase 2 — FastAPI Backend
===================================
Single endpoint: POST /chat
  Input:  { "question": "string" }
  Output: { "answer": "string", "sql": "string|null", "row_count": int }

Architecture (four functions, called in order):
  1. generate_sql()   — question + schema → SQL (via Claude API)
  2. validate_sql()   — read-only safety check before execution
  3. execute_query()  — SQL → result rows (PII stripped before return)
  4. generate_answer() — result rows + question → plain-English answer (via Claude API)

Retry logic: on execute_query() failure, error is fed back into generate_sql()
and retried up to MAX_RETRIES times. On third failure a graceful fallback is returned.
"""

import os
import re
import sqlite3
import logging
from typing import Optional

import anthropic
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("peopleiq")

# ── Config ────────────────────────────────────────────────────────────────────

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# DB path: ../outputs/peopleiq_dev.db relative to this file
DB_PATH = os.getenv(
    "PEOPLEIQ_DB_PATH",
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "outputs",
        "peopleiq_dev.db",
    ),
)

MODEL       = "claude-sonnet-4-5"   # Text-to-SQL model (update as needed)
MAX_RETRIES = 3

# PII column names — stripped from all result sets before they touch the LLM
PII_COLUMNS = {"full_name", "email", "first_name", "last_name"}

# ── Schema extraction (runs once at startup) ──────────────────────────────────

def _extract_schema_ddl() -> str:
    """Pull CREATE TABLE statements from SQLite and return as a single string."""
    if not os.path.exists(DB_PATH):
        log.warning(f"Database not found at {DB_PATH}. Run generate_data.py first.")
        return ""
    con = sqlite3.connect(DB_PATH)
    cur = con.execute(
        "SELECT name, sql FROM sqlite_master "
        "WHERE type='table' AND sql IS NOT NULL "
        "ORDER BY name"
    )
    rows = cur.fetchall()
    con.close()
    return "\n\n".join(sql for _, sql in rows)


SCHEMA_DDL = _extract_schema_ddl()

SYSTEM_PROMPT_SQL = f"""You are the Text-to-SQL engine for PeopleIQ, a workforce intelligence platform.

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

# ── FastAPI app ────────────────────────────────────────────────────────────────

app = FastAPI(title="PeopleIQ API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "https://*.vercel.app",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Anthropic client (lazy init — missing key raises at request time, not startup)
def _get_client() -> anthropic.Anthropic:
    if not ANTHROPIC_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="ANTHROPIC_API_KEY is not configured. Add it to backend/.env",
        )
    return anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


# ── Request / Response models ─────────────────────────────────────────────────

class ChatRequest(BaseModel):
    question: str

class ChatResponse(BaseModel):
    answer: str
    sql: Optional[str] = None
    row_count: int = 0


# ── Core functions ─────────────────────────────────────────────────────────────

def generate_sql(question: str, error_context: str = "") -> str:
    """
    Step 1 — Send question (+ optional prior error) to Claude.
    Returns a raw SQL string.
    """
    client = _get_client()

    user_content = question
    if error_context:
        user_content = (
            f"Question: {question}\n\n"
            f"The previous SQL query failed with this error:\n{error_context}\n\n"
            "Please generate a corrected SQL query that avoids this error."
        )

    msg = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        system=SYSTEM_PROMPT_SQL,
        messages=[{"role": "user", "content": user_content}],
    )

    raw = msg.content[0].text.strip()
    # Strip markdown code fences if the model includes them despite instruction
    raw = re.sub(r"^```(?:sql)?\s*\n?", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"\s*```$", "", raw)
    return raw.strip()


def validate_sql(sql: str) -> tuple[bool, str]:
    """
    Step 2 — Confirm the query is a read-only SELECT.
    Returns (is_valid: bool, reason: str).
    """
    cleaned = sql.strip().upper()

    if not cleaned.startswith("SELECT"):
        return False, "Query does not begin with SELECT"

    dangerous_keywords = [
        "INSERT", "UPDATE", "DELETE", "DROP", "CREATE", "ALTER",
        "TRUNCATE", "REPLACE", "ATTACH", "DETACH", "PRAGMA",
        "--", ";--",   # injection patterns
    ]
    for kw in dangerous_keywords:
        if re.search(rf"\b{re.escape(kw)}\b", cleaned):
            return False, f"Query contains forbidden keyword: {kw}"

    return True, "ok"


def execute_query(sql: str) -> tuple[list[dict], int]:
    """
    Step 3 — Run SQL against peopleiq_dev.db.
    Strips PII columns before returning.
    Returns (rows_as_dicts, row_count).
    Raises sqlite3.Error on failure (caller handles retry).
    """
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
    """
    Step 4 — Send result set + original question to Claude.
    Returns a plain-English answer written for a non-technical HR audience.
    """
    client = _get_client()

    # Trim result payload — max 50 rows to avoid token overrun
    results_text = str(rows[:50]) if rows else "The query returned no results."

    msg = client.messages.create(
        model=MODEL,
        max_tokens=512,
        system=(
            "You are PeopleIQ, a friendly workforce analytics assistant. "
            "Your job is to turn SQL query results into clear, concise answers "
            "written for a non-technical HR audience. Follow these rules:\n"
            "- Write in complete sentences. Use plain English.\n"
            "- Never use SQL, column names, or technical jargon.\n"
            "- Be specific with numbers. Round percentages to one decimal place.\n"
            "- If results are empty, say so clearly and suggest a related question.\n"
            "- Keep answers under 150 words unless the data genuinely requires more.\n"
            "- Do not mention employee names under any circumstances."
        ),
        messages=[
            {
                "role": "user",
                "content": (
                    f"Question asked: {question}\n\n"
                    f"Query returned {row_count} row(s):\n{results_text}"
                ),
            }
        ],
    )
    return msg.content[0].text.strip()


# ── /chat endpoint ─────────────────────────────────────────────────────────────

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
    last_sql = None

    for attempt in range(1, MAX_RETRIES + 1):
        log.info(f"[attempt {attempt}/{MAX_RETRIES}] Generating SQL for: {question!r}")

        # Step 1 — Generate SQL
        sql = generate_sql(question, error_context)
        last_sql = sql
        log.info(f"Generated SQL: {sql}")

        # Step 2 — Validate SQL
        is_valid, reason = validate_sql(sql)
        if not is_valid:
            error_context = f"SQL validation failed: {reason}. Query was: {sql}"
            log.warning(f"Attempt {attempt}: validation failed — {reason}")
            continue

        # Step 3 — Execute query
        try:
            rows, row_count = execute_query(sql)
        except Exception as exc:
            error_context = str(exc)
            log.warning(f"Attempt {attempt}: execute failed — {exc}")
            continue

        # Step 4 — Generate human-readable answer
        answer = generate_answer(question, rows, row_count)
        log.info(f"Answer: {answer[:120]}...")

        return ChatResponse(answer=answer, sql=sql, row_count=row_count)

    log.error(f"All {MAX_RETRIES} attempts failed for question: {question!r}")
    return FALLBACK_RESPONSE


# ── Health check ───────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    db_exists = os.path.exists(DB_PATH)
    table_count = len(re.findall(r"CREATE TABLE", SCHEMA_DDL, re.IGNORECASE))
    return {
        "status": "ok",
        "db_path": DB_PATH,
        "db_exists": db_exists,
        "schema_tables_loaded": table_count,
        "model": MODEL,
    }
