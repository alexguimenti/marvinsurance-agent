"""A senior handler sends a claim waiting for sign-off back to the agent with a note, and the agent decides again. The decision
that was sent back stays on the claim, with the note, who sent it back and when. A claim is sent back from awaiting_approval
only, by one conditional update of the claim, which is also what decides between a sign-off and a send back made at the same
moment. A sent-back claim is taken for work once, by whoever asks first: the agent's resume, or a dispatch."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import Engine, func, insert, select, update

from app.db import claims, decisions, payments, payout_calculations, sent_back_decisions
from app.payments import NotWaiting
from app.reference import Reference
from app.runs import add_trace_event

MAX_NOTE = 1000
HEARD_WITHIN = timedelta(minutes=5)  # a resume the app has heard from this recently holds its claim


def send_back(engine: Engine, ref: Reference, claim_id: str, note: str, handler_id: str) -> None:
    """Send the claim's decision back with a note. Raises PermissionError for a handler who cannot sign off over the limit,
    ValueError for a note that is blank or too long, and NotWaiting (a 409) for a claim that is not waiting for a sign-off
    and for an evaluation's copy."""
    handler = next((h for h in ref.handlers if h["handler_id"] == handler_id), None)
    if handler is None or not handler["can_approve_over_limit"]:
        raise PermissionError(f"{handler_id} cannot send payments over the limit back")
    note = note.strip()
    if not note or len(note) > MAX_NOTE:
        raise ValueError(f"the note needs 1 to {MAX_NOTE} characters")
    with engine.begin() as conn:
        taken = conn.execute(
            update(claims).where(claims.c.claim_id == claim_id, claims.c.status == "awaiting_approval", claims.c.copy_of.is_(None))
            .values(status="sent_back").returning(claims.c.claim_id)
        ).scalar()
        if taken is None:
            copy_of = conn.execute(select(claims.c.copy_of).where(claims.c.claim_id == claim_id)).first()
            if copy_of is None:
                raise LookupError(claim_id)
            if copy_of[0] is not None:
                raise NotWaiting("an evaluation's copy cannot be sent back")
            raise NotWaiting(f"{claim_id} is not waiting for a sign-off")
        kept = conn.execute(select(decisions).where(decisions.c.claim_id == claim_id)).mappings().first()
        if kept is not None:
            conn.execute(insert(sent_back_decisions).values(
                claim_id=claim_id, decision=kept["decision"], payout=kept["payout"], calculation=kept["calculation"],
                rationale=kept["rationale"], citations=kept["citations"], decided_by=kept["decided_by"],
                manual_version=kept["manual_version"], decided_at=kept["created_at"], note=note,
                sent_back_by=handler_id, sent_back_at=func.now()))
        conn.execute(decisions.delete().where(decisions.c.claim_id == claim_id))
        conn.execute(payments.delete().where(payments.c.claim_id == claim_id, payments.c.status == "held"))
        conn.execute(payout_calculations.delete().where(payout_calculations.c.claim_id == claim_id))
        add_trace_event(conn, claim_id, "sent_back", {
            "note": note, "sent_back_by": handler_id, "sent_back_by_name": handler["name"],
            "decision": kept["decision"] if kept else None, "payout": kept["payout"] if kept else None})


def take_sent_back(engine: Engine, claim_id: str) -> bool:
    """Take a sent-back claim for work: sent back to in progress, once. True only for the first caller."""
    with engine.begin() as conn:
        return bool(conn.execute(
            update(claims).where(claims.c.claim_id == claim_id, claims.c.status == "sent_back")
            .values(status="in_progress", resume_heard_at=func.now())
        ).rowcount)


def heard_from_resume(engine: Engine, claim_id: str) -> None:
    """The agent says it is still working a resumed claim."""
    with engine.begin() as conn:
        conn.execute(update(claims).where(claims.c.claim_id == claim_id).values(resume_heard_at=func.now()))


def held_by_a_resume(heard_at: datetime | None) -> bool:
    """Whether a resume has told the app it is working within the last five minutes. SQLite's timestamps are naive, and UTC."""
    if heard_at is None:
        return False
    if heard_at.tzinfo is None:
        heard_at = heard_at.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - heard_at < HEARD_WITHIN


def latest_sent_back(engine: Engine, ref: Reference, claim_id: str) -> dict | None:
    """The last decision sent back on a claim, with its note, who sent it back and when."""
    with engine.connect() as conn:
        row = conn.execute(select(sent_back_decisions).where(sent_back_decisions.c.claim_id == claim_id)
                           .order_by(sent_back_decisions.c.id.desc()).limit(1)).mappings().first()
    if row is None:
        return None
    handler = next((h for h in ref.handlers if h["handler_id"] == row["sent_back_by"]), None)
    return {"note": row["note"], "sent_back_by": row["sent_back_by"],
            "sent_back_by_name": handler["name"] if handler else row["sent_back_by"], "sent_back_at": row["sent_back_at"]}
