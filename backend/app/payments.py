from dataclasses import dataclass

from sqlalchemy import Engine, func, select, update

from app.db import insert_or_ignore, payments
from app.reference import Reference


@dataclass
class Payment:
    payment_id: str
    claim_id: str
    amount: float
    status: str
    approved_by: str | None = None


def _get(engine: Engine, column, value: str) -> Payment | None:
    with engine.connect() as conn:
        row = conn.execute(select(payments).where(column == value)).mappings().first()
    if row is None:
        return None
    return Payment(row["payment_id"], row["claim_id"], row["amount"], row["status"], row["approved_by"])


def request_payment(engine: Engine, claim_id: str, amount: float, limit: float) -> Payment:
    """Create the payment once per claim. Anything above the limit is held, whoever asks."""
    status = "paid" if amount <= limit else "held"
    with engine.begin() as conn:
        conn.execute(
            insert_or_ignore(conn, payments),
            {"payment_id": f"PAY-{claim_id}", "claim_id": claim_id, "amount": amount, "status": status},
        )
    return _get(engine, payments.c.claim_id, claim_id)


def approve_payment(engine: Engine, payment_id: str, handler_id: str, ref: Reference) -> Payment:
    handler = next((h for h in ref.handlers if h["handler_id"] == handler_id), None)
    if handler is None or not handler["can_approve_over_limit"]:
        raise PermissionError(f"{handler_id} cannot approve payments over the limit")
    if _get(engine, payments.c.payment_id, payment_id) is None:
        raise LookupError(payment_id)
    with engine.begin() as conn:
        conn.execute(
            update(payments)
            .where(payments.c.payment_id == payment_id, payments.c.status == "held")
            .values(status="paid", approved_by=handler_id, updated_at=func.now())
        )
    return _get(engine, payments.c.payment_id, payment_id)
