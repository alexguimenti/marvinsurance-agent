import json

import pytest
from fastapi.testclient import TestClient

from agent.prompts import load_instructions
from agent.service import create_service
from agent.worker import work_claim
from app.api import create_app
from app.config import Settings
from tests.fakes import agent_env, connect, decline, stub, tool_call


def make_settings(db_path, data_dir, **over):
    return Settings(_env_file=None, agent_provider="scripted", db_path=db_path, data_dir=data_dir, **over)


@pytest.fixture
def client(db_path, data_dir):
    settings = make_settings(db_path, data_dir)
    with TestClient(create_service(settings, inline=True)) as service:  # work is done before a call returns
        with TestClient(create_app(settings, http=service)) as c:
            yield c


def statuses(client):
    return {c["claim_id"]: c["status"] for c in client.get("/claims").json()}


def test_list_claims_returns_25_waiting(client):
    claims = client.get("/claims").json()
    assert len(claims) == 25
    assert {c["status"] for c in claims} == {"waiting"}
    first = claims[0]
    assert first["claim_id"] == "CLM-1001" and first["customer_name"] == "Maya Patel"
    assert first["decision"] is None and first["attempts"] == 0


def test_get_unknown_claim_404(client):
    assert client.get("/claims/CLM-9999").status_code == 404
    assert client.get("/claims/CLM-9999/trace").status_code == 404


def test_one_named_claim_then_detail_shows_decision(client):
    assert client.post("/runs/selected", json={"claim_ids": ["CLM-1001"]}).status_code == 202
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


def test_post_runs_with_limit_works_that_many(client):
    resp = client.post("/runs", json={"limit": 3})
    assert resp.status_code == 202 and resp.json()["run_id"] == 1
    assert list(statuses(client).values()).count("waiting") == 22
    latest = client.get("/runs/latest").json()
    assert latest["run"]["status"] == "finished" and latest["run"]["cap"] == 3
    assert latest["run"]["summary"]
    assert [i["status"] for i in latest["todo"]] == ["done"] * 3


def test_post_runs_with_claim_ids(client):
    client.post("/runs/selected", json={"claim_ids": ["CLM-1002", "CLM-1001"]})
    s = statuses(client)
    assert s["CLM-1001"] == "decided" and s["CLM-1002"] == "decided"
    assert s["CLM-1003"] == "waiting"


def test_a_limit_of_every_claim_works_the_whole_queue(client):
    client.post("/runs", json={"limit": 25})
    values = list(statuses(client).values())
    assert values.count("awaiting_approval") == 3
    assert values.count("needs_handler") == 1
    assert values.count("decided") == 21


def test_second_run_conflicts(client, db_path):
    conn = connect(db_path)
    conn.execute("INSERT INTO runs (status, model, heartbeat_at) VALUES ('running', 'm', CURRENT_TIMESTAMP)")
    resp = client.post("/runs", json={"limit": 1})
    assert resp.status_code == 409 and "already in progress" in resp.json()["detail"]


def test_post_runs_unknown_claim_404(client):
    assert client.post("/runs/selected", json={"claim_ids": ["CLM-1001", "CLM-9999"]}).status_code == 404
    assert statuses(client)["CLM-1001"] == "waiting"


def test_post_runs_limit_zero_422(client):
    assert client.post("/runs", json={"limit": 0}).status_code == 422


def test_runs_latest_empty(client):
    assert client.get("/runs/latest").json() == {"run": None, "todo": []}


def test_run_by_id_has_events_and_unknown_is_404(client):
    client.post("/runs", json={"limit": 1})
    run = client.get("/runs/1").json()
    assert run["run"]["run_id"] == 1 and run["events"] and len(run["todo"]) == 1
    assert client.get("/runs/99").status_code == 404


def test_needs_handler_claim_after_run(client):
    client.post("/runs/selected", json={"claim_ids": ["CLM-1013"]})
    detail = client.get("/claims/CLM-1013").json()
    assert detail["status"] == "needs_handler"
    assert detail["attempts"] == 2 and "scripted failure" in detail["last_error"]
    assert detail["decision"] is None
    assert client.get("/runs/latest").json()["todo"][0]["status"] == "left_for_handler"


def test_claim_failing_once_is_decided_in_the_same_run(client):
    client.post("/runs/selected", json={"claim_ids": ["CLM-1017"]})
    detail = client.get("/claims/CLM-1017").json()
    assert detail["status"] == "decided" and detail["attempts"] == 1


def read_with(db_path, data_dir, *reads):
    """Work a claim on a stub model that reads these sections, and ask the backend what it read."""
    steps = [tool_call("read_section", {"document": d, "section": n}, f"r{i}") for i, (d, n) in enumerate(reads)]
    with agent_env(db_path, data_dir, stub({"CLM-1001": [[*steps, decline()]]})) as env:
        work_claim(env, "CLM-1001")
    with TestClient(create_app(make_settings(db_path, data_dir))) as client:
        return client.get("/claims/CLM-1001").json()["read"]


def test_claim_detail_lists_what_was_read(db_path, data_dir):
    read = read_with(db_path, data_dir, ("claims_manual", 7), ("policy_wording", 5), ("claims_manual", 7))
    assert read == [
        {"document": "claims_manual", "section": 7, "title": "Interpretation notes"},
        {"document": "policy_wording", "section": 5, "title": "Baggage and Personal Effects"},
    ]


def test_a_refused_read_is_not_listed(db_path, data_dir):
    assert read_with(db_path, data_dir, ("claims_manual", 99)) == []


def test_post_runs_with_an_empty_claim_list_422(client):
    assert client.post("/runs/selected", json={"claim_ids": []}).status_code == 422
    assert client.get("/runs/latest").json()["run"] is None


def test_claim_usage_sums_every_attempt(client, db_path):
    client.post("/runs/selected", json={"claim_ids": ["CLM-1001"]})
    conn = connect(db_path)
    conn.execute("DELETE FROM trace_events WHERE kind='usage'")
    usage = {"calls": 2, "input_tokens": 300, "output_tokens": 130, "reasoning_tokens": 0, "cost_usd": 0.0035}
    for seq in (98, 99):
        conn.execute("INSERT INTO trace_events (claim_id, seq, kind, content) VALUES ('CLM-1001', ?, 'usage', ?)",
                     (seq, json.dumps(usage)))
    conn.commit()
    total = client.get("/claims/CLM-1001").json()["usage"]
    assert total["calls"] == 4 and total["cost_usd"] == pytest.approx(0.007)


def test_sign_off_is_recorded_and_the_agent_pays(client):
    client.post("/runs", json={"limit": 25})
    held = client.get("/approvals").json()
    assert {h["claim_id"] for h in held} == {"CLM-1003", "CLM-1008", "CLM-1011"}
    pid = held[0]["payment_id"]
    resp = client.post(f"/payments/{pid}/approve", json={"handler_id": "HND-01"})
    assert resp.status_code == 200 and resp.json()["status"] == "signed_off"
    assert resp.json()["approved_by"] == "HND-01" and resp.json()["approved_at"] and resp.json()["paid_at"] is None
    detail = client.get(f"/claims/{held[0]['claim_id']}").json()
    assert detail["status"] == "decided" and detail["payment"]["status"] == "paid"  # the service heard, and paid
    assert len(client.get("/approvals").json()) == 2


def test_approve_with_unauthorized_handler_403(client):
    client.post("/runs", json={"limit": 25})
    pid = client.get("/approvals").json()[0]["payment_id"]
    assert client.post(f"/payments/{pid}/approve", json={"handler_id": "HND-99"}).status_code == 403
    assert len(client.get("/approvals").json()) == 3


def test_approve_unknown_payment_404(client):
    assert client.post("/payments/PAY-nope/approve", json={"handler_id": "HND-01"}).status_code == 404


def test_handlers_lists_approvers(client):
    ids = [h["handler_id"] for h in client.get("/handlers").json()]
    assert ids == ["HND-01", "HND-02", "HND-03"]


def test_instructions_returns_both_as_the_agents_receive_them(client):
    got = client.get("/instructions").json()
    assert got["run"] == load_instructions("run")
    assert got["claim"].startswith(load_instructions("claim"))
    assert "Claims manual: 1. Purpose and scope" in got["claim"]  # the section index the claim agent is given


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
    with TestClient(create_app(settings)):
        pass
    conn = connect(db_path)
    conn.execute("INSERT INTO runs (status, model) VALUES ('running', 'm')")
    conn.execute("UPDATE claims SET status='in_progress', run_id=1, thread_id='CLM-1001-1' WHERE claim_id='CLM-1001'")

    with TestClient(create_app(settings)) as client:  # a stopped run is the next run's to finish, not the backend's
        assert client.get("/runs/latest").json()["run"]["status"] == "running"
        assert client.get("/claims/CLM-1001").json()["status"] == "in_progress"
