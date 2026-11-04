import json

from sqlalchemy import Engine, delete, func, insert, select, update

from app.agent import Agent, Decision, rationale_warnings
from app.db import claims, payments, trace_events
from app.claims import hold_for_sign_off, needs_sign_off, record_decision
from app.payments import pay
from app.reference import Reference
from app.runs import todo_for_claim


class _Trace:
    """Appends events to a claim's trace, carrying on the numbering of earlier attempts."""

    def __init__(self, engine: Engine, claim_id: str, run_id: int | None):
        self.engine, self.claim_id, self.run_id = engine, claim_id, run_id
        with engine.connect() as conn:
            self.seq = conn.execute(
                select(func.coalesce(func.max(trace_events.c.seq), 0)).where(trace_events.c.claim_id == claim_id)
            ).scalar_one()

    def __call__(self, kind: str, content: dict) -> None:
        self.seq += 1
        with self.engine.begin() as conn:
            conn.execute(insert(trace_events).values(
                claim_id=self.claim_id, seq=self.seq, kind=kind, content=json.dumps(content), run_id=self.run_id,
            ))


def _record_usage(trace: _Trace, agent: Agent) -> None:
    usage = getattr(agent, "usage", None)
    if usage:
        trace("usage", usage)


def _check_payment(engine: Engine, claim_id: str, decision: Decision) -> None:
    """A payment the agent asked for must match the payout it decided."""
    with engine.begin() as conn:
        amount = conn.execute(select(payments.c.amount).where(payments.c.claim_id == claim_id)).scalar_one_or_none()
        if amount is None:
            return
        if decision.decision != "approve":
            conn.execute(update(payments).where(payments.c.claim_id == claim_id).values(status="void", updated_at=func.now()))
        elif abs(amount - decision.payout) > 0.005:
            raise ValueError(f"the payment of {amount} does not match the payout of {decision.payout}")


def _fail(engine, trace, claim_id, run_id, attempt, max_attempts, exc) -> str:
    """Undo a failed attempt and put the claim back in the queue, or leave it for a handler."""
    message = f"{type(exc).__name__}: {exc}"
    status = "waiting" if attempt < max_attempts else "needs_handler"
    with engine.begin() as conn:
        conn.execute(delete(payments).where(payments.c.claim_id == claim_id))
        conn.execute(update(claims).where(claims.c.claim_id == claim_id).values(status=status, attempts=attempt, last_error=message))
    then = "It goes back in the queue." if status == "waiting" else "It is left for a handler."
    trace("error", {"message": f"Attempt {attempt} of {max_attempts} failed ({message}). {then}"})
    if run_id is not None:
        todo_for_claim(engine, run_id, claim_id, "todo" if status == "waiting" else "left_for_handler", message)
    if status == "waiting":
        return f"{claim_id}: attempt {attempt} of {max_attempts} failed ({message}). It is back in the queue: try it again after the others."
    return f"{claim_id}: attempt {attempt} of {max_attempts} failed ({message}). Left for a handler."


def work_claim(
    engine: Engine,
    ref: Reference,
    agent: Agent,
    claim_id: str,
    run_id: int | None = None,
    max_attempts: int = 2,
) -> str:
    """Make one attempt at one waiting claim and report the outcome in one line.

    A failed attempt is undone: the claim goes back to waiting, or, after max_attempts
    failures, is left for a handler. A claim that is not waiting is never touched.
    """
    with engine.begin() as conn:
        row = conn.execute(select(claims.c.status, claims.c.attempts).where(claims.c.claim_id == claim_id)).mappings().first()
        if row is None:
            return f"{claim_id}: not worked, there is no such claim."
        taken = conn.execute(
            update(claims).where(claims.c.claim_id == claim_id, claims.c.status == "waiting")
            .values(status="in_progress", run_id=run_id)
        )
    if taken.rowcount == 0:
        return f"{claim_id}: not worked, the claim is {row['status'].replace('_', ' ')}."

    attempt = row["attempts"] + 1  # claims.attempts counts the failed ones
    trace = _Trace(engine, claim_id, run_id)
    trace("status", {"status": "in_progress", "model": agent.model_id, "attempt": attempt})
    if run_id is not None:
        todo_for_claim(engine, run_id, claim_id, "in_progress")
    try:
        # the runner records the final decision itself, after the payment check
        decision = agent.decide(claim_id, lambda kind, content: None if kind == "decision" else trace(kind, content))
        _check_payment(engine, claim_id, decision)
    except Exception as exc:  # any model, tool or payment failure fails this attempt only
        _record_usage(trace, agent)
        return _fail(engine, trace, claim_id, run_id, attempt, max_attempts, exc)

    for warning in rationale_warnings(decision.rationale):
        trace("warning", {"message": warning})
    outcome = decision.decision
    record_decision(engine, ref, claim_id, decision.model_dump(), agent.model_id)
    if decision.decision == "approve":
        if needs_sign_off(engine, ref, claim_id):
            hold_for_sign_off(engine, ref, claim_id)
            outcome = f"approve ${decision.payout:,.2f}, held for sign-off"
        else:
            pay(engine, ref, claim_id)
            outcome = f"approve ${decision.payout:,.2f}, paid"
    trace("decision", decision.model_dump())
    _record_usage(trace, agent)
    if run_id is not None:
        todo_for_claim(engine, run_id, claim_id, "done", outcome)
    return f"{claim_id}: {outcome}"
