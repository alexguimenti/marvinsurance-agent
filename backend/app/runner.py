import json
import sqlite3

from app.agent import Agent, Decision, rationale_warnings
from app.documents import Documents
from app.payments import request_payment
from app.reference import Reference


class _Trace:
    """Appends events to a claim's trace, carrying on the numbering of earlier attempts."""

    def __init__(self, conn: sqlite3.Connection, claim_id: str, run_id: int | None):
        self.conn, self.claim_id, self.run_id = conn, claim_id, run_id
        self.seq = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) FROM trace_events WHERE claim_id = ?", (claim_id,)
        ).fetchone()[0]

    def __call__(self, kind: str, content: dict) -> None:
        self.seq += 1
        self.conn.execute(
            "INSERT INTO trace_events (claim_id, seq, kind, content, run_id) VALUES (?, ?, ?, ?, ?)",
            (self.claim_id, self.seq, kind, json.dumps(content), self.run_id),
        )
        self.conn.commit()


def _record_usage(trace: _Trace, agent: Agent) -> None:
    usage = getattr(agent, "usage", None)
    if usage:
        trace("usage", usage)


def _check_payment(conn: sqlite3.Connection, claim_id: str, decision: Decision) -> None:
    """A payment the agent asked for must match the payout it decided."""
    existing = conn.execute("SELECT amount FROM payments WHERE claim_id = ?", (claim_id,)).fetchone()
    if existing is None:
        return
    if decision.decision != "approve":
        conn.execute("UPDATE payments SET status='void', updated_at=CURRENT_TIMESTAMP WHERE claim_id = ?", (claim_id,))
    elif abs(existing["amount"] - decision.payout) > 0.005:
        raise ValueError(f"the payment of {existing['amount']} does not match the payout of {decision.payout}")


def _fail(conn, trace, claim_id, attempt, max_attempts, exc) -> str:
    """Undo a failed attempt and put the claim back in the queue, or leave it for a handler."""
    message = f"{type(exc).__name__}: {exc}"
    conn.execute("DELETE FROM payments WHERE claim_id = ?", (claim_id,))
    status = "waiting" if attempt < max_attempts else "needs_handler"
    conn.execute(
        "UPDATE claims SET status = ?, attempts = ?, last_error = ? WHERE claim_id = ?",
        (status, attempt, message, claim_id),
    )
    conn.commit()
    then = "It goes back in the queue." if status == "waiting" else "It is left for a handler."
    trace("error", {"message": f"Attempt {attempt} of {max_attempts} failed ({message}). {then}"})
    if status == "waiting":
        return f"{claim_id}: attempt {attempt} of {max_attempts} failed ({message}). It is back in the queue: try it again after the others."
    return f"{claim_id}: attempt {attempt} of {max_attempts} failed ({message}). Left for a handler."


def work_claim(
    conn: sqlite3.Connection,
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
    row = conn.execute("SELECT status, attempts FROM claims WHERE claim_id = ?", (claim_id,)).fetchone()
    if row is None:
        return f"{claim_id}: not worked, there is no such claim."
    taken = conn.execute(
        "UPDATE claims SET status='in_progress', run_id = ? WHERE claim_id = ? AND status='waiting'",
        (run_id, claim_id),
    )
    conn.commit()
    if taken.rowcount == 0:
        return f"{claim_id}: not worked, the claim is {row['status'].replace('_', ' ')}."

    attempt = row["attempts"] + 1  # claims.attempts counts the failed ones
    trace = _Trace(conn, claim_id, run_id)
    trace("status", {"status": "in_progress", "model": agent.model_id, "attempt": attempt})
    try:
        # the runner records the final decision itself, after the payment check
        decision = agent.decide(claim_id, lambda kind, content: None if kind == "decision" else trace(kind, content))
        _check_payment(conn, claim_id, decision)
    except Exception as exc:  # any model, tool or payment failure fails this attempt only
        _record_usage(trace, agent)
        return _fail(conn, trace, claim_id, attempt, max_attempts, exc)

    for warning in rationale_warnings(decision.rationale):
        trace("warning", {"message": warning})
    status, outcome = "decided", decision.decision
    if decision.decision == "approve":
        payment = request_payment(conn, claim_id, decision.payout, ref.company["payout_approval_limit"])
        held = payment.status == "held"
        status = "awaiting_approval" if held else "decided"
        outcome = f"approve ${decision.payout:,.2f}, {'held for sign-off' if held else 'paid'}"

    docs = Documents(ref)
    conn.execute(
        "INSERT INTO decisions (claim_id, decision, payout, calculation, rationale, citations, model, "
        "manual_version, wording_version) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            claim_id,
            decision.decision,
            decision.payout,
            json.dumps([s.model_dump() for s in decision.calculation]),
            decision.rationale,
            json.dumps(decision.citations),
            agent.model_id,
            docs.version("claims_manual"),
            docs.version("policy_wording"),
        ),
    )
    conn.execute("UPDATE claims SET status = ? WHERE claim_id = ?", (status, claim_id))
    conn.commit()
    trace("decision", decision.model_dump())
    _record_usage(trace, agent)
    return f"{claim_id}: {outcome}"
