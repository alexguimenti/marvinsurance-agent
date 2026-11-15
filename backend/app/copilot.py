"""The Copilot's records: each handler's conversations, the turns in them and the steps the sidebar shows.

A conversation belongs to the handler who started it, and every function that reads or changes one takes the handler's id and
treats another handler's as an unknown one (a LookupError). A conversation has one turn at a time: `copilot_conversations.open_turn_id`
is taken by one conditional UPDATE, as the work lock is, so two messages at the same moment start one turn. A turn is `working`,
`waiting` for the handler's answer to a confirmation, `done`, `failed` or `stopped`. A working turn the Copilot has not ended 20
minutes after it started (or after the handler's answer) is shown as stopped, and a new message may go ahead; a waiting turn is never
stopped, since nothing is working. The 20 minutes count from the Copilot's last word (a step begun or finished, or the handler's answer). Nothing here holds the handler's sign-in.

Every statement commits by itself (see `db.make_engine`), and a request uses one connection at a time."""
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Mapping

from sqlalchemy import Engine, delete, insert, select, update

from app.db import claims, copilot_conversations as conversations, copilot_steps as steps, copilot_turns as turns, decisions, iso, payments
from app.reference import Reference

STALE = timedelta(minutes=20)
TITLE_LIMIT = 120
WORKING_TURN = "the Copilot is still working on your last message"
WAITING_TURN = "answer the confirmation first"


class TurnOpen(Exception):
    """A turn is already working or waiting in the conversation. The message says which."""


class NotWaiting(Exception):
    """No turn of the conversation is waiting for an answer to a confirmation (or it was answered a moment ago)."""


class CardChanged(Exception):
    """The payout the confirmation card showed is no longer the one held (sent back and decided again, or gone): it cannot be signed off from
    this card."""


class TurnEnded(Exception):
    """The turn is no longer working: it ended, failed, stopped or is waiting for the handler."""


class NoCard(Exception):
    """There is no payout held for a sign-off on the claim, so there is nothing to confirm."""


def _utc(moment: datetime | None) -> datetime | None:
    """SQLite's timestamps come back naive, and are UTC."""
    return None if moment is None else (moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc))


def new_conversation_id() -> str:
    """49 characters of letters, digits and hyphens: the id is also the Runtime session id and the Memory's session."""
    return f"conversation-{uuid.uuid4()}"


def create_conversation(engine: Engine, handler_id: str, now: datetime | None = None) -> str:
    conversation_id, now = new_conversation_id(), now or datetime.now(timezone.utc)
    with engine.connect() as conn:
        conn.execute(insert(conversations).values(conversation_id=conversation_id, handler_id=handler_id, created_at=now, updated_at=now))
    return conversation_id


def _conversation(engine: Engine, conversation_id: str, handler_id: str) -> Mapping:
    with engine.connect() as conn:
        row = conn.execute(select(conversations).where(conversations.c.conversation_id == conversation_id)).mappings().first()
    if row is None or row["handler_id"] != handler_id:
        raise LookupError(f"unknown conversation {conversation_id}")
    return row


def list_conversations(engine: Engine, handler_id: str) -> list[dict]:
    """The handler's conversations, newest first."""
    query = select(conversations).where(conversations.c.handler_id == handler_id).order_by(conversations.c.updated_at.desc(), conversations.c.created_at.desc())
    with engine.connect() as conn:
        rows = conn.execute(query).mappings().all()
    return [{"conversation_id": r["conversation_id"], "title": r["title"] or "", "updated_at": iso(r["updated_at"])} for r in rows]


def shown_status(turn: Mapping, now: datetime) -> str:
    """A working turn that has gone 20 minutes with no word from the Copilot shows as stopped."""
    if turn["status"] != "working":
        return turn["status"]
    since = _utc(turn["resumed_at"]) or _utc(turn["started_at"])
    return "stopped" if since is not None and now - since > STALE else "working"


def _seconds(started: datetime | None, ended: datetime | None) -> float | None:
    return None if started is None or ended is None else round((_utc(ended) - _utc(started)).total_seconds(), 2)


def _step_view(row: Mapping) -> dict:
    return {"step_id": row["step_id"], "kind": row["kind"], "tool": row["tool"], "main_input": row["main_input"], "status": row["status"],
            "input": json.loads(row["input"]) if row["input"] else None, "output": row["output"],
            "sources": json.loads(row["sources"]) if row["sources"] else None, "reason": row["reason"],
            "started_at": iso(row["started_at"]), "seconds": _seconds(row["started_at"], row["ended_at"])}


def conversation_view(engine: Engine, conversation_id: str, handler_id: str, now: datetime) -> dict:
    """The conversation with each turn and its steps. Raises LookupError for an unknown conversation or another handler's."""
    conversation = _conversation(engine, conversation_id, handler_id)
    with engine.connect() as conn:
        turn_rows = conn.execute(select(turns).where(turns.c.conversation_id == conversation_id).order_by(turns.c.turn_id)).mappings().all()
        step_rows = conn.execute(select(steps).where(steps.c.conversation_id == conversation_id).order_by(steps.c.step_id)).mappings().all()
    out = []
    for t in turn_rows:
        out.append({"turn_id": t["turn_id"], "message": t["message"], "status": shown_status(t, now), "answer": t["answer"], "error": t["error"],
                    "model_calls": t["model_calls"], "cost_usd": t["cost_usd"], "seconds": _seconds(t["started_at"], t["ended_at"]),
                    "started_at": iso(t["started_at"]),
                    "confirmation": json.loads(t["confirmation"]) if t["confirmation"] else None, "confirmation_answer": t["confirmation_answer"],
                    "steps": [_step_view(s) for s in step_rows if s["turn_id"] == t["turn_id"]]})
    return {"conversation_id": conversation_id, "title": conversation["title"] or "", "turns": out}


def _turn(engine: Engine, turn_id: int) -> Mapping | None:
    with engine.connect() as conn:
        return conn.execute(select(turns).where(turns.c.turn_id == turn_id)).mappings().first()


def turn_of(engine: Engine, turn_id: int, handler_id: str) -> Mapping:
    """The turn, if it is in one of this handler's conversations. Raises LookupError otherwise."""
    turn = _turn(engine, turn_id)
    if turn is None:
        raise LookupError(f"unknown turn {turn_id}")
    _conversation(engine, turn["conversation_id"], handler_id)
    return turn


def step_of(engine: Engine, step_id: int, handler_id: str) -> Mapping:
    """The step, if it is on a turn in one of this handler's conversations. Raises LookupError otherwise."""
    with engine.connect() as conn:
        step = conn.execute(select(steps).where(steps.c.step_id == step_id)).mappings().first()
    if step is None:
        raise LookupError(f"unknown step {step_id}")
    _conversation(engine, step["conversation_id"], handler_id)
    return step


def _free(engine: Engine, conversation_id: str, turn_id: int) -> None:
    with engine.connect() as conn:
        conn.execute(update(conversations).where(conversations.c.conversation_id == conversation_id, conversations.c.open_turn_id == turn_id)
                     .values(open_turn_id=None))


def start_turn(engine: Engine, conversation_id: str, handler_id: str, message: str, now: datetime) -> int:
    """Start a turn on a message, if the conversation has none going. Raises TurnOpen, saying whether the Copilot is working or
    waits for a confirmation, and LookupError for an unknown conversation or another handler's."""
    _conversation(engine, conversation_id, handler_id)
    with engine.connect() as conn:
        turn_id = conn.execute(insert(turns).values(conversation_id=conversation_id, message=message, status="working", started_at=now)).inserted_primary_key[0]
    for _ in range(3):
        with engine.connect() as conn:
            taken = conn.execute(update(conversations).where(conversations.c.conversation_id == conversation_id, conversations.c.open_turn_id.is_(None))
                                 .values(open_turn_id=turn_id, updated_at=now)).rowcount
        if taken:
            with engine.connect() as conn:
                conn.execute(update(conversations).where(conversations.c.conversation_id == conversation_id, conversations.c.title.is_(None))
                             .values(title=message.strip()[:TITLE_LIMIT]))
            return turn_id
        with engine.connect() as conn:
            open_id = conn.execute(select(conversations.c.open_turn_id).where(conversations.c.conversation_id == conversation_id)).scalar_one()
        found = _turn(engine, open_id) if open_id is not None else None
        if found is not None and found["status"] in ("working", "waiting") and shown_status(found, now) != "stopped":
            reason = WAITING_TURN if found["status"] == "waiting" else WORKING_TURN
            break
        if found is not None and found["status"] == "working":  # no word for 20 minutes: it stopped
            with engine.connect() as conn:
                conn.execute(update(turns).where(turns.c.turn_id == open_id, turns.c.status == "working").values(status="stopped", ended_at=now))
        if open_id is not None:
            _free(engine, conversation_id, open_id)
    else:
        reason = WORKING_TURN
    with engine.connect() as conn:
        conn.execute(delete(turns).where(turns.c.turn_id == turn_id))
    raise TurnOpen(reason)


def answer_confirmation(engine: Engine, conversation_id: str, handler_id: str, answer: str, now: datetime) -> int:
    """Take the handler's answer ("sign_off" or "cancel") to the confirmation the conversation waits on, once. The turn works
    again. Raises NotWaiting when nothing waits, and LookupError for an unknown conversation or another handler's."""
    conversation = _conversation(engine, conversation_id, handler_id)
    turn_id = conversation["open_turn_id"]
    if turn_id is None:
        raise NotWaiting("there is no confirmation waiting")
    if answer == "sign_off":
        _card_still_holds(engine, turn_id)
    with engine.connect() as conn:
        taken = conn.execute(update(turns).where(turns.c.turn_id == turn_id, turns.c.status == "waiting")
                             .values(status="working", confirmation_answer=answer, resumed_at=now)).rowcount
    if not taken:
        raise NotWaiting("there is no confirmation waiting")
    return turn_id


def _card_still_holds(engine: Engine, turn_id: int) -> None:
    """A card never expires, so before a sign-off is taken from it the payout it showed must still be held, for the same amount. Raises
    CardChanged otherwise; the card stays, and the handler can cancel it."""
    turn = _turn(engine, turn_id)
    if turn is None or turn["status"] != "waiting" or not turn["confirmation"]:
        return  # not a waiting card: answer_confirmation says so
    card = json.loads(turn["confirmation"])
    with engine.connect() as conn:
        held = conn.execute(select(payments.c.amount).where(payments.c.payment_id == card["payment_id"], payments.c.status == "held")).scalar()
    if held is None or abs(held - card["amount"]) > 0.005:
        raise CardChanged("the payout changed since this card was shown: cancel it and ask the Copilot again")


def undo_answer(engine: Engine, turn_id: int, answer: str) -> None:
    """The Copilot did not take the handler's answer: the card waits again, so the handler can answer it once more."""
    with engine.connect() as conn:
        conn.execute(update(turns).where(turns.c.turn_id == turn_id, turns.c.status == "working", turns.c.confirmation_answer == answer)
                     .values(status="waiting", confirmation_answer=None, resumed_at=None))


def _heard(conn, turn_id, now: datetime) -> None:
    """A step begun or finished is the Copilot's word that the turn is still working: the 20 minutes count from it."""
    conn.execute(update(turns).where(turns.c.turn_id == turn_id, turns.c.status == "working").values(resumed_at=now))


def record_step(engine: Engine, turn_id: int, conversation_id: str, kind: str, tool: str, main_input: str | None, input: dict | None,
                now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    with engine.connect() as conn:
        step_id = conn.execute(insert(steps).values(
            turn_id=turn_id, conversation_id=conversation_id, kind=kind, tool=tool, main_input=main_input, status="working",
            input=json.dumps(input) if input is not None else None, started_at=now)).inserted_primary_key[0]
        _heard(conn, turn_id, now)
    return step_id


def finish_step(engine: Engine, step_id: int, status: str, output: str | None, sources: list | None, reason: str | None,
                now: datetime | None = None) -> None:
    now = now or datetime.now(timezone.utc)
    with engine.connect() as conn:
        conn.execute(update(steps).where(steps.c.step_id == step_id).values(
            status=status, output=output, sources=json.dumps(sources) if sources else None, reason=reason, ended_at=now))
        _heard(conn, conn.execute(select(steps.c.turn_id).where(steps.c.step_id == step_id)).scalar(), now)


def pause_turn(engine: Engine, ref: Reference, turn_id: int, claim_id: str) -> dict:
    """Make the turn wait for the handler's confirmation to sign off the claim's held payout, and return the card to show. The
    card is read from the records here, never from what the model wrote. Raises NoCard when no payout is held on the claim and
    TurnEnded when the turn is not working."""
    query = (select(payments.c.payment_id, payments.c.amount, claims.c.payload, decisions.c.rationale)
             .select_from(payments.join(claims, payments.c.claim_id == claims.c.claim_id).outerjoin(decisions, payments.c.claim_id == decisions.c.claim_id))
             .where(payments.c.claim_id == claim_id, payments.c.status == "held"))
    with engine.connect() as conn:
        row = conn.execute(query).mappings().first()
    if row is None:
        raise NoCard(f"no payout is held for a sign-off on {claim_id}")
    card = {"claim_id": claim_id, "payment_id": row["payment_id"], "customer_name": ref.customer(json.loads(row["payload"])["customer_id"])["name"],
            "amount": row["amount"], "reason": row["rationale"]}
    with engine.connect() as conn:
        taken = conn.execute(update(turns).where(turns.c.turn_id == turn_id, turns.c.status == "working")
                             .values(status="waiting", confirmation=json.dumps(card))).rowcount
    if not taken:
        raise TurnEnded(f"turn {turn_id} is not working")
    return card


def _end(engine: Engine, turn_id: int, **values) -> None:
    with engine.connect() as conn:
        taken = conn.execute(update(turns).where(turns.c.turn_id == turn_id, turns.c.status == "working")
                             .values(ended_at=datetime.now(timezone.utc), **values)).rowcount
        conversation_id = conn.execute(select(turns.c.conversation_id).where(turns.c.turn_id == turn_id)).scalar_one()
    if not taken:
        raise TurnEnded(f"turn {turn_id} is not working")
    _free(engine, conversation_id, turn_id)


def end_turn(engine: Engine, turn_id: int, answer: str, model_calls: int, cost_usd: float | None) -> None:
    """The turn is done, with the Copilot's answer, its model calls and what they cost. Raises TurnEnded unless it is working."""
    _end(engine, turn_id, status="done", answer=answer, model_calls=model_calls, cost_usd=cost_usd)


def fail_turn(engine: Engine, turn_id: int, reason: str) -> None:
    """The turn failed, with why. Raises TurnEnded unless it is working."""
    _end(engine, turn_id, status="failed", error=reason)
