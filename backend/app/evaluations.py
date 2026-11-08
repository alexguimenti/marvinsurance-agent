"""Evaluations: a run over copies of one of our labelled sets, worked by the same dispatcher and claim agent as a
dispatch. A copy has a new id, never shows in the queue, is never carried on by a dispatch, and cannot be signed off.

Building one reads which claims are in the set, never the handlers' decisions: only app/scoring.py reads those."""
import json

from sqlalchemy import Engine, insert, select

from app import runs
from app.db import claims, eval_sets, held_out_claims
from app.reference import Reference

EVAL_SETS = ("evaluation", "held_out")


def copy_id(run_id: int, claim_id: str) -> str:
    return f"E{run_id}-{claim_id}"


def start_evaluation(engine: Engine, ref: Reference, eval_set: str, tiny: bool, workers: int, model: str,
                     effort: str | None, config_version: int | None = None, round_id: int | None = None) -> int:
    """Create the evaluation and its copies, waiting, oldest submitted first. Raises RunActive while a run is going."""
    if eval_set not in EVAL_SETS:
        raise ValueError(f"unknown set {eval_set!r}; use one of {', '.join(EVAL_SETS)}")
    with engine.connect() as conn:
        query = select(eval_sets.c.claim_id).where(eval_sets.c.eval_set == eval_set)
        members = set(conn.execute(query.where(eval_sets.c.tiny) if tiny else query).scalars())
        if eval_set == "evaluation":  # the 25 claims of the queue, as they were received
            payloads = [c for c in ref.claims if c["claim_id"] in members]
        else:
            payloads = [json.loads(p) for p in conn.execute(
                select(held_out_claims.c.payload).where(held_out_claims.c.claim_id.in_(members))).scalars()]
    run_id = runs.start(engine, "evaluation", workers, model, effort, eval_set=eval_set, tiny=tiny,
                          config_version=config_version, round_id=round_id)
    payloads.sort(key=lambda c: (c["submitted_on"], c["claim_id"]))
    copies = [{**c, "claim_id": copy_id(run_id, c["claim_id"])} for c in payloads]
    try:
        _add_copies(engine, run_id, copies, payloads)
    except Exception as exc:  # a half-made evaluation must not hold the one running place
        runs.finish_run(engine, run_id, error=f"could not create its copies: {type(exc).__name__}: {exc}")
        raise
    return run_id


def _add_copies(engine: Engine, run_id: int, copies: list[dict], payloads: list[dict]) -> None:
    with engine.begin() as conn:
        conn.execute(insert(claims), [
            {"claim_id": c["claim_id"], "status": "waiting", "payload": json.dumps(c), "submitted_on": c["submitted_on"],
             "copy_of": original["claim_id"], "evaluation_id": run_id}
            for c, original in zip(copies, payloads)
        ])
    runs.add_run_claims(engine, run_id, [c["claim_id"] for c in copies])


def is_copy(engine: Engine, claim_id: str) -> bool:
    with engine.connect() as conn:
        return conn.execute(select(claims.c.copy_of).where(claims.c.claim_id == claim_id)).scalar_one_or_none() is not None
