import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.configs import load_instructions
from tests.fakes import create_service
from agent.worker import work_claim
from app.api import create_app
from app.config import Settings
from tests.fakes import service_client, api_client, MINT, agent_env, connect, decline, stub, tool_call


def make_settings(db_path, data_dir, **over):
    return Settings(_env_file=None, agent_provider="scripted", db_path=db_path, data_dir=data_dir, **over)


@pytest.fixture
def client(db_path, data_dir):
    settings = make_settings(db_path, data_dir)
    with service_client(create_service(settings, inline=True)) as service:  # work is done before a call returns
        with api_client(create_app(settings, http=service, jwks=MINT), headers=MINT.header()) as c:
            c.service = service  # for the tests that ask the agent itself
            yield c


def statuses(client):
    return {c["claim_id"]: c["status"] for c in client.get("/claims").json()}


def test_list_claims_returns_26_waiting(client):
    claims = client.get("/claims").json()
    assert len(claims) == 26  # the 25 of the queue, and the security team's CLM-1901
    assert {c["status"] for c in claims} == {"waiting"}
    first = claims[0]
    assert first["claim_id"] == "CLM-1001" and first["customer_name"] == "Maya Patel"
    assert first["decision"] is None and first["attempts"] == 0


def test_get_unknown_claim_404(client):
    assert client.get("/claims/CLM-9999").status_code == 404
    assert client.get("/claims/CLM-9999/trace").status_code == 404


def test_a_dispatch_then_detail_shows_decision(client):
    assert client.post("/dispatches", json={"workers": 3}).status_code == 202
    detail = client.get("/claims/CLM-1001").json()
    assert detail["status"] == "decided"
    assert detail["decision"]["decision"] == "approve"
    assert detail["decision"]["model"] == "scripted"
    assert (detail["decision"]["manual_version"], detail["decision"]["wording_version"]) == ("2026.2", "2026.1")
    assert detail["payment"]["status"] == "paid"
    assert detail["policy"]["policy_id"] == "POL-2001"
    assert detail["customer"]["name"] == "Maya Patel"
    trace = client.get("/claims/CLM-1001/trace").json()
    assert [e["seq"] for e in trace] == sorted(e["seq"] for e in trace)
    assert trace[0]["kind"] == "status" and "decision" in [e["kind"] for e in trace]
    assert trace[-1]["content"]["status"] == "paid"


def test_a_dispatch_works_the_whole_queue(client):
    client.post("/dispatches", json={"workers": 5})
    values = list(statuses(client).values())
    assert values.count("awaiting_approval") == 3
    assert values.count("needs_handler") == 1  # CLM-1013 fails every time
    assert values.count("decided") == 22  # CLM-1017 fails once and is tried again; CLM-1901 is referred by the security check


def test_backend_forwards_n_to_the_service(db_path, data_dir):
    class Recorder:
        calls = []

        def post(self, url, json=None, timeout=None, headers=None):
            self.calls.append((url, json))
            return httpx.Response(200, json={"accepted": True})

    with api_client(create_app(make_settings(db_path, data_dir), http=Recorder(), jwks=MINT), headers=MINT.header()) as backend:
        assert backend.post("/dispatches", json={"workers": 3}).status_code == 202
    assert Recorder.calls == [("http://127.0.0.1:8080/invocations", {"operation": "work_run", "run_id": 1})]


@pytest.mark.parametrize("workers", [0, 26])
def test_workers_outside_1_to_25_are_refused(client, workers):
    assert client.post("/dispatches", json={"workers": workers}).status_code == 422


def test_a_second_dispatch_conflicts(client, db_path):
    client.service.post("/invocations", json={"operation": "health"})  # the agent gets ready first: that marks leftovers stopped
    connect(db_path).execute(
        "INSERT INTO runs (kind, status, workers, model, reasoning_effort) VALUES ('dispatch', 'running', 3, 'm', 'none')")
    resp = client.post("/dispatches", json={"workers": 3})
    assert resp.status_code == 409 and "already running" in resp.json()["detail"]


def test_latest_dispatch_empty(client):
    assert client.get("/dispatches/latest").json() == {"run": None, "claims": [], "totals": None, "summary": None}


def test_latest_dispatch_shows_its_claims_summary_and_totals(client):
    client.post("/dispatches", json={"workers": 5})
    latest = client.get("/dispatches/latest").json()
    assert (latest["run"]["status"], latest["run"]["workers"], latest["run"]["kind"]) == ("finished", 5, "dispatch")
    assert len(latest["claims"]) == 26 and all(c["started_at"] for c in latest["claims"])
    assert [p["claim_id"] for p in latest["summary"]["awaiting_sign_off"]] == ["CLM-1003", "CLM-1008", "CLM-1011"]
    assert latest["summary"]["left_for_handler"] == ["CLM-1013"]
    assert latest["totals"]["calls"] > 0 and "seconds" in latest["totals"]


def test_needs_handler_claim_after_a_dispatch(client):
    client.post("/dispatches", json={"workers": 3})
    detail = client.get("/claims/CLM-1013").json()
    assert detail["status"] == "needs_handler"
    assert detail["attempts"] == 2 and "scripted failure" in detail["last_error"]
    assert detail["decision"] is None


def read_with(db_path, data_dir, *reads):
    """Work a claim on a stub model that reads these sections, and ask the backend what it looked up."""
    steps = [tool_call("read_section", {"document": d, "section": n}, f"r{i}") for i, (d, n) in enumerate(reads)]
    with agent_env(db_path, data_dir, stub({"CLM-1001": [[*steps, decline()]]})) as env:
        work_claim(env, "CLM-1001")
    with api_client(create_app(make_settings(db_path, data_dir), jwks=MINT), headers=MINT.header()) as client:
        return client.get("/claims/CLM-1001").json()["context_used"]


def test_claim_detail_lists_what_was_looked_up_once_each_grouped_by_file(db_path, data_dir):
    used = read_with(db_path, data_dir, ("claims_manual", 7), ("policy_wording", 5), ("claims_manual", 7))
    assert [f["file"] for f in used] == ["plans.yaml", "claims_manual.md", "policy_wording.md"]  # the plan is looked up first
    manual = used[1]
    assert (manual["system"], manual["owner"], manual["reviewed"]) == ("Claims operations", "Head of claims operations", "2026-09-01")
    assert manual["lookups"] == [{"tool": "read_section", "args": {"document": "claims_manual", "section": 7}}]
    assert used[0]["lookups"] == [{"tool": "get_plan", "args": {"plan": "premium"}}]


def test_a_refused_read_is_not_listed(db_path, data_dir):
    assert [f["file"] for f in read_with(db_path, data_dir, ("claims_manual", 99))] == ["plans.yaml"]


def test_claim_usage_sums_every_attempt_and_its_time(client, db_path):
    conn = connect(db_path)
    for run_id, cost in ((1, 0.002), (2, 0.0015)):  # a failed attempt in one run, the decision in the next
        conn.execute("INSERT INTO model_calls (claim_id, run_id, model, input_tokens, output_tokens, reasoning_tokens, "
                     "cost_usd) VALUES ('CLM-1001', ?, 'm', 150, 65, 0, ?)", (run_id, cost))
        conn.execute("INSERT INTO work_spans (claim_id, run_id, started_at, seconds) VALUES ('CLM-1001', ?, "
                     "CURRENT_TIMESTAMP, 12.5)", (run_id,))
    total = client.get("/claims/CLM-1001").json()["usage"]
    assert total["calls"] == 2 and total["input_tokens"] == 300 and total["cost_usd"] == pytest.approx(0.0035)
    assert total["seconds"] == 25.0


def test_a_sign_off_pays_the_claim_with_no_help_from_the_service(client):
    client.post("/dispatches", json={"workers": 5})
    held = client.get("/approvals").json()
    assert {h["claim_id"] for h in held} == {"CLM-1003", "CLM-1008", "CLM-1011"}
    pid = held[0]["payment_id"]
    resp = client.post(f"/payments/{pid}/approve")
    assert resp.status_code == 200 and resp.json()["status"] == "paid"
    assert resp.json()["approved_by"] == "HND-01" and resp.json()["approved_at"] and resp.json()["paid_at"]
    detail = client.get(f"/claims/{held[0]['claim_id']}").json()
    assert detail["status"] == "decided" and detail["payment"]["status"] == "paid"  # the sign-off itself paid it
    assert len(client.get("/approvals").json()) == 2


def test_approve_with_unauthorized_handler_403(client):
    client.post("/dispatches", json={"workers": 5})
    pid = client.get("/approvals").json()[0]["payment_id"]
    assert client.post(f"/payments/{pid}/approve", headers=MINT.header("HND-02")).status_code == 403  # Marcus
    assert len(client.get("/approvals").json()) == 3


def test_approve_unknown_payment_404(client):
    assert client.post("/payments/PAY-nope/approve").status_code == 404


def test_the_list_of_handlers_to_choose_from_is_gone(client):
    assert client.get("/handlers").status_code == 404  # the signed-in handler signs off, not one picked from a list


def test_the_old_instructions_route_is_gone(client):
    assert client.get("/instructions").status_code == 404


def test_old_run_routes_are_gone(client):
    assert client.post("/queue/run").status_code in (404, 405)
    assert client.post("/claims/CLM-1001/run").status_code in (404, 405)


@pytest.mark.parametrize("path", [
    "/evals", "/evals/decisions.json", "/data/evals/decisions.json",
    "/claims/..%2Fevals%2Fdecisions.json", "/claims/../evals/decisions.json", "/security",
])
def test_no_route_serves_evals(client, path):
    assert client.get(path).status_code == 404


def test_backend_restart_changes_nothing(db_path, data_dir):
    settings = make_settings(db_path, data_dir)
    with api_client(create_app(settings, jwks=MINT), headers=MINT.header()):
        pass
    conn = connect(db_path)
    conn.execute("INSERT INTO runs (kind, status, workers, model, reasoning_effort) VALUES ('dispatch', 'running', 3, 'm', 'none')")
    conn.execute("UPDATE claims SET status='in_progress', run_id=1, thread_id='CLM-1001-1' WHERE claim_id='CLM-1001'")

    with api_client(create_app(settings, jwks=MINT), headers=MINT.header()) as client:  # marking a stopped run is the service's job, not the backend's
        assert client.get("/dispatches/latest").json()["run"]["status"] == "running"
        assert client.get("/claims/CLM-1001").json()["status"] == "in_progress"


def test_health_is_open_and_routes_live_under_api(db_path, data_dir):
    with TestClient(create_app(make_settings(db_path, data_dir), jwks=MINT)) as bare:  # no credentials, no prefix in the client
        assert bare.get("/api/health").json() == {"status": "ok"}
        assert bare.get("/claims").status_code == 404
        assert bare.get("/openapi.json").status_code == 404 and bare.get("/docs").status_code == 404
