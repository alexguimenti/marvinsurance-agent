"""The app keeps one piece of the agent's work going at a time: a dispatch, an evaluation, or a round of the improver while it
works (open, with no proposal waiting for a handler's review). Resuming a claim is not a piece of work in this sense.

The app does the first part of the work itself: it creates the run or the round, or records a handler's review, and then asks the
agent to work it by its id. Every statement here commits by itself (see `db.make_engine`), so what holds two starts at the same
moment apart is the one conditional update of the lock row: whoever takes it checks and writes, and the other waits for it, then
finds the work going. A request uses one database connection at a time.

The agent tells the app every minute that it is still working. Work the app has not heard about for five minutes counts as
stopped (a Runtime session ended, or the agent on the laptop restarted), counting from the later of when the app started it and
the agent's last word; the next start stops it, with a reason, and goes ahead. The app never marks a round's evaluation stopped
on its own: the round's word covers it."""
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Callable

from sqlalchemy import Engine, select, update

from app import improvement, runs
from app.db import insert_or_ignore, rounds, work_lock
from app.db import runs as runs_table

QUIET = timedelta(minutes=5)  # the agent's word every minute: this long without it counts as stopped
LOCK_HELD_FOR = timedelta(seconds=30)  # a lock a start that died left is taken over after this
WAIT_FOR_LOCK_S = 10
STOPPED_FOR_QUIET = "The agent stopped answering"
WORKING_STAGES = ("first_evaluations", "improving", "evaluating", "verdict")  # the others wait for a handler, or have ended


class WorkGoing(Exception):
    """Something is already going. The message names what: "a dispatch", "an evaluation" or "round 3"."""

    def __init__(self, what: str):
        super().__init__(f"{what} is already running")
        self.what = what


def _utc(moment: datetime | None) -> datetime | None:
    """SQLite's timestamps come back naive, and are UTC."""
    return None if moment is None else (moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _take_lock(engine: Engine, token: str) -> None:
    with engine.begin() as conn:
        conn.execute(insert_or_ignore(conn, work_lock).values(id=1))
    deadline = time.monotonic() + WAIT_FOR_LOCK_S
    while True:
        now = _now()
        with engine.begin() as conn:
            taken = conn.execute(
                update(work_lock).where(work_lock.c.id == 1, (work_lock.c.holder.is_(None)) | (work_lock.c.taken_at < now - LOCK_HELD_FOR))
                .values(holder=token, taken_at=now)
            ).rowcount
        if taken:
            return
        if time.monotonic() > deadline:
            raise WorkGoing("another start")
        time.sleep(0.02)


def _release_lock(engine: Engine, token: str) -> None:
    with engine.begin() as conn:
        conn.execute(update(work_lock).where(work_lock.c.id == 1, work_lock.c.holder == token).values(holder=None))


def stop_the_quiet(engine: Engine) -> None:
    """Stop the work the agent has not been heard from about for five minutes, with the reason. A round takes its running
    evaluation with it; an evaluation a round asked for is never judged on its own."""
    cutoff = _now() - QUIET
    with engine.connect() as conn:
        running = conn.execute(select(runs_table).where(runs_table.c.status == "running")).mappings().all()
        working = conn.execute(select(rounds).where(rounds.c.open.is_(True), rounds.c.stage.in_(WORKING_STAGES))).mappings().all()
    for run in running:
        if run["round_id"] is None and max(t for t in (_utc(run["started_at"]), _utc(run["heard_at"])) if t) < cutoff:
            runs.stop_run(engine, run["run_id"], STOPPED_FOR_QUIET)
    for found in working:
        if max(t for t in (_utc(found["started_at"]), _utc(found["heard_at"])) if t) < cutoff:
            improvement.stop_round_with_reason(engine, found["round_id"], STOPPED_FOR_QUIET)


def what_is_going(engine: Engine) -> str | None:
    """"a dispatch", "an evaluation" or "round N" while one is going, else None."""
    with engine.connect() as conn:
        run = conn.execute(select(runs_table.c.kind).where(runs_table.c.status == "running")).first()
        working = conn.execute(select(rounds.c.round_id).where(rounds.c.open.is_(True), rounds.c.stage.in_(WORKING_STAGES))).first()
    if run is not None:
        return "an evaluation" if run[0] == "evaluation" else "a dispatch"
    return None if working is None else f"round {working[0]}"


def start_work(engine: Engine, create: Callable[[], int]) -> int:
    """Create a piece of work (`create` makes the run or the round, or records the review, and returns its id) if nothing is going.
    Raises WorkGoing, naming what is. Quiet work is stopped first."""
    token = secrets.token_hex(8)
    _take_lock(engine, token)
    try:
        stop_the_quiet(engine)
        going = what_is_going(engine)
        if going:
            raise WorkGoing(going)
        try:
            return create()
        except runs.RunActive as exc:  # a run the agent made for a round, between the check and the write
            raise WorkGoing("a dispatch or evaluation") from exc
    finally:
        _release_lock(engine, token)
