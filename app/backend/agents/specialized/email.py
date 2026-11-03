import sqlite3
from typing import Dict, Any, List
from app.backend.database import get_connection

class EmailSpecialist:
    """
    Email Specialist Agent:
    - Drafts, sends, and logs customer communications
    - Handles case acknowledgements and refund decision notifications
    """

    @classmethod
    def send_acknowledgement(
        cls,
        case_id: str,
        to_email: str,
        customer_name: str,
        amount: float,
        conn: sqlite3.Connection
    ) -> Dict[str, Any]:
        subject = f"[ChangSupport] Case Received: #{case_id}"
        body = (
            f"Dear {customer_name},\n\n"
            f"Thank you for contacting ChangSupport. We have received your refund request of ${amount:.2f} "
            f"for Case #{case_id}. Our automated support system is reviewing your case against our refund policy.\n\n"
            f"You will receive an update shortly once the policy evaluation is complete.\n\n"
            f"Best regards,\n"
            f"ChangSupport Automated Team"
        )
        return cls._record_email(case_id, to_email, subject, body, "ACKNOWLEDGEMENT", conn)

    @classmethod
    def send_decision_notification(
        cls,
        case_id: str,
        to_email: str,
        customer_name: str,
        status: str,
        reason: str,
        amount: float,
        conn: sqlite3.Connection
    ) -> Dict[str, Any]:
        subject = f"[ChangSupport] Update on Case #{case_id}: {status}"
        if status == "APPROVED":
            body = (
                f"Dear {customer_name},\n\n"
                f"Good news! Your refund request for ${amount:.2f} (Case #{case_id}) has been approved.\n"
                f"Reason: {reason}\n"
                f"Your bank account balance has been credited accordingly.\n\n"
                f"Thank you for your business,\n"
                f"ChangSupport Team"
            )
        elif status == "ESCALATED":
            body = (
                f"Dear {customer_name},\n\n"
                f"Your refund request for ${amount:.2f} (Case #{case_id}) has been escalated to a human supervisor for manual review.\n"
                f"Details: {reason}\n\n"
                f"A supervisor will review your case within 1 business day.\n\n"
                f"ChangSupport Escalations Team"
            )
        else: # DENIED or REVOKED
            body = (
                f"Dear {customer_name},\n\n"
                f"We are writing to inform you that your refund request for Case #{case_id} could not be approved at this time.\n"
                f"Reason: {reason}\n\n"
                f"If you believe this is in error, please reply to this notice or contact supervisor support.\n\n"
                f"ChangSupport Team"
            )

        return cls._record_email(case_id, to_email, subject, body, "DECISION", conn)

    @classmethod
    def _record_email(
        cls,
        case_id: str,
        to_email: str,
        subject: str,
        body: str,
        email_type: str,
        conn: sqlite3.Connection
    ) -> Dict[str, Any]:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO emails (case_id, to_email, subject, body, email_type)
            VALUES (?, ?, ?, ?, ?);
        """, (case_id, to_email, subject, body, email_type))
        conn.commit()
        return {
            "case_id": case_id,
            "to_email": to_email,
            "subject": subject,
            "email_type": email_type,
            "status": "SENT"
        }

    @classmethod
    def get_emails_for_customer(cls, email: str, conn: sqlite3.Connection) -> List[Dict[str, Any]]:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, case_id, to_email, subject, body, email_type, sent_at
            FROM emails
            WHERE to_email = ?
            ORDER BY sent_at DESC;
        """, (email,))
        rows = cur.fetchall()
        return [dict(r) for r in rows]

