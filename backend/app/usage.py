"""What each claim cost and how long the agent spent on it, kept with the run it happened in, so that each run's
totals are its own, failed attempts included."""
from datetime import datetime

from sqlalchemy import Engine, func, insert, select

from app.db import model_calls, work_spans

TOKENS = ("input_tokens", "output_tokens", "reasoning_tokens")


def record_model_call(engine: Engine, claim_id: str, run_id: int | None, model: str, usage: dict) -> None:
    """One model reply's tokens and cost."""
    with engine.begin() as conn:
        conn.execute(insert(model_calls).values(
            claim_id=claim_id, run_id=run_id, model=model, cost_usd=usage.get("cost_usd"),
            **{key: usage.get(key, 0) for key in TOKENS},
        ))


def record_work_time(engine: Engine, claim_id: str, run_id: int | None, started_at: datetime, seconds: float) -> None:
    """One stretch of the agent working a claim: from a call to the graph to its return, so a wait is left out."""
    with engine.begin() as conn:
        conn.execute(insert(work_spans).values(claim_id=claim_id, run_id=run_id, started_at=started_at, seconds=seconds))


def _totals(where_calls, where_spans, group: bool = False):
    calls = select(
        *([model_calls.c.claim_id] if group else []),
        func.count().label("calls"),
        *[func.coalesce(func.sum(model_calls.c[key]), 0).label(key) for key in TOKENS],
        func.sum(model_calls.c.cost_usd).label("cost_usd"),
    ).where(where_calls)
    spans = select(
        *([work_spans.c.claim_id] if group else []),
        func.coalesce(func.sum(work_spans.c.seconds), 0.0).label("seconds"),
    ).where(where_spans)
    if group:
        calls, spans = calls.group_by(model_calls.c.claim_id), spans.group_by(work_spans.c.claim_id)
    return calls, spans


def _row(calls, seconds) -> dict:
    out = {"calls": 0, **{key: 0 for key in TOKENS}, "cost_usd": None, "seconds": 0.0}
    if calls is not None:
        out.update({key: calls[key] for key in ("calls", *TOKENS)})
        out["cost_usd"] = round(calls["cost_usd"], 8) if calls["cost_usd"] is not None else None
    out["seconds"] = round(float(seconds or 0.0), 3)
    return out


def claim_totals(engine: Engine, claim_id: str) -> dict:
    calls, spans = _totals(model_calls.c.claim_id == claim_id, work_spans.c.claim_id == claim_id)
    with engine.connect() as conn:
        return _row(conn.execute(calls).mappings().one(), conn.execute(spans).scalar_one())


def run_totals(engine: Engine, run_id: int) -> dict:
    calls, spans = _totals(model_calls.c.run_id == run_id, work_spans.c.run_id == run_id)
    with engine.connect() as conn:
        return _row(conn.execute(calls).mappings().one(), conn.execute(spans).scalar_one())


def per_claim(engine: Engine, run_id: int) -> dict[str, dict]:
    """Each claim's totals within one run."""
    calls, spans = _totals(model_calls.c.run_id == run_id, work_spans.c.run_id == run_id, group=True)
    with engine.connect() as conn:
        by_calls = {r["claim_id"]: r for r in conn.execute(calls).mappings()}
        by_spans = {r["claim_id"]: r["seconds"] for r in conn.execute(spans).mappings()}
    return {claim_id: _row(by_calls.get(claim_id), by_spans.get(claim_id)) for claim_id in by_calls.keys() | by_spans.keys()}
