"""What the backend records about a claim's outcome. A step can run again after a restart, so a repeat of
what is already recorded counts as done, and anything different is refused."""
import json

from sqlalchemy import Engine, select, update

from app.db import claims, decisions, insert_or_ignore, iso, payments
from app.documents import Documents
from app.reference import Reference
from app.settings_store import approval_limit


class DecisionConflict(Exception):
    """A different decision on a claim that already has one."""


def get_claim(engine: Engine, claim_id: str) -> dict:
    """The claim's state and its decision, if it has one. Raises LookupError for an unknown claim."""
    with engine.connect() as conn:
        row = conn.execute(select(claims).where(claims.c.claim_id == claim_id)).mappings().first()
        decision = conn.execute(select(decisions).where(decisions.c.claim_id == claim_id)).mappings().first()
    if row is None:
        raise LookupError(f"no claim {claim_id}")
    return {**{k: v for k, v in row.items() if k != "payload"}, "decision": _decision(decision)}


def get_claim_payload(engine: Engine, claim_id: str) -> dict:
    """The claim as the customer wrote it. Raises LookupError for an unknown claim."""
    with engine.connect() as conn:
        payload = conn.execute(select(claims.c.payload).where(claims.c.claim_id == claim_id)).scalar_one_or_none()
    if payload is None:
        raise LookupError(f"no claim {claim_id}")
    return json.loads(payload)


def _decision(row) -> dict | None:
    if row is None:
        return None
    return {
        **{k: row[k] for k in ("decision", "payout", "rationale", "model", "manual_version", "wording_version")},
        "calculation": json.loads(row["calculation"]),
        "citations": json.loads(row["citations"]),
        "created_at": iso(row["created_at"]),
    }


def record_decision(engine: Engine, ref: Reference, claim_id: str, decision: dict, model: str) -> dict:
    """Record the claim's one decision and return the claim. An approval stays in progress until it is paid."""
    docs = Documents(engine)
    limit = approval_limit(engine, ref)
    with engine.begin() as conn:
        conn.execute(
            insert_or_ignore(conn, decisions),
            {
                "claim_id": claim_id,
                "decision": decision["decision"],
                "payout": decision["payout"],
                "calculation": json.dumps(decision["calculation"]),
                "rationale": decision["rationale"],
                "citations": json.dumps(decision["citations"]),
                "model": model,
                "manual_version": docs.version("claims_manual"),
                "wording_version": docs.version("policy_wording"),
                "approval_limit": limit,
            },
        )
        recorded = conn.execute(
            select(decisions.c.decision, decisions.c.payout).where(decisions.c.claim_id == claim_id)
        ).one()
        if tuple(recorded) != (decision["decision"], decision["payout"]):
            raise DecisionConflict(
                f"{claim_id} is already decided: {recorded[0]} ${recorded[1]:,.2f}, "
                f"not {decision['decision']} ${decision['payout']:,.2f}"
            )
        if decision["decision"] != "approve":
            conn.execute(
                update(claims).where(claims.c.claim_id == claim_id, claims.c.status == "in_progress").values(status="decided")
            )
    return get_claim(engine, claim_id)


def _limit_of(recorded: float | None, ref: Reference) -> float:
    return recorded if recorded is not None else ref.company["payout_approval_limit"]


def needs_sign_off(engine: Engine, ref: Reference, claim_id: str) -> bool:
    """The backend's answer, for the agent: is this an approval over the limit that no handler has signed off?"""
    with engine.connect() as conn:
        row = conn.execute(
            select(decisions.c.decision, decisions.c.payout, payments.c.approved_by, decisions.c.approval_limit)
            .select_from(decisions.outerjoin(payments, decisions.c.claim_id == payments.c.claim_id))
            .where(decisions.c.claim_id == claim_id)
        ).first()
    # the limit the decision was recorded under, so a new limit never reaches back to a decision already held
    return row is not None and row[0] == "approve" and row[1] > _limit_of(row[3], ref) and row[2] is None


def hold_for_sign_off(engine: Engine, ref: Reference, claim_id: str, reason: str = "over_limit") -> dict:
    """Put the payout on the approvals list, once, and return the claim. A signed-off payment stays as it is.
    `reason` says why it waits: over_limit, or gateway_refused."""
    with engine.begin() as conn:
        amount = conn.execute(select(decisions.c.payout).where(decisions.c.claim_id == claim_id)).scalar_one()
        conn.execute(
            insert_or_ignore(conn, payments),
            {"payment_id": f"PAY-{claim_id}", "claim_id": claim_id, "amount": amount, "status": "held", "hold_reason": reason},
        )
        status = conn.execute(select(payments.c.status).where(payments.c.claim_id == claim_id)).scalar_one()
        if status == "held":
            conn.execute(
                update(claims).where(claims.c.claim_id == claim_id, claims.c.status == "in_progress")
                .values(status="awaiting_approval")
            )
    return get_claim(engine, claim_id)


def hold_after_gateway_refusal(engine: Engine, ref: Reference, claim_id: str) -> bool:
    """The Gateway refused this claim's payment: wait for a handler's sign-off, as an approval over the limit does.
    Only an approved claim in progress, or one already waiting this way, with no payment made. Returns whether this
    call put it on hold; anything else is a ValueError."""
    from app.payments import get_payment  # payments imports this module

    state = get_claim(engine, claim_id)
    decision, payment = state["decision"], get_payment(engine, claim_id)
    if (decision or {}).get("decision") != "approve" or state["status"] not in ("in_progress", "awaiting_approval"):
        raise ValueError(f"{claim_id} is not an approved claim waiting for its payment")
    if payment is not None and payment.status != "held":
        raise ValueError(f"{claim_id} is already paid")
    first_time = payment is None
    hold_for_sign_off(engine, ref, claim_id, "gateway_refused")
    return first_time


# --- what the agent's worker did to the claim itself, now the backend's -------------------------------

def take_claim(engine: Engine, claim_id: str, run_id: int | None) -> dict:
    """Take a waiting claim for a new attempt: in progress, on a thread of its own. Says whether it was taken, and
    if not, the status it was found in. A claim that is not waiting is never touched."""
    with engine.begin() as conn:
        row = conn.execute(select(claims.c.status, claims.c.attempts).where(claims.c.claim_id == claim_id)).mappings().first()
        if row is None:
            raise LookupError(f"no claim {claim_id}")
        attempt = row["attempts"] + 1  # claims.attempts counts the failed ones
        thread_id = f"{claim_id}-{attempt}"
        taken = conn.execute(
            update(claims).where(claims.c.claim_id == claim_id, claims.c.status == "waiting")
            .values(status="in_progress", run_id=run_id, thread_id=thread_id)
        ).rowcount == 1
    return {"taken": taken, "status": row["status"], "attempt": attempt, "thread_id": thread_id}


def fail_claim(engine: Engine, claim_id: str, attempt: int, max_attempts: int, message: str) -> str:
    """An error with no decision yet fails the attempt: back in the queue for the next one, on a new thread, or left
    for a handler after the last. Returns the new status."""
    status = "waiting" if attempt < max_attempts else "needs_handler"
    with engine.begin() as conn:
        conn.execute(update(claims).where(claims.c.claim_id == claim_id).values(
            status=status, attempts=attempt, last_error=message, thread_id=None,
        ))
    return status


def set_claim_run(engine: Engine, claim_id: str, run_id: int | None) -> None:
    """The run a claim's steps are traced under, when a run carries it on."""
    with engine.begin() as conn:
        conn.execute(update(claims).where(claims.c.claim_id == claim_id).values(run_id=run_id))


def claim_state(engine: Engine, ref: Reference, claim_id: str) -> dict:
    """Everything the agent needs to settle a claim, in one read: where it stands, its decision and payment, and
    whether its payout still needs a handler's sign-off."""
    from app.payments import get_payment  # payments imports this module

    with engine.connect() as conn:
        row = conn.execute(
            select(claims.c.status, claims.c.thread_id, claims.c.attempts, claims.c.run_id, claims.c.copy_of, claims.c.last_error)
            .where(claims.c.claim_id == claim_id)
        ).mappings().first()
    if row is None:
        raise LookupError(f"no claim {claim_id}")
    decision = get_claim(engine, claim_id)["decision"]
    payment = get_payment(engine, claim_id)
    return {**row, "decision": decision, "payment": None if payment is None else payment.__dict__,
            "needs_sign_off": needs_sign_off(engine, ref, claim_id)}
