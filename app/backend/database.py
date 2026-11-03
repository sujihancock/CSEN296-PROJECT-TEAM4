import sqlite3
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from app.backend.config import DB_PATH

def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn

def init_db(seed: bool = True, drop_existing: bool = False):
    conn = get_connection()
    cur = conn.cursor()

    if drop_existing:
        cur.execute("DROP TABLE IF EXISTS customers;")
        cur.execute("DROP TABLE IF EXISTS orders;")
        cur.execute("DROP TABLE IF EXISTS policies;")
        cur.execute("DROP TABLE IF EXISTS cases;")
        cur.execute("DROP TABLE IF EXISTS emails;")
        cur.execute("DROP TABLE IF EXISTS audit_logs;")
        cur.execute("DROP TABLE IF EXISTS system_settings;")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS customers (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        email TEXT NOT NULL UNIQUE,
        bank_account TEXT NOT NULL,
        bank_balance REAL NOT NULL DEFAULT 1000.0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS orders (
        id TEXT PRIMARY KEY,
        customer_id TEXT NOT NULL,
        product_name TEXT NOT NULL,
        amount REAL NOT NULL,
        purchase_date TIMESTAMP NOT NULL,
        status TEXT NOT NULL DEFAULT 'DELIVERED',
        FOREIGN KEY (customer_id) REFERENCES customers(id)
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS policies (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        content TEXT NOT NULL,
        tags TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS cases (
        id TEXT PRIMARY KEY,
        customer_id TEXT NOT NULL,
        order_id TEXT NOT NULL,
        amount_requested REAL NOT NULL,
        reason TEXT NOT NULL,
        status TEXT NOT NULL, -- RECEIVED, UNDER_REVIEW, APPROVED, DENIED, ESCALATED, REVOKED
        assigned_to TEXT NOT NULL,
        decision_reason TEXT,
        autonomous INTEGER DEFAULT 1, -- 1 if approved/denied by agent, 0 if supervisor
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (customer_id) REFERENCES customers(id),
        FOREIGN KEY (order_id) REFERENCES orders(id)
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS emails (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        case_id TEXT NOT NULL,
        to_email TEXT NOT NULL,
        subject TEXT NOT NULL,
        body TEXT NOT NULL,
        email_type TEXT NOT NULL, -- ACKNOWLEDGEMENT, DECISION, ESCALATION
        sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS audit_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        case_id TEXT,
        action TEXT NOT NULL,
        agent TEXT NOT NULL,
        details TEXT NOT NULL,
        status TEXT NOT NULL, -- ALLOWED, BLOCKED, ESCALATED, OVERRIDDEN
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS system_settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    conn.commit()

    if seed:
        seed_data(conn)

    conn.close()

def seed_data(conn: sqlite3.Connection):
    cur = conn.cursor()

    # Seed system settings
    cur.execute("INSERT OR IGNORE INTO system_settings (key, value) VALUES ('kill_switch_active', 'false');")

    # Seed Customers
    customers = [
        ("CUST-001", "Alex Smith", "alex.smith@example.com", "BANK-ACCT-9871", 1250.0),
        ("CUST-002", "Elena Rostova", "elena.rostova@example.com", "BANK-ACCT-4412", 840.0),
        ("CUST-003", "Marcus Vance", "marcus.vance@example.com", "BANK-ACCT-3310", 2100.0),
    ]
    cur.executemany("""
    INSERT OR IGNORE INTO customers (id, name, email, bank_account, bank_balance)
    VALUES (?, ?, ?, ?, ?);
    """, customers)

    # Seed Orders
    now = datetime.utcnow()
    orders = [
        ("ORD-101", "CUST-001", "Wireless Noise-Canceling Headphones", 45.0, (now - timedelta(days=5)).isoformat(), "DELIVERED"),
        ("ORD-102", "CUST-001", "Mechanical Keyboard", 120.0, (now - timedelta(days=12)).isoformat(), "DELIVERED"),
        ("ORD-103", "CUST-001", "4K Ultra-HD Monitor", 450.0, (now - timedelta(days=20)).isoformat(), "DELIVERED"),
        ("ORD-201", "CUST-002", "USB-C Multiport Dock", 35.0, (now - timedelta(days=3)).isoformat(), "DELIVERED"),
        ("ORD-202", "CUST-002", "Ergonomic Desk Chair", 280.0, (now - timedelta(days=40)).isoformat(), "DELIVERED"), # Outside 30-day window
        ("ORD-301", "CUST-003", "Python AI Engineering E-Book", 25.0, (now - timedelta(days=2)).isoformat(), "DELIVERED"),
    ]
    cur.executemany("""
    INSERT OR IGNORE INTO orders (id, customer_id, product_name, amount, purchase_date, status)
    VALUES (?, ?, ?, ?, ?, ?);
    """, orders)

    # Seed Policies
    policies = [
        (
            "POL-001",
            "Standard 30-Day Refund Policy",
            "Customers are eligible for a full refund on physical items within 30 days of the purchase date. Items must be in working condition or reported as defective/damaged upon arrival.",
            "refund, window, 30 days, physical goods, return"
        ),
        (
            "POL-002",
            "Damaged in Transit / Defective Goods",
            "If an item arrives damaged, defective, or missing components, a full refund or replacement is guaranteed immediately upon customer submission of the claim with order verification.",
            "damaged, defective, broken, shipping, transit, guaranteed"
        ),
        (
            "POL-003",
            "Digital Goods Policy",
            "Digital products, e-books, downloadable content, and software licenses are non-refundable once accessed or downloaded unless technical corruption prevents usage.",
            "digital, software, ebook, non-refundable, license"
        ),
        (
            "POL-004",
            "High-Value and Security Escalation Policy",
            "Any single refund request exceeding $100, or any customer request that would cause cumulative weekly automated refunds to exceed $500, cannot be autonomously approved by AI and must be forwarded to a human supervisor for manual review.",
            "escalation, limit, $100, $500, supervisor, autonomous cap"
        )
    ]
    cur.executemany("""
    INSERT OR IGNORE INTO policies (id, title, content, tags)
    VALUES (?, ?, ?, ?);
    """, policies)

    conn.commit()
