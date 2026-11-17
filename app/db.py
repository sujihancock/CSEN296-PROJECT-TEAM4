"""SQLite storage: schema, seed data and connection helper."""
import sqlite3
from pathlib import Path
from typing import Optional
from datetime import datetime, timedelta, timezone

from app import config

TS_FORMAT = "%Y-%m-%d %H:%M:%S"

# Drop order respects foreign keys.
TABLES = [
    "audit_logs", "disbursements", "emails", "cases",
    "orders", "customers", "policies", "system_settings",
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    bank_account TEXT NOT NULL,
    bank_balance REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL REFERENCES customers(id),
    product_name TEXT NOT NULL,
    category TEXT NOT NULL,              -- covered | excluded
    amount REAL NOT NULL,
    purchase_date TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS policies (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    tags TEXT NOT NULL,
    uploaded_by TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cases (
    id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL REFERENCES customers(id),
    order_id TEXT NOT NULL REFERENCES orders(id),
    amount_requested REAL NOT NULL,
    reason TEXT NOT NULL,
    status TEXT NOT NULL,                -- RECEIVED, UNDER_REVIEW, APPROVED, DENIED, ESCALATED, REVOKED
    assigned_to TEXT NOT NULL,
    decision_reason TEXT,
    decided_by TEXT,
    autonomous INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS emails (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id TEXT NOT NULL,
    to_email TEXT NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    email_type TEXT NOT NULL,            -- ACKNOWLEDGEMENT, DECISION
    sent_at TEXT NOT NULL
);

-- Ledger written only by the guardrail layer. Caps are computed from here.
CREATE TABLE IF NOT EXISTS disbursements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id TEXT NOT NULL,
    customer_id TEXT NOT NULL,
    order_id TEXT NOT NULL,
    amount REAL NOT NULL,
    autonomous INTEGER NOT NULL,
    authorized_by TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- Hash-chained audit trail (see app/audit.py).
CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    case_id TEXT,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    status TEXT NOT NULL,                -- ALLOWED, BLOCKED, ESCALATED, OVERRIDDEN, INFO
    details TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS system_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def now_ts(delta_days: float = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=delta_days)).strftime(TS_FORMAT)


def parse_ts(value: str) -> datetime:
    return datetime.strptime(value, TS_FORMAT).replace(tzinfo=timezone.utc)


def get_connection(path: Optional[Path] = None) -> sqlite3.Connection:
    path = Path(path or config.DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    # One connection per request/workflow; FastAPI may hand it between threadpool threads.
    conn = sqlite3.connect(str(path), timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(seed: bool = True, drop_existing: bool = False, path: Optional[Path] = None) -> None:
    conn = get_connection(path)
    try:
        if drop_existing:
            for table in TABLES:
                conn.execute(f"DROP TABLE IF EXISTS {table}")
            try:
                from app.retriever import get_store_for_conn
                get_store_for_conn(conn).reset()
            except Exception:
                pass
        conn.executescript(SCHEMA)
        if seed:
            _seed(conn)
        conn.commit()
    finally:
        conn.close()


def _seed(conn: sqlite3.Connection) -> None:
    ts = now_ts()
    conn.execute(
        "INSERT OR IGNORE INTO system_settings (key, value, updated_at) VALUES ('kill_switch_active', 'false', ?)",
        (ts,),
    )
    conn.executemany(
        "INSERT OR IGNORE INTO customers (id, name, email, bank_account, bank_balance) VALUES (?, ?, ?, ?, ?)",
        [
            ("BEN-001", "Alex Lopes", "alex.lopes@example.com", "BANK-ACCT-9871", 1250.0),
            ("BEN-002", "Ariana Sun", "ariana.sun@example.com", "BANK-ACCT-4412", 840.0),
            ("BEN-003", "Suji Hancock", "suji.hancock@example.com", "BANK-ACCT-3310", 2100.0),
            ("BEN-004", "Raphael Kusuma", "raphael.kusuma@example.com", "BANK-ACCT-5520", 960.0),
            ("BEN-005", "Jeromy Chang", "jeromy.chang@example.com", "BANK-ACCT-6634", 1480.0),
        ],
    )
    conn.executemany(
        "INSERT OR IGNORE INTO orders (id, customer_id, product_name, category, amount, purchase_date) VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("EXP-101", "BEN-001", "Urgent care visit copay", "covered", 45.0, now_ts(-5)),
            ("EXP-102", "BEN-001", "Physical therapy session", "covered", 120.0, now_ts(-12)),
            ("EXP-103", "BEN-001", "Outpatient MRI scan", "covered", 450.0, now_ts(-20)),
            ("EXP-104", "BEN-001", "Cosmetic teeth whitening", "excluded", 30.0, now_ts(-4)),
            ("EXP-105", "BEN-001", "Routine eye exam", "covered", 60.0, now_ts(-45)),
            ("EXP-201", "BEN-002", "Prescription: amoxicillin", "covered", 35.0, now_ts(-3)),
            ("EXP-202", "BEN-002", "Hearing aid fitting", "covered", 280.0, now_ts(-40)),
            ("EXP-301", "BEN-003", "Wellness app subscription", "excluded", 25.0, now_ts(-2)),
            ("EXP-401", "BEN-004", "Generic prescription refill", "covered", 40.0, now_ts(-6)),
            ("EXP-501", "BEN-005", "Primary care office visit", "covered", 55.0, now_ts(-8)),
        ],
    )
    conn.executemany(
        "INSERT OR IGNORE INTO policies (id, title, content, tags, uploaded_by, updated_at) VALUES (?, ?, ?, ?, 'seed', ?)",
        [
            ("POL-001", "Standard 30-Day Reimbursement Window",
             "Beneficiaries are eligible for full reimbursement of covered healthcare expenses submitted within "
             "30 days of the date of service. The expense must be for a covered service from a licensed provider "
             "and be supported by an itemized receipt.",
             "reimbursement window 30 days covered service eligible expense", ts),
            ("POL-002", "Emergency Care and Billing Error Exception",
             "Expenses for emergency or urgent care, and charges caused by a provider billing error, are "
             "reimbursable in full upon submission of the itemized bill, even after the standard 30-day window.",
             "emergency urgent billing error exception late submission", ts),
            ("POL-003", "Excluded Services Policy",
             "Cosmetic procedures, elective wellness products, and other services excluded from the benefits "
             "program are not reimbursable unless provided as part of emergency care.",
             "excluded cosmetic elective wellness non-covered not reimbursable", ts),
            ("POL-004", "High-Value and Security Escalation Policy",
             "Any single reimbursement request exceeding $100, or any request that would cause cumulative weekly "
             "autonomous reimbursements to exceed $500, cannot be approved by the AI agent and must be forwarded "
             "to a human supervisor for manual review.",
             "escalation limit $100 $500 supervisor autonomous cap", ts),
        ],
    )
    try:
        from app.retriever import Retriever
        Retriever().sync_from_db(conn)
    except Exception:
        pass
