from dataclasses import dataclass

from sqlalchemy import Engine, func, select, update

from app.claims import get_claim
from app.settings_store import approval_limit
from app.db import claims, decisions, insert_or_ignore, iso, payments
from app.reference import Reference
from app.runs import add_trace_event


class OverLimit(Exception):
    """A payment over the approval limit: the system refuses it, on any claim, before it looks at the claim."""


class SignOffRequired(Exception):
    """A payout over the limit that no handler has signed off. The system refuses it, whatever the agent does."""


class NotWaiting(Exception):
    """The claim is not waiting for a handler's sign-off (it was settled, sent back or never held)."""


@dataclass
class Payment:
    payment_id: str
    claim_id: str
    amount: float
    status: str  # held -> paid by a sign-off, or paid straight away within the limit
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
    """Pay a held payout on a handler's sign-off. It goes through only while the claim is waiting for sign-off: the claim's
    one conditional update from awaiting_approval to decided is what decides between a sign-off and a send back made at the
    same moment, and the loser changes nothing. Then the payment is recorded with who and when, and the paid step is added to
    the claim's trace. The agent is not needed. A repeat changes nothing, and one that stopped halfway is finished."""
    handler = next((h for h in ref.handlers if h["handler_id"] == handler_id), None)
    if handler is None or not handler["can_approve_over_limit"]:
        raise PermissionError(f"{handler_id} cannot approve payments over the limit")
    owner = select(payments.c.claim_id).where(payments.c.payment_id == payment_id).scalar_subquery()
    with engine.begin() as conn:
        taken = conn.execute(
            update(claims).where(claims.c.claim_id == owner, claims.c.status == "awaiting_approval", claims.c.copy_of.is_(None))
            .values(status="decided").returning(claims.c.claim_id)
        ).scalar()
        if taken is None:  # this call did not take the claim: say why, or finish what an earlier call left
            found = conn.execute(
                select(payments, claims.c.copy_of, claims.c.status.label("claim_status"))
                .select_from(payments.join(claims, payments.c.claim_id == claims.c.claim_id))
                .where(payments.c.payment_id == payment_id)
            ).mappings().first()
            if found is None:
                # a send back deletes the held payment: a sign-off that lost to it is told so, not that the payment is unknown
                owner_claim = conn.execute(select(claims.c.status).where(claims.c.claim_id == payment_id.removeprefix("PAY-"))).scalar()
                if owner_claim is not None and owner_claim != "decided":
                    raise NotWaiting(f"{payment_id.removeprefix('PAY-')} is not waiting for a sign-off")
                raise LookupError(payment_id)
            if found["copy_of"] is not None:
                raise PermissionError("an evaluation's copy cannot be signed off")
            if found["status"] == "paid":
                return _payment(found)
            if found["claim_status"] != "decided":
                raise NotWaiting(f"{found['claim_id']} is not waiting for a sign-off")
        row = conn.execute(
            update(payments).where(payments.c.payment_id == payment_id, payments.c.status == "held")
            .values(status="paid", approved_by=handler_id, approved_at=func.now(), paid_at=func.now(), updated_at=func.now())
            .returning(payments)
        ).mappings().first()
        if row is None:
            raise LookupError(payment_id)
        add_trace_event(conn, row["claim_id"], "status", {"status": "paid", "amount": row["amount"], "approved_by": handler_id})
    return _payment(row)


def pay(engine: Engine, ref: Reference, claim_id: str) -> dict:
    """Pay the recorded approval once and return the claim, saying whether this call paid it. The amount is the
    decision's, never the caller's. If it stops halfway, running it again finishes the job."""
    with engine.begin() as conn:
        found = conn.execute(
            select(decisions.c.decision, decisions.c.payout, payments.c.status, payments.c.approved_by, decisions.c.approval_limit)
            .select_from(decisions.outerjoin(payments, decisions.c.claim_id == payments.c.claim_id))
            .where(decisions.c.claim_id == claim_id)
        ).first()
        if found is None or found[0] != "approve":
            raise ValueError(f"{claim_id} has no approval to pay")
        decision, amount, status, approved_by, recorded_limit = found
        limit = recorded_limit if recorded_limit is not None else ref.company["payout_approval_limit"]
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


def pay_claim(engine: Engine, ref: Reference, claim_id: str, amount: float) -> dict:
    """The agent's payment. An amount over the approval limit is refused first, on any claim, naming the limit; then
    an amount other than the decision's payout is refused; then it is paid as `pay` does, once."""
    limit = approval_limit(engine, ref)
    if amount > limit:
        raise OverLimit(f"a payment of ${amount:,.2f} is over the approval limit of ${limit:,.2f}")
    with engine.connect() as conn:
        found = conn.execute(select(decisions.c.decision, decisions.c.payout).where(decisions.c.claim_id == claim_id)).first()
    if found is None or found[0] != "approve":
        raise ValueError(f"{claim_id} has no approval to pay")
    if round(amount, 2) != round(found[1], 2):
        raise ValueError(f"{claim_id}: the payment is ${found[1]:,.2f}, not ${amount:,.2f}")
    return pay(engine, ref, claim_id)
