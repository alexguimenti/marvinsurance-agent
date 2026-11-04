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


def get_payment(engine: Engine, claim_id: str) -> Payment | None:
    with engine.connect() as conn:
        row = conn.execute(select(payments).where(payments.c.claim_id == claim_id)).mappings().first()
    return None if row is None else _payment(row)


def _payment(row) -> Payment:
    return Payment(row["payment_id"], row["claim_id"], row["amount"], row["status"], row["approved_by"],
                   iso(row["approved_at"]), iso(row["paid_at"]))


def sign_off(engine: Engine, ref: Reference, payment_id: str, handler_id: str) -> Payment:
    """Record who signed off and when, and put the claim back in progress. It does not pay: the agent does."""
    handler = next((h for h in ref.handlers if h["handler_id"] == handler_id), None)
    if handler is None or not handler["can_approve_over_limit"]:
        raise PermissionError(f"{handler_id} cannot approve payments over the limit")
    with engine.begin() as conn:
        row = conn.execute(
            update(payments)
            .where(payments.c.payment_id == payment_id, payments.c.status == "held")
            .values(status="signed_off", approved_by=handler_id, approved_at=func.now(), updated_at=func.now())
            .returning(payments)
        ).mappings().first()
        if row is None:  # already signed off or paid, or no such payment
            row = conn.execute(select(payments).where(payments.c.payment_id == payment_id)).mappings().first()
            if row is None:
                raise LookupError(payment_id)
        if row["status"] == "signed_off":
            conn.execute(
                update(claims).where(claims.c.claim_id == row["claim_id"], claims.c.status == "awaiting_approval")
                .values(status="in_progress")
            )
    return _payment(row)


def pay(engine: Engine, ref: Reference, claim_id: str) -> dict:
    """Pay the recorded approval once and return the claim, saying whether this call paid it. The amount is the
    decision's, never the caller's. If it stops halfway, running it again finishes the job."""
    limit = ref.company["payout_approval_limit"]
    with engine.begin() as conn:
        found = conn.execute(
            select(decisions.c.decision, decisions.c.payout, payments.c.status, payments.c.approved_by)
            .select_from(decisions.outerjoin(payments, decisions.c.claim_id == payments.c.claim_id))
            .where(decisions.c.claim_id == claim_id)
        ).first()
        if found is None or found[0] != "approve":
            raise ValueError(f"{claim_id} has no approval to pay")
        decision, amount, status, approved_by = found
        if amount > limit and approved_by is None:
            raise SignOffRequired(f"{claim_id}: a payout of ${amount:,.2f} needs a handler's sign-off")
        paid_now = False
        if status is None:
            paid_now = conn.execute(
                insert_or_ignore(conn, payments).values(
                    payment_id=f"PAY-{claim_id}", claim_id=claim_id, amount=amount, status="paid", paid_at=func.now(),
                )
            ).rowcount == 1
        elif status != "paid":
            paid_now = conn.execute(
                update(payments).where(payments.c.claim_id == claim_id, payments.c.status != "paid")
                .values(status="paid", paid_at=func.now(), updated_at=func.now())
            ).rowcount == 1
        conn.execute(
            update(claims).where(claims.c.claim_id == claim_id, claims.c.status.in_(("in_progress", "awaiting_approval")))
            .values(status="decided")
        )
    return {**get_claim(engine, claim_id), "paid_now": paid_now}
