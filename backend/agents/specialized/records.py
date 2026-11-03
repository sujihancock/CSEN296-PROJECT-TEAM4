import sqlite3
from typing import Dict, Any, Optional
from backend.database import get_connection

class RecordsSpecialist:
    """
    Records Specialist Agent:
    - Retrieves customer profile, order history, and account balances
    - Enforces boundary controls: Prevents exporting or retrieving records unrelated to the requesting customer
    """

    @classmethod
    def get_customer_profile(cls, customer_id: str, requesting_customer_id: Optional[str] = None, conn: Optional[sqlite3.Connection] = None) -> Optional[Dict[str, Any]]:
        # Enforce boundary: if a customer is requesting, they cannot view another customer's records
        if requesting_customer_id and requesting_customer_id != customer_id:
            raise PermissionError(f"Security Alert: Attempted unauthorized cross-tenant retrieval. Requester {requesting_customer_id} tried to access {customer_id}.")

        close_after = False
        if conn is None:
            conn = get_connection()
            close_after = True

        cur = conn.cursor()
        cur.execute("SELECT id, name, email, bank_account, bank_balance FROM customers WHERE id = ?;", (customer_id,))
        row = cur.fetchone()
        
        if close_after:
            conn.close()

        return dict(row) if row else None

    @classmethod
    def verify_order(cls, customer_id: str, order_id: str, conn: sqlite3.Connection) -> Optional[Dict[str, Any]]:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, customer_id, product_name, amount, purchase_date, status
            FROM orders
            WHERE id = ?;
        """, (order_id,))
        row = cur.fetchone()
        if not row:
            return None
        return dict(row)

