import pytest

from app import config
from app.agent import Orchestrator
from app.db import get_connection, init_db


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    """Every test gets its own freshly seeded database."""
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    init_db(seed=True, drop_existing=True)


@pytest.fixture
def conn():
    c = get_connection()
    yield c
    c.close()


@pytest.fixture
def agent():
    return Orchestrator()


def balance(conn, customer_id="CUST-001"):
    return conn.execute("SELECT bank_balance FROM customers WHERE id = ?", (customer_id,)).fetchone()["bank_balance"]


def add_disbursement(conn, amount, order_id="ORD-201", customer_id="CUST-002", autonomous=1):
    from app.db import now_ts
    conn.execute(
        "INSERT INTO disbursements (case_id, customer_id, order_id, amount, autonomous, authorized_by, created_at) "
        "VALUES ('CASE-SIM', ?, ?, ?, ?, 'test', ?)",
        (customer_id, order_id, amount, autonomous, now_ts()),
    )
    conn.commit()
