"""Dispatches and evaluations: the run record, which claims it works, where each stands, and its summary.
Code keeps track of a run, with no model. One run of either kind at a time holds across processes."""
import json
from datetime import datetime, timezone

from sqlalchemy import Engine, func, insert, select, update
from sqlalchemy.exc import IntegrityError

from app.db import claims, decisions, payments, plain, run_claims, runs, trace_events
from app.usage import per_claim

RUN_KINDS = ("dispatch", "evaluation")


class RunActive(Exception):
    """Another dispatch or evaluation is still going."""


def start(engine: Engine, kind: str, workers: int, model: str, effort: str | None, **extra) -> int:
    """Create a running run. Raises RunActive while another one runs."""
    try:
        with engine.begin() as conn:
            return conn.execute(
                insert(runs).values(kind=kind, status="running", workers=workers, model=model,
                                    reasoning_effort=effort or "none", **extra)
                .returning(runs.c.run_id)
            ).scalar_one()
    except IntegrityError as exc:  # the one_running_run index
        raise RunActive("a dispatch or evaluation is already running") from exc


def start_dispatch(engine: Engine, workers: int, model: str, effort: str | None) -> int:
    return start(engine, "dispatch", workers, model, effort)


def get_run(engine: Engine, run_id: int):
    with engine.connect() as conn:
        return conn.execute(select(runs).where(runs.c.run_id == run_id)).mappings().first()


def latest_run(engine: Engine, kind: str):
    with engine.connect() as conn:
        return conn.execute(
            select(runs).where(runs.c.kind == kind).order_by(runs.c.run_id.desc()).limit(1)
        ).mappings().first()


def running_run(engine: Engine):
    with engine.connect() as conn:
        return conn.execute(select(runs).where(runs.c.status == "running")).mappings().first()


def finish_run(engine: Engine, run_id: int, error: str | None = None) -> None:
    """A run ends finished; one whose dispatcher broke ends stopped, with the reason."""
    with engine.begin() as conn:
        conn.execute(update(runs).where(runs.c.run_id == run_id, runs.c.status == "running").values(
            status="stopped" if error else "finished", error=error, finished_at=func.now(),
        ))


def stop_running(engine: Engine) -> list[int]:
    """The service has just started, so whatever still says running lost its process. Its claims carry on in the
    next dispatch."""
    with engine.begin() as conn:
        return list(conn.execute(
            update(runs).where(runs.c.status == "running")
            .values(status="stopped", finished_at=func.now()).returning(runs.c.run_id)
        ).scalars())


# --- which claims a run works ------------------------------------------------------------------

def _queue_claims():
    """The queue's own claims."""
    return select(claims.c.claim_id)


def claims_to_dispatch(engine: Engine) -> list[str]:
    """Every claim left in progress, then every waiting claim, each oldest submitted first."""
    order = (claims.c.submitted_on, claims.c.claim_id)
    with engine.connect() as conn:
        in_progress = conn.execute(
            _queue_claims().where(claims.c.status == "in_progress", claims.c.thread_id.is_not(None)).order_by(*order)
        ).scalars().all()
        waiting = conn.execute(_queue_claims().where(claims.c.status == "waiting").order_by(*order)).scalars().all()
    return [*in_progress, *waiting]


def add_run_claims(engine: Engine, run_id: int, claim_ids: list[str]) -> None:
    if not claim_ids:
        return
    with engine.begin() as conn:
        conn.execute(insert(run_claims), [
            {"run_id": run_id, "claim_id": claim_id, "position": n} for n, claim_id in enumerate(claim_ids, 1)
        ])


def claim_started(engine: Engine, run_id: int, claim_id: str) -> None:
    """The first time a claim agent took the claim in this run; a retry keeps it."""
    with engine.begin() as conn:
        conn.execute(update(run_claims).where(
            run_claims.c.run_id == run_id, run_claims.c.claim_id == claim_id, run_claims.c.started_at.is_(None),
        ).values(started_at=datetime.now(timezone.utc)))


def claim_finished(engine: Engine, run_id: int, claim_id: str) -> None:
    with engine.begin() as conn:
        conn.execute(update(run_claims).where(run_claims.c.run_id == run_id, run_claims.c.claim_id == claim_id)
                     .values(finished_at=datetime.now(timezone.utc)))


def run_claim_ids(engine: Engine, run_id: int) -> list[str]:
    with engine.connect() as conn:
        return list(conn.execute(
            select(run_claims.c.claim_id).where(run_claims.c.run_id == run_id).order_by(run_claims.c.position)
        ).scalars())


def run_claims_view(engine: Engine, run_id: int) -> list[dict]:
    """Each claim of the run and where it stands, with what it cost and how long it took in this run."""
    query = (
        select(run_claims.c.claim_id, run_claims.c.position, run_claims.c.started_at, run_claims.c.finished_at,
               claims.c.status, claims.c.attempts, decisions.c.decision, decisions.c.payout,
               payments.c.status.label("payment"))
        .select_from(run_claims.join(claims, run_claims.c.claim_id == claims.c.claim_id)
                     .outerjoin(decisions, run_claims.c.claim_id == decisions.c.claim_id)
                     .outerjoin(payments, run_claims.c.claim_id == payments.c.claim_id))
        .where(run_claims.c.run_id == run_id)
        .order_by(run_claims.c.position)
    )
    with engine.connect() as conn:
        rows = conn.execute(query).mappings().all()
    totals = per_claim(engine, run_id)
    empty = {"cost_usd": None, "seconds": 0.0}
    return [{**plain(r), "cost_usd": totals.get(r["claim_id"], empty)["cost_usd"],
             "seconds": totals.get(r["claim_id"], empty)["seconds"]} for r in rows]


def run_summary(engine: Engine, run_id: int) -> dict:
    """What the run came to, from the records alone: no model writes it."""
    rows = run_claims_view(engine, run_id)
    by = lambda word: sum(1 for r in rows if r["decision"] == word)  # noqa: E731
    return {
        "approved": by("approve"),
        "declined": by("decline"),
        "referred": by("refer"),
        "awaiting_sign_off": sorted(({"claim_id": r["claim_id"], "payout": r["payout"]}
                                     for r in rows if r["payment"] == "held"), key=lambda p: p["claim_id"]),
        "left_for_handler": sorted(r["claim_id"] for r in rows if r["status"] == "needs_handler"),
        "in_progress": sorted(r["claim_id"] for r in rows if r["status"] in ("in_progress", "waiting")),
    }


def run_view(row) -> dict | None:
    if row is None:
        return None
    return plain(row)


# --- a claim's own trace ------------------------------------------------------------------------

def add_trace_event(engine: Engine, claim_id: str, kind: str, content: dict) -> None:
    """Append one step to a claim's trace, under the run working it."""
    t = trace_events
    next_seq = select(func.coalesce(func.max(t.c.seq), 0) + 1).where(t.c.claim_id == claim_id).scalar_subquery()
    run_id = select(claims.c.run_id).where(claims.c.claim_id == claim_id).scalar_subquery()
    with engine.begin() as conn:
        conn.execute(insert(t).values(claim_id=claim_id, seq=next_seq, kind=kind, content=json.dumps(content), run_id=run_id))
