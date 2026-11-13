"""Waiting for a dispatch or an evaluation in code, and what needs a handler after one."""
import json

from agent.copilot import waiting
from agent.connection import Refused  # noqa: F401
from tests.test_copilot_tools import App, DANA, run_tool
from agent import connection
import httpx


class Time:
    """A clock that only moves when the wait sleeps, so a wait of 15 minutes takes no time."""

    def __init__(self):
        self.now, self.sleeps = 0.0, []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class Runs:
    """The app's answers for one run: `running` for each read in `running_for`, then `finished`."""

    def __init__(self, running_for: int, final="finished", claims=None):
        self.reads, self.running_for, self.final, self.claims = 0, running_for, final, claims

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.reads += 1
        status = "running" if self.reads <= self.running_for else self.final
        report = {"run": {"run_id": 5, "status": status, "error": None}, "measures": {"accuracy": 0.8}}
        if self.claims:
            report["claims"] = self.claims
        return httpx.Response(200, json=report)


def wait(runs: Runs, time: Time, kind="evaluation", **kwargs):
    with connection.use(httpx.Client(transport=httpx.MockTransport(runs))):
        return waiting.wait_for_run("tok-dana", kind, 5, clock=time.clock, sleep=time.sleep, **kwargs)


def test_the_wait_polls_every_3s_and_returns_when_finished():
    time, runs = Time(), Runs(running_for=4)
    result = wait(runs, time)
    assert result["status"] == "finished" and result["run"]["measures"] == {"accuracy": 0.8}
    assert runs.reads == 5 and time.sleeps == [3, 3, 3, 3]  # every 3 seconds, and no model call: the wait is this loop


def test_a_dispatch_is_read_from_its_own_route_and_a_stopped_run_says_so():
    seen = []
    runs = Runs(running_for=0, final="stopped")

    def record(request):
        seen.append(request.url.path)
        return runs(request)

    with connection.use(httpx.Client(transport=httpx.MockTransport(record))):
        result = waiting.wait_for_run("tok", "dispatch", 5)
    assert seen == ["/api/dispatches/5"] and result["status"] == "stopped"


def test_the_evaluations_claims_with_the_handlers_decisions_are_dropped():
    result = wait(Runs(0, claims=[{"claim_id": "CLM-1", "handler": {"decision": "approve"}}]), Time())
    assert "claims" not in result["run"]


def test_after_15_minutes_it_is_still_going_with_where_to_follow():
    time, runs = Time(), Runs(running_for=10_000)
    result = wait(runs, time)
    assert result["status"] == "still_going" and "/scoreboard/5" in result["follow"] and "15 minutes" in result["follow"]
    assert time.now >= 900 and runs.reads <= 302
    assert "Claims page" in wait(Runs(running_for=10_000), Time(), kind="dispatch")["follow"]


def test_a_refused_start_passes_the_reason_on():
    app = App({("GET", "/api/settings"): (200, {"settings": [{"name": "agents_at_once", "value": 25}]}),
               ("POST", "/api/dispatches"): (409, {"detail": "a dispatch is already running"})})
    message = run_tool(app, "start_dispatch", {})
    assert message.content == "Refused: a dispatch is already running"


def test_what_needs_me_after_a_dispatch():
    queue = [
        {"claim_id": "CLM-1", "customer_name": "A", "claim_type": "baggage_delay", "status": "decided", "decision": "approve", "payout": 100.0},
        {"claim_id": "CLM-2", "customer_name": "B", "claim_type": "medical", "status": "decided", "decision": "refer", "payout": 0.0},
        {"claim_id": "CLM-3", "customer_name": "C", "claim_type": "medical", "status": "needs_handler", "decision": None, "payout": None},
        {"claim_id": "CLM-4", "customer_name": "D", "claim_type": "trip", "status": "sent_back", "decision": None, "payout": None},
        {"claim_id": "CLM-5", "customer_name": "E", "claim_type": "trip", "status": "awaiting_approval", "decision": "approve", "payout": 3750.0},
    ]
    held = [{"payment_id": "PAY-CLM-5", "claim_id": "CLM-5", "amount": 3750.0}]
    app = App({("GET", "/api/claims"): (200, queue), ("GET", "/api/approvals"): (200, held)})
    out = json.loads(run_tool(app, "what_needs_me", {}).content)
    assert out["held_for_sign_off"] == held
    assert [c["claim_id"] for c in out["referred_or_left"]] == ["CLM-2", "CLM-3"]
    assert [c["claim_id"] for c in out["sent_back_not_worked"]] == ["CLM-4"]
