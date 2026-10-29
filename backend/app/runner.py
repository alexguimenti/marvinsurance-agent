import json
import sqlite3
import time

from app.agent import Agent, Decision, rationale_warnings
from app.payments import request_payment
from app.reference import Reference


class _Trace:
    def __init__(self, conn: sqlite3.Connection, claim_id: str):
        self.conn, self.claim_id, self.seq = conn, claim_id, 0

    def __call__(self, kind: str, content: dict) -> None:
        self.seq += 1
        self.conn.execute(
            "INSERT INTO trace_events (claim_id, seq, kind, content) VALUES (?, ?, ?, ?)",
            (self.claim_id, self.seq, kind, json.dumps(content)),
        )
        self.conn.commit()


def _set_status(conn: sqlite3.Connection, claim_id: str, status: str) -> None:
    conn.execute("UPDATE claims SET status = ? WHERE claim_id = ?", (status, claim_id))
    conn.commit()


def _refer(reason: str) -> Decision:
    return Decision(
        decision="refer",
        rationale=f"The automated review could not settle this claim, so it is referred to a handler. {reason}",
    )


def _void_payment(conn: sqlite3.Connection, claim_id: str) -> None:
    conn.execute(
        "UPDATE payments SET status='void', updated_at=CURRENT_TIMESTAMP WHERE claim_id = ?", (claim_id,)
    )
    conn.commit()


def _drop_payment(conn: sqlite3.Connection, claim_id: str) -> None:
    """Forget a payment made by an attempt that did not stand, so the next attempt starts clean."""
    conn.execute("DELETE FROM payments WHERE claim_id = ?", (claim_id,))
    conn.commit()


def _add_usage(total: dict | None, usage: dict | None) -> dict | None:
    if not usage:
        return total
    if not total:
        return dict(usage)
    costs = [c for c in (total["cost_usd"], usage["cost_usd"]) if c is not None]
    return {
        "calls": total["calls"] + usage["calls"],
        "input_tokens": total["input_tokens"] + usage["input_tokens"],
        "output_tokens": total["output_tokens"] + usage["output_tokens"],
        "reasoning_tokens": total["reasoning_tokens"] + usage["reasoning_tokens"],
        "cost_usd": round(sum(costs), 8) if costs else None,
    }


def run_claim(
    conn: sqlite3.Connection,
    ref: Reference,
    agent: Agent,
    claim_id: str,
    max_attempts: int = 1,
    retry_delay: float = 0.0,
) -> Decision | None:
    """Run the agent on one waiting claim. Returns None when the claim is not waiting.

    A failed attempt (the model errors, or ends without a decision) is tried again, up to
    max_attempts times in all. Only when the last attempt fails is the claim referred.
    """
    taken = conn.execute(
        "UPDATE claims SET status='in_progress' WHERE claim_id = ? AND status='waiting'", (claim_id,)
    )
    conn.commit()
    if taken.rowcount == 0:
        return None
    trace = _Trace(conn, claim_id)
    trace("status", {"status": "in_progress", "model": agent.model_id})

    total_usage: dict | None = None
    decision = None
    for attempt in range(1, max_attempts + 1):
        try:
            # the runner records the final decision itself, after payment checks
            decision = agent.decide(claim_id, lambda kind, content: None if kind == "decision" else trace(kind, content))
            total_usage = _add_usage(total_usage, getattr(agent, "usage", None))
            break
        except Exception as exc:  # any model or tool failure: try again, and refer only when out of attempts
            total_usage = _add_usage(total_usage, getattr(agent, "usage", None))
            message = f"{type(exc).__name__}: {exc}"
            if attempt < max_attempts:
                trace("retry", {"message": f"Attempt {attempt} of {max_attempts} failed ({message}). Trying again."})
                _drop_payment(conn, claim_id)
                if retry_delay:
                    time.sleep(retry_delay)
            else:
                trace("error", {"message": message})
                decision = _refer("Reason: the agent failed, see the activity trace.")

    for warning in rationale_warnings(decision.rationale):
        trace("warning", {"message": warning})

    limit = ref.company["payout_approval_limit"]
    existing = conn.execute("SELECT amount FROM payments WHERE claim_id = ?", (claim_id,)).fetchone()
    new_status = "decided"
    if decision.decision == "approve":
        if existing is not None and abs(existing["amount"] - decision.payout) > 0.005:
            trace("error", {"message": f"payment of {existing['amount']} does not match payout {decision.payout}"})
            _void_payment(conn, claim_id)
            decision = _refer("Reason: the requested payment did not match the decided payout.")
        else:
            payment = request_payment(conn, claim_id, decision.payout, limit)
            if payment.status == "held":
                new_status = "awaiting_approval"
    elif existing is not None:
        _void_payment(conn, claim_id)

    conn.execute(
        "INSERT OR REPLACE INTO decisions "
        "(claim_id, decision, payout, calculation, rationale, citations, model) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            claim_id,
            decision.decision,
            decision.payout,
            json.dumps([s.model_dump() for s in decision.calculation]),
            decision.rationale,
            json.dumps(decision.citations),
            agent.model_id,
        ),
    )
    _set_status(conn, claim_id, new_status)
    trace("decision", decision.model_dump())
    if total_usage:
        trace("usage", total_usage)
    return decision


def run_queue(
    conn: sqlite3.Connection,
    ref: Reference,
    agent: Agent,
    claim_ids: list[str] | None = None,
    max_attempts: int = 1,
    retry_delay: float = 0.0,
) -> list[str]:
    """Run the waiting claims (all of them, or only claim_ids) one at a time, in claim order."""
    ids = [r["claim_id"] for r in conn.execute("SELECT claim_id FROM claims WHERE status='waiting' ORDER BY claim_id")]
    if claim_ids is not None:
        wanted = set(claim_ids)
        ids = [i for i in ids if i in wanted]
    for claim_id in ids:
        run_claim(conn, ref, agent, claim_id, max_attempts, retry_delay)
    return ids
