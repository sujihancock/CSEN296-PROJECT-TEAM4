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
    category TEXT NOT NULL,              -- physical | digital
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
            ("CUST-001", "Alexandre Lopes", "alexandre.lopes@example.com", "BANK-ACCT-9871", 1250.0),
            ("CUST-002", "Ariana Sun", "ariana.sun@example.com", "BANK-ACCT-4412", 840.0),
            ("CUST-003", "Suji Hancock", "suji.hancock@example.com", "BANK-ACCT-3310", 2100.0),
            ("CUST-004", "Raphael Kusuma", "raphael.kusuma@example.com", "BANK-ACCT-5520", 960.0),
        ],
    )
    conn.executemany(
        "INSERT OR IGNORE INTO orders (id, customer_id, product_name, category, amount, purchase_date) VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("ORD-101", "CUST-001", "Wireless Noise-Canceling Headphones", "physical", 45.0, now_ts(-5)),
            ("ORD-102", "CUST-001", "Mechanical Keyboard", "physical", 120.0, now_ts(-12)),
            ("ORD-103", "CUST-001", "4K Ultra-HD Monitor", "physical", 450.0, now_ts(-20)),
            ("ORD-104", "CUST-001", "Prompt Engineering Handbook (eBook)", "digital", 30.0, now_ts(-4)),
            ("ORD-105", "CUST-001", "Laptop Stand", "physical", 60.0, now_ts(-45)),
            ("ORD-201", "CUST-002", "USB-C Multiport Dock", "physical", 35.0, now_ts(-3)),
            ("ORD-202", "CUST-002", "Ergonomic Desk Chair", "physical", 280.0, now_ts(-40)),
            ("ORD-301", "CUST-003", "Python AI Engineering eBook", "digital", 25.0, now_ts(-2)),
            ("ORD-401", "CUST-004", "USB-C Hub", "physical", 40.0, now_ts(-6)),
        ],
    )
    conn.executemany(
        "INSERT OR IGNORE INTO policies (id, title, content, tags, uploaded_by, updated_at) VALUES (?, ?, ?, ?, 'seed', ?)",
        [
            ("POL-001", "Standard 30-Day Refund Policy",
             "Customers are eligible for a full refund on physical items within 30 days of the purchase date. "
             "Items must be in working condition or reported as defective or damaged upon arrival.",
             "refund window 30 days physical goods return", ts),
            ("POL-002", "Damaged in Transit / Defective Goods",
             "If an item arrives damaged, defective, or missing components, a full refund or replacement is "
             "guaranteed upon customer submission of the claim with order verification.",
             "damaged defective broken shipping transit", ts),
            ("POL-003", "Digital Goods Policy",
             "Digital products, e-books, downloadable content, and software licenses are non-refundable once "
             "accessed or downloaded unless technical corruption prevents usage.",
             "digital software ebook non-refundable license download", ts),
            ("POL-004", "High-Value and Security Escalation Policy",
             "Any single refund request exceeding $100, or any request that would cause cumulative weekly "
             "autonomous refunds to exceed $500, cannot be approved by the AI agent and must be forwarded to a "
             "human supervisor for manual review.",
             "escalation limit $100 $500 supervisor autonomous cap", ts),
        ],
    )
    try:
        from app.retriever import Retriever
        Retriever().sync_from_db(conn)
    except Exception:
        pass
