"""Start a run from the command line: `uv run -m agent --limit 5`, from the backend folder.

If the agent's service is up, it does the work, so that one process owns the queue. If not, the run happens
here. Either way the run first carries on with any claim that was stopped or signed off while the agent was away.
"""
import argparse
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv
from sqlalchemy import func, select

from agent.queue import begin_run, work_run
from agent.service import prepare_database
from agent.worker import open_env
from app import runs
from app.config import Settings
from app.db import claims, make_engine

ROOT = Path(__file__).resolve().parents[2]
POLL_S = 2


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="agent", description="Work the claims queue.")
    parser.add_argument("--limit", type=int, help="the most claims to take (default: every waiting claim)")
    parser.add_argument("--claims", help="work only these claims, comma-separated")
    return parser.parse_args(argv)


def _service_is_up(settings: Settings) -> bool:
    try:
        return httpx.get(f"{settings.agent_url}/health", timeout=1).status_code == 200
    except httpx.HTTPError:
        return False


def _waiting(settings: Settings) -> int:
    engine = make_engine(settings, max_connections=1)
    try:
        with engine.connect() as conn:
            return conn.execute(select(func.count()).select_from(claims).where(claims.c.status == "waiting")).scalar_one()
    finally:
        engine.dispose()


def _print_run(row) -> int:
    print(f"Run {row['run_id']}: {row['status']}")
    if row["summary"]:
        print(row["summary"])
    if row["error"]:
        print(row["error"])
    return 0 if row["status"] == "finished" else 1


def _hand_over(settings: Settings, limit: int | None, claim_ids: list[str] | None) -> int:
    if claim_ids:
        path, body = "/runs/selected", {"claim_ids": claim_ids}
    else:
        path, body = "/runs", {"limit": limit or max(_waiting(settings), 1)}
    answer = httpx.post(f"{settings.agent_url}{path}", json=body, timeout=10)
    if answer.status_code == 409:
        print("A run is already in progress.")
        return 1
    answer.raise_for_status()
    run_id = answer.json()["run_id"]
    print(f"Run {run_id} is with the agent's service.")
    engine = make_engine(settings, max_connections=1)
    try:
        while (row := runs.get_run(engine, run_id)) is not None and row["status"] == "running":
            time.sleep(POLL_S)
        return _print_run(row)
    finally:
        engine.dispose()


def _run_here(settings: Settings, limit: int | None, claim_ids: list[str] | None) -> int:
    with open_env(settings) as env:
        try:
            run_id, adopted = begin_run(env, limit, claim_ids)
        except runs.RunActive:
            print("A run is already in progress.")
            return 1
        if adopted:
            print(f"Finishing run {run_id}, which was stopped, with its own limit and claims.")
        work_run(env, run_id, adopted)
        return _print_run(runs.get_run(env.engine, run_id))


def main(argv: list[str] | None = None) -> int:
    args = _parse(sys.argv[1:] if argv is None else argv)
    load_dotenv(ROOT / ".env")  # this entry point only: importing the agent must not read it
    settings = Settings()
    prepare_database(settings)
    claim_ids = args.claims.split(",") if args.claims else None
    if _service_is_up(settings):
        return _hand_over(settings, args.limit, claim_ids)
    return _run_here(settings, args.limit, claim_ids)


if __name__ == "__main__":
    sys.exit(main())
