import sqlite3
from dataclasses import dataclass

from app.reference import Reference


@dataclass
class Payment:
    payment_id: str
    claim_id: str
    amount: float
    status: str
    approved_by: str | None = None


def _get(conn: sqlite3.Connection, where: str, value: str) -> Payment | None:
    row = conn.execute(f"SELECT * FROM payments WHERE {where} = ?", (value,)).fetchone()
    if row is None:
        return None
    return Payment(row["payment_id"], row["claim_id"], row["amount"], row["status"], row["approved_by"])


def request_payment(conn: sqlite3.Connection, claim_id: str, amount: float, limit: float) -> Payment:
    """Create the payment once per claim. Anything above the limit is held, whoever asks."""
    status = "paid" if amount <= limit else "held"
    conn.execute(
        "INSERT INTO payments (payment_id, claim_id, amount, status) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(claim_id) DO NOTHING",
        (f"PAY-{claim_id}", claim_id, amount, status),
    )
    conn.commit()
    return _get(conn, "claim_id", claim_id)


def approve_payment(conn: sqlite3.Connection, payment_id: str, handler_id: str, ref: Reference) -> Payment:
    handler = next((h for h in ref.handlers if h["handler_id"] == handler_id), None)
    if handler is None or not handler["can_approve_over_limit"]:
        raise PermissionError(f"{handler_id} cannot approve payments over the limit")
    if _get(conn, "payment_id", payment_id) is None:
        raise LookupError(payment_id)
    conn.execute(
        "UPDATE payments SET status='paid', approved_by=?, updated_at=CURRENT_TIMESTAMP "
        "WHERE payment_id=? AND status='held'",
        (handler_id, payment_id),
    )
    conn.commit()
    return _get(conn, "payment_id", payment_id)
