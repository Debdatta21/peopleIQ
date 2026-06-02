#!/usr/bin/env python3
"""
generate_data.py — PeopleIQ Phase 1 Synthetic Data Generator
============================================================
Produces 12 CSVs + peopleiq_dev.db in /outputs/

Tables:
  Dims : dim_company, dim_date, dim_position, dim_org_unit,
         dim_work_location, dim_person
  Facts: fact_position_assignment, fact_employment_event,
         fact_headcount_snapshot, fact_requisition,
         fact_recruiting_pipeline, fact_exit_interview

Realism constraints implemented:
  - 500 employees, 30 locations, 20 org units, 40 positions
  - Annual attrition 12-18%  (monthly baseline hazard ~1.3%)
  - Early-attrition spike: 3× hazard in first 90 days
  - 70 % voluntary / 30 % involuntary terminations
  - Right-skewed tenure: exponential recency bias in hire dates
  - Seasonal hiring: Q1 + Q3 weighted higher
  - dim_date spine: 2019-01-01 → 2025-12-31
  - All events ≤ today (2026-05-31) — but events capped to spine end
  - Zero orphan FK violations
"""

import os, sys, math, random, sqlite3
from datetime import date, timedelta
from collections import defaultdict

import numpy as np
import pandas as pd
from faker import Faker

# ── Globals ──────────────────────────────────────────────────────────────────
SEED = 42
random.seed(SEED);  np.random.seed(SEED)
fake = Faker("en_US");  Faker.seed(SEED)

SPINE_START  = date(2019, 1, 1)
SPINE_END    = date(2025, 12, 31)
TODAY        = date(2025, 12, 31)   # cap events to spine end (≤ today per PRD)
COMPANY_ID   = 1

OUTPUT_DIR   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outputs")
os.makedirs(OUTPUT_DIR, exist_ok=True)
DB_PATH = os.path.join(OUTPUT_DIR, "peopleiq_dev.db")

# ── Date helpers ──────────────────────────────────────────────────────────────

def iter_dates(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)

def month_end_dates(start: date, end: date):
    """Yield the last calendar day of each month that falls within [start, end]."""
    y, m = start.year, start.month
    while True:
        if m == 12:
            last = date(y, 12, 31)
            ny, nm = y + 1, 1
        else:
            last = date(y, m + 1, 1) - timedelta(days=1)
            ny, nm = y, m + 1
        if last > end:
            break
        if last >= start:
            yield last
        y, m = ny, nm

def to_date_id(d: date) -> int:
    return d.year * 10000 + d.month * 100 + d.day

def month_add(d: date, n: int) -> date:
    """Add n months to d (clamp day to valid range)."""
    total = d.month - 1 + n
    y = d.year + total // 12
    m = total % 12 + 1
    # last valid day
    if m == 12:
        max_day = 31
    else:
        max_day = (date(y, m + 1, 1) - timedelta(days=1)).day
    return date(y, m, min(d.day, max_day))

_MONTH_NAMES = [
    "January","February","March","April","May","June",
    "July","August","September","October","November","December",
]

_MONTH_WEIGHTS = [
    1.40, 1.30, 1.20,   # Q1 — elevated
    0.75, 0.70, 0.75,   # Q2
    1.30, 1.20, 1.10,   # Q3 — elevated
    0.75, 0.65, 0.70,   # Q4
]
_MONTH_W_ARR = np.array(_MONTH_WEIGHTS) / sum(_MONTH_WEIGHTS)

def seasonal_date(year: int) -> date:
    """Return a hire date in `year` with Q1/Q3 seasonal weighting."""
    m = int(np.random.choice(range(1, 13), p=_MONTH_W_ARR))
    if m == 12:
        max_d = 31
    elif m in (4, 6, 9, 11):
        max_d = 30
    elif m == 2:
        max_d = 29 if (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)) else 28
    else:
        max_d = 31
    return date(year, m, random.randint(1, max_d))


# ── 1. dim_company ────────────────────────────────────────────────────────────

def build_dim_company() -> pd.DataFrame:
    return pd.DataFrame([{
        "company_id":   COMPANY_ID,
        "company_name": "Meridian Services Group",
        "company_code": "MSG",
    }])


# ── 2. dim_date ───────────────────────────────────────────────────────────────

def build_dim_date() -> pd.DataFrame:
    rows = []
    for d in iter_dates(SPINE_START, SPINE_END):
        if d.month == 12:
            nxt = date(d.year + 1, 1, 1)
        else:
            nxt = date(d.year, d.month + 1, 1)
        is_me = (d + timedelta(days=1) == nxt)
        rows.append({
            "date_id":      to_date_id(d),
            "full_date":    d.isoformat(),
            "year":         d.year,
            "quarter":      (d.month - 1) // 3 + 1,
            "month":        d.month,
            "month_name":   _MONTH_NAMES[d.month - 1],
            "day_of_week":  d.strftime("%A"),
            "is_weekend":   int(d.weekday() >= 5),
            "is_month_end": int(is_me),
            "fiscal_year":  d.year,   # fiscal = calendar year, Phase 1
        })
    return pd.DataFrame(rows)


# ── 3. dim_position ───────────────────────────────────────────────────────────

_POSITIONS_DEF = [
    # (title, family, level)
    # ── Operations ── 7
    ("Operations Director",            "Operations",      "Director"),
    ("Operations Manager",             "Operations",      "Manager"),
    ("Site Supervisor",                "Operations",      "Manager"),
    ("Operations Coordinator",         "Operations",      "Individual Contributor"),
    ("Operations Analyst",             "Operations",      "Individual Contributor"),
    ("Process Improvement Specialist", "Operations",      "Individual Contributor"),
    ("Field Operations Specialist",    "Operations",      "Individual Contributor"),
    # ── Human Resources ── 7
    ("HR Director",                    "Human Resources", "Director"),
    ("HR Business Partner",            "Human Resources", "Individual Contributor"),
    ("HR Generalist",                  "Human Resources", "Individual Contributor"),
    ("HR Coordinator",                 "Human Resources", "Individual Contributor"),
    ("Talent Acquisition Specialist",  "Human Resources", "Individual Contributor"),
    ("People Analytics Lead",          "Human Resources", "Individual Contributor"),
    ("Compensation Analyst",           "Human Resources", "Individual Contributor"),
    # ── Finance ── 6
    ("Controller",                     "Finance",         "Senior Manager"),
    ("Finance Manager",                "Finance",         "Manager"),
    ("Senior Financial Analyst",       "Finance",         "Individual Contributor"),
    ("Financial Analyst",              "Finance",         "Individual Contributor"),
    ("FP&A Analyst",                   "Finance",         "Individual Contributor"),
    ("Accounts Payable Specialist",    "Finance",         "Individual Contributor"),
    # ── Technology ── 7
    ("VP of Technology",               "Technology",      "VP"),
    ("Engineering Manager",            "Technology",      "Manager"),
    ("Senior Software Engineer",       "Technology",      "Individual Contributor"),
    ("Software Engineer",              "Technology",      "Individual Contributor"),
    ("Data Engineer",                  "Technology",      "Individual Contributor"),
    ("Systems Administrator",          "Technology",      "Individual Contributor"),
    ("IT Support Specialist",          "Technology",      "Individual Contributor"),
    # ── Sales & Marketing ── 7
    ("VP of Sales",                    "Sales & Marketing","VP"),
    ("Business Development Manager",   "Sales & Marketing","Manager"),
    ("Account Manager",                "Sales & Marketing","Individual Contributor"),
    ("Sales Representative",           "Sales & Marketing","Individual Contributor"),
    ("Customer Success Manager",       "Sales & Marketing","Manager"),
    ("Marketing Coordinator",          "Sales & Marketing","Individual Contributor"),
    ("Marketing Analyst",              "Sales & Marketing","Individual Contributor"),
    # ── Executive ── 6
    ("Chief Executive Officer",        "Executive",       "C-Suite"),
    ("Chief Operating Officer",        "Executive",       "C-Suite"),
    ("Chief Financial Officer",        "Executive",       "C-Suite"),
    ("VP of Operations",               "Executive",       "VP"),
    ("VP of Human Resources",          "Executive",       "VP"),
    ("Chief People Officer",           "Executive",       "C-Suite"),
]   # 40 total

def build_dim_position() -> pd.DataFrame:
    rows = []
    for i, (title, family, level) in enumerate(_POSITIONS_DEF, 1):
        rows.append({
            "position_id":    i,
            "position_title": title,
            "job_family":     family,
            "job_level":      level,
            "company_id":     COMPANY_ID,
        })
    return pd.DataFrame(rows)


# ── 4. dim_org_unit ───────────────────────────────────────────────────────────

def build_dim_org_unit() -> pd.DataFrame:
    # 3-level: Division (1) → Department (2) → Team (3)
    units = [
        (1,  "Corporate Services",         None, 1),
        (2,  "Human Resources",            1,    2),
        (3,  "Finance & Accounting",       1,    2),
        (4,  "Information Technology",     1,    2),
        (5,  "HR Operations Team",         2,    3),
        (6,  "Talent Acquisition Team",    2,    3),
        (7,  "Financial Planning Team",    3,    3),
        (8,  "Accounting Team",            3,    3),
        (9,  "IT Infrastructure Team",     4,    3),
        (10, "Software Development Team",  4,    3),
        (11, "Field Operations",           None, 1),
        (12, "Site Operations - East",     11,   2),
        (13, "Site Operations - West",     11,   2),
        (14, "Site Operations - Central",  11,   2),
        (15, "Operations Support",         11,   2),
        (16, "East Region Alpha Team",     12,   3),
        (17, "East Region Beta Team",      12,   3),
        (18, "Revenue & Growth",           None, 1),
        (19, "Sales & Business Dev",       18,   2),
        (20, "Marketing & Communications", 18,   2),
    ]
    rows = []
    for uid, name, parent, level in units:
        rows.append({
            "org_unit_id":        uid,
            "org_unit_name":      name,
            "parent_org_unit_id": parent,
            "org_level":          level,
            "company_id":         COMPANY_ID,
        })
    return pd.DataFrame(rows)


# ── 5. dim_work_location ──────────────────────────────────────────────────────

def build_dim_work_location() -> pd.DataFrame:
    locs = [
        (1,  "Atlanta HQ",           "Atlanta",       "GA", "Southeast", "On-site"),
        (2,  "Chicago Office",       "Chicago",       "IL", "Midwest",   "On-site"),
        (3,  "New York City",        "New York",      "NY", "Northeast", "On-site"),
        (4,  "Los Angeles Office",   "Los Angeles",   "CA", "West",      "Hybrid"),
        (5,  "Dallas Site A",        "Dallas",        "TX", "South",     "On-site"),
        (6,  "Dallas Site B",        "Dallas",        "TX", "South",     "On-site"),
        (7,  "Phoenix Operations",   "Phoenix",       "AZ", "West",      "On-site"),
        (8,  "Denver Office",        "Denver",        "CO", "West",      "Hybrid"),
        (9,  "Seattle Office",       "Seattle",       "WA", "West",      "Hybrid"),
        (10, "Boston Office",        "Boston",        "MA", "Northeast", "Hybrid"),
        (11, "Miami Operations",     "Miami",         "FL", "Southeast", "On-site"),
        (12, "Nashville Site",       "Nashville",     "TN", "Southeast", "On-site"),
        (13, "Detroit Site",         "Detroit",       "MI", "Midwest",   "On-site"),
        (14, "Minneapolis Office",   "Minneapolis",   "MN", "Midwest",   "On-site"),
        (15, "Columbus Site",        "Columbus",      "OH", "Midwest",   "On-site"),
        (16, "Portland Office",      "Portland",      "OR", "West",      "Hybrid"),
        (17, "Las Vegas Operations", "Las Vegas",     "NV", "West",      "On-site"),
        (18, "Raleigh Office",       "Raleigh",       "NC", "Southeast", "Hybrid"),
        (19, "Baltimore Site",       "Baltimore",     "MD", "Northeast", "On-site"),
        (20, "Pittsburgh Ops",       "Pittsburgh",    "PA", "Northeast", "On-site"),
        (21, "San Antonio Site",     "San Antonio",   "TX", "South",     "On-site"),
        (22, "Austin Office",        "Austin",        "TX", "South",     "Hybrid"),
        (23, "Kansas City Site",     "Kansas City",   "MO", "Midwest",   "On-site"),
        (24, "St. Louis Operations", "St. Louis",     "MO", "Midwest",   "On-site"),
        (25, "Indianapolis Site",    "Indianapolis",  "IN", "Midwest",   "On-site"),
        (26, "Charlotte Office",     "Charlotte",     "NC", "Southeast", "Hybrid"),
        (27, "San Diego Office",     "San Diego",     "CA", "West",      "Hybrid"),
        (28, "Sacramento Site",      "Sacramento",    "CA", "West",      "On-site"),
        (29, "Remote - East",        "Virtual",       "N/A","Northeast", "Remote"),
        (30, "Remote - West",        "Virtual",       "N/A","West",      "Remote"),
    ]
    rows = []
    for lid, name, city, state, region, ltype in locs:
        rows.append({
            "location_id":   lid,
            "location_name": name,
            "city":          city,
            "state":         state,
            "region":        region,
            "location_type": ltype,
            "company_id":    COMPANY_ID,
        })
    return pd.DataFrame(rows)


# ── 6. dim_person + position/org/location assignment scaffolding ──────────────

# Map job family → candidate org units
_FAM_ORGS = {
    "Operations":      [11, 12, 13, 14, 15, 16, 17],
    "Human Resources": [2, 5, 6],
    "Finance":         [3, 7, 8],
    "Technology":      [4, 9, 10],
    "Sales & Marketing": [18, 19, 20],
    "Executive":       [1],
}
# Map job family → candidate location ids (weighted toward fewer exec sites)
_FAM_LOCS = {
    "Operations":      list(range(1, 29)),
    "Human Resources": [1, 2, 3, 8, 10, 18, 29, 30],
    "Finance":         [1, 2, 3, 10, 29, 30],
    "Technology":      [1, 4, 8, 9, 16, 29, 30],
    "Sales & Marketing": list(range(1, 31)),
    "Executive":       [1],
}

def _pick_position(pos_df: pd.DataFrame) -> dict:
    """Pick a random position row, weighted toward IC roles."""
    weights = pos_df["job_level"].map({
        "Individual Contributor": 6,
        "Manager":               2,
        "Senior Manager":        1,
        "Director":              1,
        "VP":                    0.4,
        "C-Suite":               0.2,
    }).fillna(1).values
    weights = weights / weights.sum()
    idx = np.random.choice(len(pos_df), p=weights)
    return pos_df.iloc[idx].to_dict()


def build_dim_person(pos_df: pd.DataFrame):
    """
    Simulate 500 employees through a monthly-hazard survival model.
    Returns (person_df, person_records) where person_records is a list
    of dicts containing date objects needed for fact-table generation.
    """
    # Year weights for hire dates: exponential recency bias (right-skewed tenure)
    years = list(range(2019, 2026))   # 2019-2025 (within spine)
    year_w = np.array([math.exp(0.55 * i) for i in range(len(years))], dtype=float)
    year_w /= year_w.sum()

    MONTHLY_BASE  = 0.012    # baseline monthly hazard  → ~14% annual
    EARLY_MULT    = 2.5      # first-90-day spike
    EARLY_CUTOFF  = 90       # days
    VOL_PROB      = 0.70     # post-processed to enforce exactly 70/30

    emp_type_pool = (["Full-Time"] * 70 +
                     ["Part-Time"] * 15 +
                     ["Contractor"] * 15)

    person_rows = []
    person_records = []    # internal list with date objects

    for pid in range(1, 501):
        # ── hire date ──────────────────────────────────────────────────────
        yr = int(np.random.choice(years, p=year_w))
        hire_date = seasonal_date(yr)
        if hire_date > SPINE_END:
            hire_date = SPINE_END

        # ── survival simulation ────────────────────────────────────────────
        term_date = None
        cur = hire_date
        while cur < TODAY:
            days_in = (cur - hire_date).days
            hazard = MONTHLY_BASE * (EARLY_MULT if days_in < EARLY_CUTOFF else 1.0)
            if random.random() < hazard:
                offset = random.randint(0, 29)
                td = cur + timedelta(days=offset)
                if td > TODAY:
                    td = TODAY
                term_date = td
                break
            cur = month_add(cur, 1)

        status    = "Terminated" if term_date else "Active"
        term_type = ("Voluntary" if random.random() < VOL_PROB else "Involuntary") if term_date else None
        emp_type  = random.choice(emp_type_pool)

        # ── initial position ───────────────────────────────────────────────
        pos = _pick_position(pos_df)
        family    = pos["job_family"]
        org_id    = random.choice(_FAM_ORGS[family])
        loc_id    = random.choice(_FAM_LOCS[family])

        person_rows.append({
            "person_id":        pid,
            "full_name":        fake.name(),
            "email":            fake.email(),
            "status":           status,
            "hire_date":        hire_date.isoformat(),
            "termination_date": term_date.isoformat() if term_date else None,
            "termination_type": term_type,
            "employment_type":  emp_type,
            "company_id":       COMPANY_ID,
        })
        person_records.append({
            "person_id":   pid,
            "hire_date":   hire_date,
            "term_date":   term_date,
            "status":      status,
            "term_type":   term_type,
            "emp_type":    emp_type,
            "pos_id":      int(pos["position_id"]),
            "family":      family,
            "org_id":      org_id,
            "loc_id":      loc_id,
        })

    # ── Enforce 70/30 vol/invol split precisely ────────────────────────────────
    termed_indices = [i for i, r in enumerate(person_records) if r["term_date"]]
    n_termed = len(termed_indices)
    if n_termed > 0:
        target_vol   = round(n_termed * 0.70)
        target_invol = n_termed - target_vol
        random.shuffle(termed_indices)
        for k, idx in enumerate(termed_indices):
            ttype = "Voluntary" if k < target_vol else "Involuntary"
            person_records[idx]["term_type"] = ttype
            person_rows[idx]["termination_type"] = ttype

    return pd.DataFrame(person_rows), person_records


# ── 7. fact_position_assignment ───────────────────────────────────────────────

def build_fact_position_assignment(person_records: list, pos_df: pd.DataFrame):
    """
    Every person has 1-3 position assignments.
    ~20% of employees get a transfer/promotion; ~5% get a second one.
    Returns (assignment_df, assignment_lookup) where lookup maps
    person_id → sorted list of (eff_start, eff_end_or_None, pos_id, org_id, loc_id).
    """
    rows = []
    assignment_id = 1
    lookup = defaultdict(list)   # person_id → list of assignment dicts

    for pr in person_records:
        pid       = pr["person_id"]
        hire      = pr["hire_date"]
        term      = pr["term_date"]
        end_of_life = term if term else TODAY

        # Initial assignment
        segments = [(hire, pr["pos_id"], pr["org_id"], pr["loc_id"])]

        # Employment length in months
        tenure_months = max(1, round((end_of_life - hire).days / 30.44))

        # Possible transfer/promotion (need ≥ 3 months tenure, not in early spike)
        if tenure_months >= 3 and random.random() < 0.22:
            # transfer date: somewhere between 3 months and end-1-month
            t1_months = random.randint(3, max(3, tenure_months - 1))
            t1_date   = month_add(hire, t1_months)
            if t1_date < end_of_life:
                new_pos  = _pick_position(pos_df)
                new_fam  = new_pos["job_family"]
                new_org  = random.choice(_FAM_ORGS[new_fam])
                new_loc  = random.choice(_FAM_LOCS[new_fam])
                segments.append((t1_date, int(new_pos["position_id"]), new_org, new_loc))

                # Second transfer (rarer)
                if tenure_months >= 6 and random.random() < 0.22:
                    t2_months = random.randint(t1_months + 2, max(t1_months + 2, tenure_months - 1))
                    t2_date   = month_add(hire, t2_months)
                    if t2_date < end_of_life:
                        np2    = _pick_position(pos_df)
                        nf2    = np2["job_family"]
                        no2    = random.choice(_FAM_ORGS[nf2])
                        nl2    = random.choice(_FAM_LOCS[nf2])
                        segments.append((t2_date, int(np2["position_id"]), no2, nl2))

        # Sort segments by start date
        segments.sort(key=lambda x: x[0])

        for k, (seg_start, pos_id, org_id, loc_id) in enumerate(segments):
            is_last = (k == len(segments) - 1)
            if is_last:
                eff_end  = term.isoformat() if term else None
                is_cur   = 1 if not term else 0
            else:
                next_start = segments[k + 1][0]
                eff_end  = (next_start - timedelta(days=1)).isoformat()
                is_cur   = 0

            rows.append({
                "assignment_id":  assignment_id,
                "person_id":      pid,
                "position_id":    pos_id,
                "org_unit_id":    org_id,
                "location_id":    loc_id,
                "company_id":     COMPANY_ID,
                "effective_start": seg_start.isoformat(),
                "effective_end":  eff_end,
                "is_current":     is_cur,
            })
            lookup[pid].append({
                "start":   seg_start,
                "end":     date.fromisoformat(eff_end) if eff_end else None,
                "pos_id":  pos_id,
                "org_id":  org_id,
                "loc_id":  loc_id,
            })
            assignment_id += 1

    return pd.DataFrame(rows), lookup


def assignment_on_date(lookup: dict, person_id: int, d: date):
    """Return (pos_id, org_id, loc_id) for person on date d."""
    segs = lookup.get(person_id, [])
    for seg in reversed(segs):   # latest first
        if seg["start"] <= d:
            if seg["end"] is None or seg["end"] >= d:
                return seg["pos_id"], seg["org_id"], seg["loc_id"]
    # fallback: first segment
    if segs:
        return segs[0]["pos_id"], segs[0]["org_id"], segs[0]["loc_id"]
    return 1, 1, 1   # should never happen


# ── 8. fact_employment_event ──────────────────────────────────────────────────

_EVENT_COUNTER = [1]

def _next_event_id():
    eid = _EVENT_COUNTER[0]
    _EVENT_COUNTER[0] += 1
    return eid

def build_fact_employment_event(person_records: list, lookup: dict,
                                date_id_set: set) -> pd.DataFrame:
    rows = []
    for pr in person_records:
        pid  = pr["person_id"]
        hire = pr["hire_date"]

        # ── HIRE event ──────────────────────────────────────────────────────
        if to_date_id(hire) in date_id_set:
            pos_id, org_id, loc_id = assignment_on_date(lookup, pid, hire)
            rows.append({
                "event_id":           _next_event_id(),
                "person_id":          pid,
                "date_id":            to_date_id(hire),
                "event_date":         hire.isoformat(),
                "event_type":         "Hire",
                "termination_type":   None,
                "tenure_days_at_event": 0,
                "position_id":        pos_id,
                "org_unit_id":        org_id,
                "location_id":        loc_id,
                "company_id":         COMPANY_ID,
            })

        # ── TRANSFER events (one per extra segment) ─────────────────────────
        segs = lookup.get(pid, [])
        for seg in segs[1:]:    # skip the first (hire) segment
            t_date = seg["start"]
            if to_date_id(t_date) not in date_id_set:
                continue
            tenure_days = (t_date - hire).days
            rows.append({
                "event_id":           _next_event_id(),
                "person_id":          pid,
                "date_id":            to_date_id(t_date),
                "event_date":         t_date.isoformat(),
                "event_type":         "Transfer",
                "termination_type":   None,
                "tenure_days_at_event": tenure_days,
                "position_id":        seg["pos_id"],
                "org_unit_id":        seg["org_id"],
                "location_id":        seg["loc_id"],
                "company_id":         COMPANY_ID,
            })

        # ── TERMINATION event ───────────────────────────────────────────────
        term = pr["term_date"]
        if term and to_date_id(term) in date_id_set:
            tenure_days = (term - hire).days
            pos_id, org_id, loc_id = assignment_on_date(lookup, pid, term)
            rows.append({
                "event_id":           _next_event_id(),
                "person_id":          pid,
                "date_id":            to_date_id(term),
                "event_date":         term.isoformat(),
                "event_type":         "Termination",
                "termination_type":   pr["term_type"],
                "tenure_days_at_event": tenure_days,
                "position_id":        pos_id,
                "org_unit_id":        org_id,
                "location_id":        loc_id,
                "company_id":         COMPANY_ID,
            })

    return pd.DataFrame(rows)


# ── 9. fact_headcount_snapshot ────────────────────────────────────────────────

_TENURE_BANDS = [
    (0,   90,   "0-90 days"),
    (91,  182,  "91-182 days"),
    (183, 365,  "6-12 months"),
    (366, 730,  "1-2 years"),
    (731, 1095, "2-3 years"),
    (1096,1825, "3-5 years"),
    (1826,3650, "5-10 years"),
    (3651,99999,"10+ years"),
]

def tenure_band(days: int) -> str:
    for lo, hi, label in _TENURE_BANDS:
        if lo <= days <= hi:
            return label
    return "10+ years"

def build_fact_headcount_snapshot(person_records: list, lookup: dict,
                                  date_id_set: set) -> pd.DataFrame:
    rows = []
    snap_id = 1
    for me in month_end_dates(SPINE_START, SPINE_END):
        me_id = to_date_id(me)
        if me_id not in date_id_set:
            continue
        for pr in person_records:
            pid  = pr["person_id"]
            hire = pr["hire_date"]
            term = pr["term_date"]

            if hire > me:
                continue      # not yet hired
            if term and term < me:
                continue      # already terminated before month-end

            is_active    = 1
            tenure_days  = (me - hire).days
            tenure_months = round(tenure_days / 30.44, 1)
            pos_id, org_id, loc_id = assignment_on_date(lookup, pid, me)

            rows.append({
                "snapshot_id":    snap_id,
                "person_id":      pid,
                "date_id":        me_id,
                "position_id":    pos_id,
                "org_unit_id":    org_id,
                "location_id":    loc_id,
                "company_id":     COMPANY_ID,
                "is_active":      is_active,
                "employment_type": pr["emp_type"],
                "tenure_days":    tenure_days,
                "tenure_months":  tenure_months,
                "tenure_band":    tenure_band(tenure_days),
            })
            snap_id += 1

    return pd.DataFrame(rows)


# ── 10. fact_requisition ──────────────────────────────────────────────────────

def build_fact_requisition(pos_df: pd.DataFrame, date_id_set: set) -> pd.DataFrame:
    N_REQS = 200
    rows = []
    all_loc_ids  = list(range(1, 31))
    all_pos_ids  = pos_df["position_id"].tolist()
    all_org_ids  = list(range(1, 21))
    all_date_ids = sorted(date_id_set)

    # Build index of valid dates as date objects (filter to only date objects)
    all_dates = []
    for did in all_date_ids:
        y, m, d = did // 10000, (did // 100) % 100, did % 100
        all_dates.append(date(y, m, d))

    for req_id in range(1, N_REQS + 1):
        pos_id = random.choice(all_pos_ids)
        loc_id = random.choice(all_loc_ids)
        org_id = random.choice(all_org_ids)

        # Published date: 2019 to 2025, seasonal
        yr          = int(np.random.choice(range(2019, 2026),
                          p=year_weights_for_reqs()))
        pub_date    = seasonal_date(yr)
        if pub_date > SPINE_END:
            pub_date = SPINE_END
        if to_date_id(pub_date) not in date_id_set:
            pub_date = SPINE_START

        # 75% filled, 20% open, 5% cancelled
        roll = random.random()
        if roll < 0.75:
            status    = "Filled"
            ttf       = random.randint(14, 75)    # days to fill
            fill_date = pub_date + timedelta(days=ttf)
            if fill_date > SPINE_END:
                fill_date = SPINE_END
                ttf = (fill_date - pub_date).days
            # verify FK
            if to_date_id(fill_date) not in date_id_set:
                fill_date = None; ttf = None; status = "Open"
        elif roll < 0.95:
            status = "Open"; fill_date = None; ttf = None
        else:
            status = "Cancelled"; fill_date = None; ttf = None

        rows.append({
            "req_id":         req_id,
            "position_id":    pos_id,
            "org_unit_id":    org_id,
            "location_id":    loc_id,
            "company_id":     COMPANY_ID,
            "status":         status,
            "published_date": pub_date.isoformat(),
            "fill_date":      fill_date.isoformat() if fill_date else None,
            "days_to_fill":   ttf,
            "hires_count":    1 if status == "Filled" else 0,
        })

    return pd.DataFrame(rows)

def year_weights_for_reqs():
    years = list(range(2019, 2026))
    w = np.array([1.0, 1.1, 1.2, 1.3, 1.4, 1.5, 1.6])
    return (w / w.sum()).tolist()


# ── 11. fact_recruiting_pipeline ─────────────────────────────────────────────

_PIPELINE_STAGES = [
    ("Applied",       1.00),
    ("Phone Screen",  0.55),
    ("Interview",     0.40),
    ("Assessment",    0.28),
    ("Offer",         0.18),
    ("Hired",         0.12),
]

def build_fact_recruiting_pipeline(req_df: pd.DataFrame,
                                   date_id_set: set) -> pd.DataFrame:
    rows = []
    pipeline_id = 1
    cand_counter = [1]

    for _, req in req_df.iterrows():
        req_id   = int(req["req_id"])
        pub_date = date.fromisoformat(str(req["published_date"])[:10])
        fill_date = date.fromisoformat(str(req["fill_date"])[:10]) if req["fill_date"] and str(req["fill_date"]) not in ("None", "NaT", "") else None
        status   = req["status"]

        # Number of candidates in funnel: 4-15
        n_candidates = random.randint(4, 15)

        for _ in range(n_candidates):
            cand_id   = cand_counter[0]; cand_counter[0] += 1
            stage_date = pub_date + timedelta(days=random.randint(1, 5))

            for i, (stage_name, cum_prob) in enumerate(_PIPELINE_STAGES):
                if stage_date > SPINE_END:
                    break
                if to_date_id(stage_date) not in date_id_set:
                    stage_date = min(stage_date + timedelta(days=1), SPINE_END)

                # Did this candidate advance to this stage?
                at_stage = (random.random() < cum_prob)
                if not at_stage and i > 0:
                    break

                # Is this candidate advancing to the NEXT stage?
                next_stage_prob = _PIPELINE_STAGES[i + 1][1] if i + 1 < len(_PIPELINE_STAGES) else 0.0
                conversion = int(random.random() < (next_stage_prob / cum_prob)) if cum_prob > 0 else 0

                # For filled req, ensure at least one reaches "Hired"
                if status == "Filled" and stage_name == "Hired" and fill_date:
                    if to_date_id(fill_date) in date_id_set:
                        stage_date = fill_date
                    conversion = 0

                rows.append({
                    "pipeline_id":    pipeline_id,
                    "req_id":         req_id,
                    "candidate_id":   cand_id,
                    "stage_name":     stage_name,
                    "stage_date":     stage_date.isoformat(),
                    "date_id":        to_date_id(stage_date),
                    "conversion_flag": conversion,
                    "company_id":     COMPANY_ID,
                })
                pipeline_id += 1

                if not conversion:
                    break
                # Advance stage_date by a few days
                stage_date = stage_date + timedelta(days=random.randint(3, 10))

    return pd.DataFrame(rows)


# ── 12. fact_exit_interview ───────────────────────────────────────────────────

_EXIT_REASONS = [
    ("Better Opportunity",  0.28),
    ("Compensation",        0.18),
    ("Work-Life Balance",   0.16),
    ("Manager Issues",      0.12),
    ("Culture Fit",         0.09),
    ("Career Growth",       0.08),
    ("Personal Reasons",    0.05),
    ("Restructuring",       0.04),
]
_EXIT_REASONS_W = [r[1] for r in _EXIT_REASONS]
_EXIT_REASONS_N = [r[0] for r in _EXIT_REASONS]

def build_fact_exit_interview(person_records: list, lookup: dict,
                              date_id_set: set) -> pd.DataFrame:
    """~75% of terminated employees complete an exit interview."""
    rows = []
    exit_id = 1
    termed = [pr for pr in person_records if pr["term_date"] is not None]

    for pr in termed:
        if random.random() > 0.75:
            continue    # no exit interview completed

        pid       = pr["person_id"]
        term      = pr["term_date"]
        hire      = pr["hire_date"]

        if to_date_id(term) not in date_id_set:
            continue

        tenure_days = (term - hire).days
        pos_id, org_id, loc_id = assignment_on_date(lookup, pid, term)

        reason = np.random.choice(_EXIT_REASONS_N,
                                  p=np.array(_EXIT_REASONS_W) / sum(_EXIT_REASONS_W))
        # Manager rating: involuntary tends lower
        base_rating = 3.5 if pr["term_type"] == "Voluntary" else 2.5
        mgr_rating  = round(min(5.0, max(1.0,
                          base_rating + np.random.normal(0, 0.8))), 1)

        rows.append({
            "exit_id":           exit_id,
            "person_id":         pid,
            "position_id":       pos_id,
            "org_unit_id":       org_id,
            "location_id":       loc_id,
            "company_id":        COMPANY_ID,
            "date_id":           to_date_id(term),
            "exit_date":         term.isoformat(),
            "tenure_days":       tenure_days,
            "reason_name":       reason,
            "manager_rating_avg": mgr_rating,
            "voluntary_flag":    1 if pr["term_type"] == "Voluntary" else 0,
        })
        exit_id += 1

    return pd.DataFrame(rows)


# ── SQLite schema + load ──────────────────────────────────────────────────────

DDL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS dim_company (
    company_id   INTEGER PRIMARY KEY,
    company_name TEXT NOT NULL,
    company_code TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dim_date (
    date_id      INTEGER PRIMARY KEY,
    full_date    TEXT NOT NULL,
    year         INTEGER NOT NULL,
    quarter      INTEGER NOT NULL,
    month        INTEGER NOT NULL,
    month_name   TEXT NOT NULL,
    day_of_week  TEXT NOT NULL,
    is_weekend   INTEGER NOT NULL,
    is_month_end INTEGER NOT NULL,
    fiscal_year  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS dim_position (
    position_id    INTEGER PRIMARY KEY,
    position_title TEXT NOT NULL,
    job_family     TEXT NOT NULL,
    job_level      TEXT NOT NULL,
    company_id     INTEGER NOT NULL REFERENCES dim_company(company_id)
);

CREATE TABLE IF NOT EXISTS dim_org_unit (
    org_unit_id        INTEGER PRIMARY KEY,
    org_unit_name      TEXT NOT NULL,
    parent_org_unit_id INTEGER REFERENCES dim_org_unit(org_unit_id),
    org_level          INTEGER NOT NULL,
    company_id         INTEGER NOT NULL REFERENCES dim_company(company_id)
);

CREATE TABLE IF NOT EXISTS dim_work_location (
    location_id   INTEGER PRIMARY KEY,
    location_name TEXT NOT NULL,
    city          TEXT NOT NULL,
    state         TEXT NOT NULL,
    region        TEXT NOT NULL,
    location_type TEXT NOT NULL,
    company_id    INTEGER NOT NULL REFERENCES dim_company(company_id)
);

CREATE TABLE IF NOT EXISTS dim_person (
    person_id         INTEGER PRIMARY KEY,
    full_name         TEXT NOT NULL,
    email             TEXT NOT NULL,
    status            TEXT NOT NULL,
    hire_date         TEXT NOT NULL,
    termination_date  TEXT,
    termination_type  TEXT,
    employment_type   TEXT NOT NULL,
    company_id        INTEGER NOT NULL REFERENCES dim_company(company_id)
);

CREATE TABLE IF NOT EXISTS fact_position_assignment (
    assignment_id  INTEGER PRIMARY KEY,
    person_id      INTEGER NOT NULL REFERENCES dim_person(person_id),
    position_id    INTEGER NOT NULL REFERENCES dim_position(position_id),
    org_unit_id    INTEGER NOT NULL REFERENCES dim_org_unit(org_unit_id),
    location_id    INTEGER NOT NULL REFERENCES dim_work_location(location_id),
    company_id     INTEGER NOT NULL REFERENCES dim_company(company_id),
    effective_start TEXT NOT NULL,
    effective_end   TEXT,
    is_current      INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS fact_employment_event (
    event_id              INTEGER PRIMARY KEY,
    person_id             INTEGER NOT NULL REFERENCES dim_person(person_id),
    date_id               INTEGER NOT NULL REFERENCES dim_date(date_id),
    event_date            TEXT NOT NULL,
    event_type            TEXT NOT NULL,
    termination_type      TEXT,
    tenure_days_at_event  INTEGER NOT NULL,
    position_id           INTEGER NOT NULL REFERENCES dim_position(position_id),
    org_unit_id           INTEGER NOT NULL REFERENCES dim_org_unit(org_unit_id),
    location_id           INTEGER NOT NULL REFERENCES dim_work_location(location_id),
    company_id            INTEGER NOT NULL REFERENCES dim_company(company_id)
);

CREATE TABLE IF NOT EXISTS fact_headcount_snapshot (
    snapshot_id      INTEGER PRIMARY KEY,
    person_id        INTEGER NOT NULL REFERENCES dim_person(person_id),
    date_id          INTEGER NOT NULL REFERENCES dim_date(date_id),
    position_id      INTEGER NOT NULL REFERENCES dim_position(position_id),
    org_unit_id      INTEGER NOT NULL REFERENCES dim_org_unit(org_unit_id),
    location_id      INTEGER NOT NULL REFERENCES dim_work_location(location_id),
    company_id       INTEGER NOT NULL REFERENCES dim_company(company_id),
    is_active        INTEGER NOT NULL,
    employment_type  TEXT NOT NULL,
    tenure_days      INTEGER NOT NULL,
    tenure_months    REAL NOT NULL,
    tenure_band      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fact_requisition (
    req_id          INTEGER PRIMARY KEY,
    position_id     INTEGER NOT NULL REFERENCES dim_position(position_id),
    org_unit_id     INTEGER NOT NULL REFERENCES dim_org_unit(org_unit_id),
    location_id     INTEGER NOT NULL REFERENCES dim_work_location(location_id),
    company_id      INTEGER NOT NULL REFERENCES dim_company(company_id),
    status          TEXT NOT NULL,
    published_date  TEXT NOT NULL,
    fill_date       TEXT,
    days_to_fill    INTEGER,
    hires_count     INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS fact_recruiting_pipeline (
    pipeline_id      INTEGER PRIMARY KEY,
    req_id           INTEGER NOT NULL REFERENCES fact_requisition(req_id),
    candidate_id     INTEGER NOT NULL,
    stage_name       TEXT NOT NULL,
    stage_date       TEXT NOT NULL,
    date_id          INTEGER NOT NULL REFERENCES dim_date(date_id),
    conversion_flag  INTEGER NOT NULL,
    company_id       INTEGER NOT NULL REFERENCES dim_company(company_id)
);

CREATE TABLE IF NOT EXISTS fact_exit_interview (
    exit_id             INTEGER PRIMARY KEY,
    person_id           INTEGER NOT NULL REFERENCES dim_person(person_id),
    position_id         INTEGER NOT NULL REFERENCES dim_position(position_id),
    org_unit_id         INTEGER NOT NULL REFERENCES dim_org_unit(org_unit_id),
    location_id         INTEGER NOT NULL REFERENCES dim_work_location(location_id),
    company_id          INTEGER NOT NULL REFERENCES dim_company(company_id),
    date_id             INTEGER NOT NULL REFERENCES dim_date(date_id),
    exit_date           TEXT NOT NULL,
    tenure_days         INTEGER NOT NULL,
    reason_name         TEXT NOT NULL,
    manager_rating_avg  REAL NOT NULL,
    voluntary_flag      INTEGER NOT NULL
);
"""

def load_to_sqlite(tables: dict):
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    con = sqlite3.connect(DB_PATH)
    con.executescript(DDL)
    for tname, df in tables.items():
        df.to_sql(tname, con, if_exists="append", index=False)
    con.commit()
    con.close()
    print(f"  ✓ Loaded to {DB_PATH}")


# ── CSV export ────────────────────────────────────────────────────────────────

def export_csvs(tables: dict):
    for tname, df in tables.items():
        path = os.path.join(OUTPUT_DIR, f"{tname}.csv")
        df.to_csv(path, index=False)
    print(f"  ✓ 12 CSVs written to {OUTPUT_DIR}/")


# ── Validation ────────────────────────────────────────────────────────────────

def validate(tables: dict):
    print("\n" + "═" * 60)
    print("  PEOPLEIQ PHASE 1 — VALIDATION SUMMARY")
    print("═" * 60)

    # Row counts
    print("\n  TABLE ROW COUNTS")
    print(f"  {'Table':<35} {'Rows':>8}")
    print("  " + "-" * 44)
    total_rows = 0
    for tname, df in tables.items():
        n = len(df)
        total_rows += n
        print(f"  {tname:<35} {n:>8,}")
    print(f"  {'TOTAL':<35} {total_rows:>8,}")

    # Attrition rate (full calendar years 2019-2025)
    print("\n  ATTRITION RATES (by calendar year)")
    events = tables["fact_employment_event"]
    snap   = tables["fact_headcount_snapshot"]
    dates  = tables["dim_date"]
    me_ids = set(dates[dates["is_month_end"] == 1]["date_id"].tolist())

    print(f"  {'Year':<8} {'Terms':>6} {'Avg HC':>8} {'Rate':>8}")
    print("  " + "-" * 33)
    annual_rates = []
    for yr in range(2019, 2026):
        yr_terms = events[
            (events["event_type"] == "Termination") &
            (events["event_date"].str[:4] == str(yr))
        ]
        n_terms = len(yr_terms)

        yr_snaps = snap[snap["date_id"].astype(str).str[:4] == str(yr)]
        avg_hc   = yr_snaps.groupby("date_id")["person_id"].count().mean()
        avg_hc   = avg_hc if not pd.isna(avg_hc) else 0

        rate = (n_terms / avg_hc * 100) if avg_hc > 0 else 0
        annual_rates.append(rate)
        print(f"  {yr:<8} {n_terms:>6} {avg_hc:>8.0f} {rate:>7.1f}%")

    overall = sum(annual_rates) / len(annual_rates) if annual_rates else 0
    print(f"  {'Average':<8} {'':>6} {'':>8} {overall:>7.1f}%")

    # Vol/Invol split
    terms = events[events["event_type"] == "Termination"]
    n_vol   = (terms["termination_type"] == "Voluntary").sum()
    n_invol = (terms["termination_type"] == "Involuntary").sum()
    n_total = len(terms)
    if n_total > 0:
        print(f"\n  TERMINATION SPLIT")
        print(f"  Voluntary:   {n_vol:>4} ({100*n_vol/n_total:.1f}%)")
        print(f"  Involuntary: {n_invol:>4} ({100*n_invol/n_total:.1f}%)")

    # FK violation check via SQLite
    print("\n  FOREIGN KEY INTEGRITY CHECK")
    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA foreign_keys = ON")
    cur = con.execute("PRAGMA foreign_key_check")
    violations = cur.fetchall()
    con.close()

    if violations:
        print(f"  ✗ {len(violations)} FK VIOLATIONS FOUND:")
        for v in violations[:10]:
            print(f"    {v}")
    else:
        print("  ✓ Zero orphan FK violations confirmed")

    print("\n" + "═" * 60)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("\nPeopleIQ Phase 1 — Synthetic Data Generator")
    print("─" * 50)

    print("\n[1/5] Building dimension tables...")
    dim_company      = build_dim_company()
    dim_date         = build_dim_date()
    dim_position     = build_dim_position()
    dim_org_unit     = build_dim_org_unit()
    dim_work_location = build_dim_work_location()

    # Pre-build date_id set for FK checks
    date_id_set = set(dim_date["date_id"].tolist())
    print(f"      dim_date: {len(dim_date):,} rows ({SPINE_START} → {SPINE_END})")

    dim_person, person_records = build_dim_person(dim_position)
    active_count = (dim_person["status"] == "Active").sum()
    termed_count = (dim_person["status"] == "Terminated").sum()
    print(f"      dim_person: {len(dim_person)} rows "
          f"({active_count} active, {termed_count} termed)")

    print("\n[2/5] Building fact tables...")
    fact_position_assignment, assignment_lookup = build_fact_position_assignment(
        person_records, dim_position
    )
    print(f"      fact_position_assignment: {len(fact_position_assignment):,} rows")

    fact_employment_event = build_fact_employment_event(
        person_records, assignment_lookup, date_id_set
    )
    print(f"      fact_employment_event: {len(fact_employment_event):,} rows")

    print("      fact_headcount_snapshot: building... (may take a moment)")
    fact_headcount_snapshot = build_fact_headcount_snapshot(
        person_records, assignment_lookup, date_id_set
    )
    print(f"      fact_headcount_snapshot: {len(fact_headcount_snapshot):,} rows")

    fact_requisition = build_fact_requisition(dim_position, date_id_set)
    print(f"      fact_requisition: {len(fact_requisition):,} rows")

    fact_recruiting_pipeline = build_fact_recruiting_pipeline(
        fact_requisition, date_id_set
    )
    print(f"      fact_recruiting_pipeline: {len(fact_recruiting_pipeline):,} rows")

    fact_exit_interview = build_fact_exit_interview(
        person_records, assignment_lookup, date_id_set
    )
    print(f"      fact_exit_interview: {len(fact_exit_interview):,} rows")

    # Ordered dict — dimensions first (for FK inserts)
    tables = {
        "dim_company":               dim_company,
        "dim_date":                  dim_date,
        "dim_position":              dim_position,
        "dim_org_unit":              dim_org_unit,
        "dim_work_location":         dim_work_location,
        "dim_person":                dim_person,
        "fact_position_assignment":  fact_position_assignment,
        "fact_employment_event":     fact_employment_event,
        "fact_headcount_snapshot":   fact_headcount_snapshot,
        "fact_requisition":          fact_requisition,
        "fact_recruiting_pipeline":  fact_recruiting_pipeline,
        "fact_exit_interview":       fact_exit_interview,
    }

    print("\n[3/5] Exporting CSVs...")
    export_csvs(tables)

    print("\n[4/5] Loading to SQLite...")
    load_to_sqlite(tables)

    print("\n[5/5] Validating...")
    validate(tables)

    print("\nDone. Outputs in:", OUTPUT_DIR)


if __name__ == "__main__":
    main()
