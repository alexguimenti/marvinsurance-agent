from dataclasses import dataclass

from sqlalchemy import Engine, func, select, update

from app.claims import get_claim
from app.db import claims, decisions, insert_or_ignore, iso, payments
from app.reference import Reference


class SignOffRequired(Exception):
    """A payout over the limit that no handler has signed off. The system refuses it, whatever the agent does."""


@dataclass
class Payment:
    payment_id: str
    claim_id: str
    amount: float
    status: str  # held -> signed_off -> paid, or paid straight away within the limit
    approved_by: str | None = None
    approved_at: str | None = None
    paid_at: str | None = None


def _get(engine: Engine, column, value: str) -> Payment | None:
    with engine.connect() as conn:
        row = conn.execute(select(payments).where(column == value)).mappings().first()
    if row is None:
        return None
    return Payment(row["payment_id"], row["claim_id"], row["amount"], row["status"], row["approved_by"],
                   iso(row["approved_at"]), iso(row["paid_at"]))


def get_payment(engine: Engine, claim_id: str) -> Payment | None:
    return _get(engine, payments.c.claim_id, claim_id)


def request_payment(engine: Engine, claim_id: str, amount: float, limit: float) -> Payment:
    """Create the payment once per claim. Anything above the limit is held, whoever asks."""
    status = "paid" if amount <= limit else "held"
    with engine.begin() as conn:
        conn.execute(
            insert_or_ignore(conn, payments),
            {"payment_id": f"PAY-{claim_id}", "claim_id": claim_id, "amount": amount, "status": status},
        )
    return get_payment(engine, claim_id)


def sign_off(engine: Engine, ref: Reference, payment_id: str, handler_id: str) -> Payment:
    """Record who signed off and when, and put the claim back in progress. It does not pay: the agent does."""
    handler = next((h for h in ref.handlers if h["handler_id"] == handler_id), None)
    if handler is None or not handler["can_approve_over_limit"]:
        raise PermissionError(f"{handler_id} cannot approve payments over the limit")
    if _get(engine, payments.c.payment_id, payment_id) is None:
        raise LookupError(payment_id)
    with engine.begin() as conn:
        signed = conn.execute(
            update(payments)
            .where(payments.c.payment_id == payment_id, payments.c.status == "held")
            .values(status="signed_off", approved_by=handler_id, approved_at=func.now(), updated_at=func.now())
            .returning(payments.c.claim_id)
        ).scalar_one_or_none()
        if signed is not None:
            conn.execute(
                update(claims).where(claims.c.claim_id == signed, claims.c.status == "awaiting_approval")
                .values(status="in_progress")
            )
    return _get(engine, payments.c.payment_id, payment_id)


def pay(engine: Engine, ref: Reference, claim_id: str) -> dict:
    """Pay the recorded approval once and return the claim. The amount is the decision's, never the caller's."""
    limit = ref.company["payout_approval_limit"]
    with engine.begin() as conn:
        decision = conn.execute(
            select(decisions.c.decision, decisions.c.payout).where(decisions.c.claim_id == claim_id)
        ).first()
        if decision is None or decision[0] != "approve":
            raise ValueError(f"{claim_id} has no approval to pay")
        payment = conn.execute(select(payments).where(payments.c.claim_id == claim_id)).mappings().first()
        if decision[1] > limit and (payment is None or payment["approved_by"] is None):
            raise SignOffRequired(f"{claim_id}: a payout of ${decision[1]:,.2f} needs a handler's sign-off")
        if payment is None:
            conn.execute(
                insert_or_ignore(conn, payments).values(
                    payment_id=f"PAY-{claim_id}", claim_id=claim_id, amount=decision[1], status="paid", paid_at=func.now(),
                )
            )
        elif payment["status"] != "paid":
            conn.execute(
                update(payments).where(payments.c.claim_id == claim_id)
                .values(status="paid", paid_at=func.now(), updated_at=func.now())
            )
        elif payment["paid_at"] is None:  # paid through the claim agent's request_payment tool
            conn.execute(update(payments).where(payments.c.claim_id == claim_id).values(paid_at=func.now()))
        conn.execute(
            update(claims).where(claims.c.claim_id == claim_id, claims.c.status.in_(("in_progress", "awaiting_approval")))
            .values(status="decided")
        )
    return get_claim(engine, claim_id)
