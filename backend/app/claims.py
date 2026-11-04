"""What the backend records about a claim's outcome. A step can run again after a restart, so a repeat of
what is already recorded counts as done, and anything different is refused."""
import json

from sqlalchemy import Engine, select, update

from app.db import claims, decisions, insert_or_ignore, iso, payments
from app.documents import documents_for
from app.reference import Reference


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
    docs = documents_for(ref)
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


def needs_sign_off(engine: Engine, ref: Reference, claim_id: str) -> bool:
    """The backend's answer, for the agent: is this an approval over the limit that no handler has signed off?"""
    limit = ref.company["payout_approval_limit"]
    with engine.connect() as conn:
        decision = conn.execute(
            select(decisions.c.decision, decisions.c.payout).where(decisions.c.claim_id == claim_id)
        ).first()
        approved_by = conn.execute(select(payments.c.approved_by).where(payments.c.claim_id == claim_id)).scalar_one_or_none()
    return decision is not None and decision[0] == "approve" and decision[1] > limit and approved_by is None


def hold_for_sign_off(engine: Engine, ref: Reference, claim_id: str) -> dict:
    """Put the payout on the approvals list, once, and return the claim. A signed-off payment stays as it is."""
    with engine.begin() as conn:
        amount = conn.execute(select(decisions.c.payout).where(decisions.c.claim_id == claim_id)).scalar_one()
        conn.execute(
            insert_or_ignore(conn, payments),
            {"payment_id": f"PAY-{claim_id}", "claim_id": claim_id, "amount": amount, "status": "held"},
        )
        status = conn.execute(select(payments.c.status).where(payments.c.claim_id == claim_id)).scalar_one()
        if status == "held":
            conn.execute(
                update(claims).where(claims.c.claim_id == claim_id, claims.c.status == "in_progress")
                .values(status="awaiting_approval")
            )
    return get_claim(engine, claim_id)
