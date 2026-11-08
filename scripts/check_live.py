"""What a live run left behind, with no secrets and no claim text.

    python scripts/check_live.py                  the claims, runs and connections on the database in .env
    python scripts/check_live.py --run 3          also that run's claims: start and finish, the most at once,
                                                  the sum of their agent time against the run's time
    python scripts/check_live.py --claim CLM-1003 also that claim's thread, payment and Langfuse traces
    python scripts/check_live.py --tag run-3      the Langfuse traces with that tag
    python scripts/check_live.py --round 1        also that round: its stage, proposals, verdict, scores, runs and cost
    python scripts/check_live.py --session round-1  the Langfuse traces of that session (a round's improver)
"""
import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from dotenv import load_dotenv  # noqa: E402
from sqlalchemy import text  # noqa: E402

load_dotenv(ROOT / ".env")

from agent.tracing import session_name  # noqa: E402
from app.config import Settings  # noqa: E402
from app.db import make_engine  # noqa: E402

CONNECTIONS = "SELECT count(*) FROM pg_stat_activity WHERE usename = 'postgres'"


def langfuse():
    from langfuse import Langfuse

    return Langfuse(
        public_key=os.environ["LANGFUSE_PUBLIC_KEY"],
        secret_key=os.environ["LANGFUSE_SECRET_KEY"],
        base_url=os.environ.get("LANGFUSE_BASE_URL"),
    )


def traces_in_session(session: str) -> list:
    """The session's traces through Langfuse's API: their root observations, oldest first."""
    found = langfuse().api.observations.get_many(session_id=session, is_root_observation=True, fields="core", limit=50).data
    return sorted(found, key=lambda t: t.start_time)


def traces_with_tag(tag: str) -> list:
    """Root observations of the traces carrying the tag, one per claim run, through the real-time read path."""
    import json

    where = json.dumps([
        {"type": "arrayOptions", "column": "traceTags", "operator": "any of", "value": [tag]},
        {"type": "boolean", "column": "isRootObservation", "operator": "=", "value": True},
    ])
    found, cursor = [], None
    while True:
        page = langfuse().api.observations.get_many(filter=where, fields="core", limit=100, cursor=cursor)
        found += page.data
        cursor = getattr(page.meta, "cursor", None)
        if not cursor or not page.data:
            return found


def show_run(conn, run_id: int) -> None:
    rows = conn.execute(text(
        "SELECT rc.claim_id, rc.started_at, rc.finished_at, c.status, c.attempts, c.thread_id, "
        "(SELECT COALESCE(SUM(seconds), 0) FROM work_spans w WHERE w.claim_id = rc.claim_id AND w.run_id = rc.run_id) AS seconds "
        "FROM run_claims rc JOIN claims c ON c.claim_id = rc.claim_id WHERE rc.run_id = :r ORDER BY rc.started_at"
    ), {"r": run_id}).mappings().all()
    run = conn.execute(text("SELECT kind, status, workers, model, reasoning_effort, started_at, finished_at FROM runs "
                            "WHERE run_id = :r"), {"r": run_id}).mappings().one()
    print(f"run {run_id}: {dict(run)}")
    events = sorted([(r["started_at"], 1) for r in rows if r["started_at"]] +
                    [(r["finished_at"], -1) for r in rows if r["finished_at"]], key=lambda e: (e[0], e[1]))
    running = peak = 0
    for _, change in events:
        running += change
        peak = max(peak, running)
    first = min((r["started_at"] for r in rows if r["started_at"]), default=None)
    for r in rows:
        offset = (r["started_at"] - first).total_seconds() if r["started_at"] and first else None
        print(f"  {r['claim_id']}: {r['status']} attempts={r['attempts']} thread={r['thread_id']} "
              f"start=+{offset if offset is None else round(offset, 1)}s agent={round(r['seconds'], 1)}s")
    total = sum(r["seconds"] for r in rows)
    span = (run["finished_at"] - run["started_at"]).total_seconds() if run["finished_at"] else None
    print(f"  most at once: {peak}; sum of claims' agent time: {total:.1f}s; run took: {span}s")
    cost = conn.execute(text("SELECT COUNT(*), SUM(cost_usd) FROM model_calls WHERE run_id = :r"), {"r": run_id}).one()
    print(f"  model calls: {cost[0]}, cost: ${cost[1] or 0:.4f}")


def show_round(conn, round_id: int) -> None:
    r = conn.execute(text("SELECT stage, progress, open, recipe_version, version_tried, verdict, why, before, after, "
                          "started_by, stopped_by, error, started_at, finished_at FROM rounds WHERE round_id = :r"),
                     {"r": round_id}).mappings().one()
    print(f"round {round_id}: stage={r['stage']} progress={r['progress']} open={r['open']} recipe=v{r['recipe_version']} "
          f"tried=v{r['version_tried']} verdict={r['verdict']} ({r['why']}) by={r['started_by']} error={r['error']}")
    print(f"  before: {r['before']}")
    print(f"  after:  {r['after']}")
    for p in conn.execute(text("SELECT id, kind, status, decided_by, decision_reason FROM proposals WHERE round_id = :r ORDER BY id"),
                          {"r": round_id}).mappings():
        print(f"  proposal {p['id']}: {p['kind']} {p['status']} by={p['decided_by']} reason={p['decision_reason']!r}")
    for run in conn.execute(text("SELECT run_id, kind, status, eval_set, config_version FROM runs WHERE round_id = :r ORDER BY run_id"),
                            {"r": round_id}).mappings():
        print(f"  run {run['run_id']}: {run['kind']} {run['eval_set']} version={run['config_version']} {run['status']}")
    cost = conn.execute(text("SELECT COUNT(*), SUM(cost_usd) FROM model_calls WHERE round_id = :r"), {"r": round_id}).one()
    print(f"  the improver's model calls: {cost[0]}, cost: ${cost[1] or 0:.4f}")
    claims_cost = conn.execute(text("SELECT SUM(m.cost_usd) FROM model_calls m JOIN runs u ON u.run_id = m.run_id "
                                    "WHERE u.round_id = :r"), {"r": round_id}).scalar_one()
    judged = conn.execute(text("SELECT SUM(g.cost_usd) FROM judge_grades g JOIN runs u ON u.run_id = g.run_id "
                               "WHERE u.round_id = :r"), {"r": round_id}).scalar_one()
    print(f"  the claim agents' cost in its evaluations: ${claims_cost or 0:.4f}; the judge's: ${judged or 0:.4f}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=int)
    parser.add_argument("--claim")
    parser.add_argument("--tag")
    parser.add_argument("--round", type=int)
    parser.add_argument("--session")
    args = parser.parse_args()
    if args.tag:
        traces = traces_with_tag(args.tag)
        print(f"Langfuse traces tagged {args.tag}: {len(traces)}")
        sessions = sorted({str(getattr(t, "session_id", None)) for t in traces})
        print(f"  sessions ({len(sessions)}):", sessions)
        return
    if args.session:
        traces = traces_in_session(args.session)
        print(f"Langfuse session {args.session}: {len(traces)} traces")
        for trace in traces:
            print(" ", trace.start_time, trace.name, "trace", trace.trace_id)
        return
    engine = make_engine(Settings(), max_connections=1)
    with engine.connect() as conn:
        print("connections for user postgres:", conn.execute(text(CONNECTIONS)).scalar_one())
        print("queue:", dict(conn.execute(text("SELECT status, count(*) FROM claims WHERE copy_of IS NULL GROUP BY status")).all()))
        for row in conn.execute(text("SELECT run_id, kind, status, workers, eval_set, tiny, error FROM runs ORDER BY run_id")).all():
            print("run:", tuple(row))
        if args.run:
            show_run(conn, args.run)
        if args.round:
            show_round(conn, args.round)
        if args.claim:
            row = conn.execute(text("SELECT status, attempts, thread_id FROM claims WHERE claim_id = :c"), {"c": args.claim}).one()
            print(f"{args.claim}: status={row[0]} attempts={row[1]} thread={row[2]}")
            print("decisions:", conn.execute(text("SELECT count(*) FROM decisions WHERE claim_id = :c"), {"c": args.claim}).scalar_one())
            payment = conn.execute(text("SELECT status, amount, approved_by, approved_at, paid_at FROM payments "
                                        "WHERE claim_id = :c"), {"c": args.claim}).first()
            print("payment:", tuple(payment) if payment else None)
    engine.dispose()
    if args.claim:
        session = session_name(args.claim, row[2])
        traces = traces_in_session(session)
        print(f"Langfuse session {session}: {len(traces)} traces")
        for trace in traces:
            print(" ", trace.start_time, trace.name, "trace", trace.trace_id)


if __name__ == "__main__":
    main()
