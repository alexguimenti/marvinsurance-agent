"""Runs of the agent over the queue: the run record, its to-do list, its events and its checks."""
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import Engine, delete, func, insert, select, union_all, update
from sqlalchemy.exc import IntegrityError

from app.db import claims, decisions, plain, run_events as run_events_table, runs, todo_items, trace_events

TODO_STATUSES = ("todo", "in_progress", "done", "left_for_handler")
RUN_STALE_S = 45  # a running run this long without a heartbeat has lost its process


class RunActive(Exception):
    """Another run is still going."""


def start_run(engine: Engine, model: str, cap: int | None = None, scope: list[str] | None = None) -> int:
    try:
        with engine.begin() as conn:
            return conn.execute(
                insert(runs)
                .values(status="running", cap=cap, scope=json.dumps(scope) if scope is not None else None, model=model,
                        heartbeat_at=datetime.now(timezone.utc))
                .returning(runs.c.run_id)
            ).scalar_one()
    except IntegrityError as exc:  # the one_running_run index
        raise RunActive("a run is already in progress") from exc


def get_run(engine: Engine, run_id: int):
    with engine.connect() as conn:
        return conn.execute(select(runs).where(runs.c.run_id == run_id)).mappings().first()


def latest_run(engine: Engine):
    with engine.connect() as conn:
        return conn.execute(select(runs).order_by(runs.c.run_id.desc()).limit(1)).mappings().first()


def _running(engine: Engine):
    with engine.connect() as conn:
        return conn.execute(select(runs).where(runs.c.status == "running")).mappings().first()


def _is_alive(row, now: datetime) -> bool:
    beat = row["heartbeat_at"]
    if beat is None:
        return False
    beat = beat if beat.tzinfo else beat.replace(tzinfo=timezone.utc)
    return now - beat < timedelta(seconds=RUN_STALE_S)


def live_run(engine: Engine, now: datetime):
    """The running run, if its process is still beating."""
    row = _running(engine)
    return row if row is not None and _is_alive(row, now) else None


def stale_run(engine: Engine, now: datetime):
    """The running run, if its process has gone: it is the next run's to finish."""
    row = _running(engine)
    return row if row is not None and not _is_alive(row, now) else None


def touch(engine: Engine, run_id: int) -> None:
    with engine.begin() as conn:
        conn.execute(update(runs).where(runs.c.run_id == run_id).values(heartbeat_at=datetime.now(timezone.utc)))


def release(engine: Engine, run_id: int) -> None:
    """The process is stopping: the run can be adopted at once, with no wait for its heartbeat to go stale."""
    with engine.begin() as conn:
        conn.execute(update(runs).where(runs.c.run_id == run_id, runs.c.status == "running").values(heartbeat_at=None))


def add_trace_event(engine: Engine, claim_id: str, kind: str, content: dict) -> None:
    """Append one step to a claim's trace, under the run working it."""
    t = trace_events
    next_seq = select(func.coalesce(func.max(t.c.seq), 0) + 1).where(t.c.claim_id == claim_id).scalar_subquery()
    run_id = select(claims.c.run_id).where(claims.c.claim_id == claim_id).scalar_subquery()
    with engine.begin() as conn:
        conn.execute(insert(t).values(claim_id=claim_id, seq=next_seq, kind=kind, content=json.dumps(content), run_id=run_id))


def run_event(engine: Engine, run_id: int, kind: str, content: dict) -> None:
    table = run_events_table
    next_seq = select(func.coalesce(func.max(table.c.seq), 0) + 1).where(table.c.run_id == run_id).scalar_subquery()
    with engine.begin() as conn:
        conn.execute(insert(table).values(run_id=run_id, seq=next_seq, kind=kind, content=json.dumps(content)))


def run_events(engine: Engine, run_id: int) -> list[dict]:
    table = run_events_table
    with engine.connect() as conn:
        rows = conn.execute(
            select(table.c.seq, table.c.kind, table.c.content, table.c.created_at)
            .where(table.c.run_id == run_id).order_by(table.c.seq)
        ).mappings().all()
    return [{**plain(r), "content": json.loads(r["content"])} for r in rows]


# --- the to-do list ---------------------------------------------------------

def todo_list(engine: Engine, run_id: int) -> list[dict]:
    t = todo_items
    with engine.connect() as conn:
        rows = conn.execute(
            select(t.c.item_id, t.c.position, t.c.title, t.c.status, t.c.claim_id, t.c.note)
            .where(t.c.run_id == run_id).order_by(t.c.position)
        ).mappings().all()
    return [dict(r) for r in rows]


def set_todo(engine: Engine, run_id: int, items: list[dict]) -> list[dict]:
    """Replace the run's plan. Each item is {"title": ..., "claim_id": ... or None}. Items already done stay
    done and stay first, so finishing a stopped run does not lose what it did."""
    t = todo_items
    with engine.begin() as conn:
        conn.execute(delete(t).where(t.c.run_id == run_id, t.c.status != "done"))
        done = conn.execute(select(t.c.claim_id, t.c.position).where(t.c.run_id == run_id)).all()
        done_claims = {row[0] for row in done if row[0]}
        position = max((row[1] for row in done), default=0)
        for item in items:
            if item.get("claim_id") in done_claims:
                continue
            position += 1
            conn.execute(insert(t).values(
                run_id=run_id, position=position, title=item["title"], status="todo", claim_id=item.get("claim_id"),
            ))
    run_event(engine, run_id, "todo", {"action": "plan", "items": len(items)})
    return todo_list(engine, run_id)


def update_todo(engine: Engine, run_id: int, item_id: int, status: str, note: str | None = None) -> dict:
    if status not in TODO_STATUSES:
        raise ValueError(f"unknown status {status!r}; use one of {', '.join(TODO_STATUSES)}")
    t = todo_items
    with engine.begin() as conn:
        item = conn.execute(select(t).where(t.c.run_id == run_id, t.c.item_id == item_id)).mappings().first()
        if item is None:
            raise ValueError(f"there is no item {item_id} in this run's list")
        if status == "done" and item["claim_id"]:
            decision = conn.execute(select(decisions.c.claim_id).where(decisions.c.claim_id == item["claim_id"])).first()
            if decision is None:
                raise ValueError(f"{item['claim_id']} is not decided yet, so its item cannot be done")
        conn.execute(update(t).where(t.c.item_id == item_id)
                     .values(status=status, note=note if note is not None else item["note"]))
        updated = dict(conn.execute(select(t).where(t.c.item_id == item_id)).mappings().one())
    run_event(engine, run_id, "todo", {"action": "update", "item_id": item_id, "status": status})
    return updated


def todo_for_claim(engine: Engine, run_id: int, claim_id: str, status: str, note: str | None = None) -> None:
    """Keep a claim's item in step with the claim, adding one if the plan left it out."""
    t = todo_items
    with engine.begin() as conn:
        item = conn.execute(
            select(t.c.item_id).where(t.c.run_id == run_id, t.c.claim_id == claim_id).order_by(t.c.position).limit(1)
        ).first()
        if item is None:
            position = conn.execute(select(func.coalesce(func.max(t.c.position), 0) + 1).where(t.c.run_id == run_id)).scalar_one()
            conn.execute(insert(t).values(run_id=run_id, position=position, title=f"Work {claim_id}", status=status,
                                          claim_id=claim_id, note=note))
        else:
            conn.execute(update(t).where(t.c.item_id == item[0]).values(status=status, note=note))
    run_event(engine, run_id, "todo", {"action": "claim", "claim_id": claim_id, "status": status})


# --- scope, cap and the missed-claim check -------------------------------------

def _scope(engine: Engine, run_id: int) -> list[str] | None:
    scope = get_run(engine, run_id)["scope"]
    return json.loads(scope) if scope is not None else None


def in_scope_waiting(engine: Engine, run_id: int) -> list[str]:
    with engine.connect() as conn:
        waiting = list(conn.execute(
            select(claims.c.claim_id).where(claims.c.status == "waiting").order_by(claims.c.claim_id)
        ).scalars())
    scope = _scope(engine, run_id)
    return waiting if scope is None else [c for c in waiting if c in set(scope)]


def in_scope(engine: Engine, run_id: int, claim_id: str) -> bool:
    scope = _scope(engine, run_id)
    return scope is None or claim_id in scope


def taken_claims(engine: Engine, run_id: int) -> list:
    with engine.connect() as conn:
        return conn.execute(
            select(claims.c.claim_id, claims.c.status).where(claims.c.run_id == run_id).order_by(claims.c.claim_id)
        ).mappings().all()


def cap_reached(engine: Engine, run_id: int) -> bool:
    cap = get_run(engine, run_id)["cap"]
    return cap is not None and len(taken_claims(engine, run_id)) >= cap


def missed_claims(engine: Engine, run_id: int) -> list[str]:
    """Claims the run still owes: its own claims back in the queue or in progress with no decision,
    then untried waiting claims in scope, as many as the cap still allows. A claim with a decision is
    owed nothing by the run: a signed-off one is being paid, and an unpaid one is carried on by the next run."""
    taken = taken_claims(engine, run_id)
    with engine.connect() as conn:
        decided = set(conn.execute(select(decisions.c.claim_id)).scalars())
    owed = [r["claim_id"] for r in taken
            if r["status"] == "waiting" or (r["status"] == "in_progress" and r["claim_id"] not in decided)]
    cap = get_run(engine, run_id)["cap"]
    room = None if cap is None else max(cap - len(taken), 0)
    taken_ids = {r["claim_id"] for r in taken}
    untried = [c for c in in_scope_waiting(engine, run_id) if c not in taken_ids]
    return owed + (untried if room is None else untried[:room])


# --- ending a run ------------------------------------------------------------

def sum_usage(usages: list[dict]) -> dict | None:
    if not usages:
        return None
    costs = [u["cost_usd"] for u in usages if u.get("cost_usd") is not None]
    total = {key: sum(u.get(key, 0) for u in usages)
             for key in ("calls", "input_tokens", "output_tokens", "reasoning_tokens")}
    total["cost_usd"] = round(sum(costs), 8) if costs else None
    return total


def run_usage(engine: Engine, run_id: int) -> dict | None:
    """The orchestrator's usage plus every claim attempt made in the run."""
    query = union_all(
        select(run_events_table.c.content).where(run_events_table.c.run_id == run_id, run_events_table.c.kind == "usage"),
        select(trace_events.c.content).where(trace_events.c.run_id == run_id, trace_events.c.kind == "usage"),
    )
    with engine.connect() as conn:
        return sum_usage([json.loads(r[0]) for r in conn.execute(query)])


def set_summary(engine: Engine, run_id: int, summary: str) -> None:
    with engine.begin() as conn:
        conn.execute(update(runs).where(runs.c.run_id == run_id).values(summary=summary))


def note_on_summary(engine: Engine, run_id: int, note: str) -> None:
    with engine.begin() as conn:
        summary = conn.execute(select(runs.c.summary).where(runs.c.run_id == run_id)).scalar_one()
        conn.execute(update(runs).where(runs.c.run_id == run_id).values(summary=f"{note}\n{summary}" if summary else note))


def finish(engine: Engine, run_id: int, status: str, summary: str | None = None, error: str | None = None) -> None:
    usage = run_usage(engine, run_id)
    with engine.begin() as conn:
        values = {"status": status, "error": error, "usage": json.dumps(usage) if usage else None, "finished_at": func.now()}
        if summary is not None:
            values["summary"] = summary
        conn.execute(update(runs).where(runs.c.run_id == run_id).values(**values))
