"""Waiting for the agent's work, in code. When a handler starts a dispatch or an evaluation and wants to know how it went, the Copilot
checks the app every few seconds with a read a handler may use, and never calls the model again and again to do it. After 15 minutes it
stops waiting and says the work is still going and where to follow it."""
import time
from typing import Callable, Literal

from agent.copilot import app_api

POLL_S = 3
LIMIT_S = 15 * 60
Kind = Literal["dispatch", "evaluation"]


def wait_for_run(token: str, kind: Kind, run_id: int, poll_s: float = POLL_S, limit_s: float = LIMIT_S,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep) -> dict:
    """Wait for a dispatch or an evaluation to end. Answers `{"status", "run"}`: the run's status (`finished` or `stopped`, with its
    reason in `run.run.error`), or `still_going` after the limit, with where on the dashboard to follow it. An evaluation's report is
    given without its claims, which sit beside the handlers' own decisions."""
    path = f"/api/dispatches/{run_id}" if kind == "dispatch" else f"/api/evaluations/{run_id}"
    deadline = clock() + limit_s
    while True:
        report = app_api.get(token, path)
        report.pop("claims", None)
        status = report["run"]["status"]
        if status != "running":
            return {"status": status, "run": report}
        if clock() >= deadline:
            where = "the Claims page" if kind == "dispatch" else f"the Scoreboard, at /scoreboard/{run_id}"
            return {"status": "still_going", "run": report, "follow": f"It is still working after {int(limit_s // 60)} minutes. Follow it on {where}."}
        sleep(poll_s)
