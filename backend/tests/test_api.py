import pytest
from fastapi.testclient import TestClient

from app.agent import load_instructions
from app.api import create_app
from app.config import Settings


@pytest.fixture
def client(db_path, data_dir):
    settings = Settings(_env_file=None, agent_provider="scripted", db_path=db_path, data_dir=data_dir)
    with TestClient(create_app(settings)) as c:
        yield c


def test_list_claims_returns_25_waiting(client):
    claims = client.get("/claims").json()
    assert len(claims) == 25
    assert {c["status"] for c in claims} == {"waiting"}
    first = claims[0]
    assert first["claim_id"] == "CLM-1001" and first["customer_name"] == "Maya Patel"
    assert first["decision"] is None


def test_get_unknown_claim_404(client):
    assert client.get("/claims/CLM-9999").status_code == 404
    assert client.get("/claims/CLM-9999/trace").status_code == 404
    assert client.post("/claims/CLM-9999/run").status_code == 404


def test_run_claim_then_detail_shows_decision(client):
    assert client.post("/claims/CLM-1001/run").status_code == 202
    detail = client.get("/claims/CLM-1001").json()
    assert detail["status"] == "decided"
    assert detail["decision"]["decision"] == "approve"
    assert detail["decision"]["model"] == "scripted"
    assert detail["payment"]["status"] == "paid"
    assert detail["policy"]["policy_id"] == "POL-2001"
    assert detail["customer"]["name"] == "Maya Patel"
    trace = client.get("/claims/CLM-1001/trace").json()
    assert [e["seq"] for e in trace] == sorted(e["seq"] for e in trace)
    assert trace[0]["kind"] == "status" and trace[-1]["kind"] == "decision"


def test_run_claim_twice_conflicts(client):
    client.post("/claims/CLM-1001/run")
    assert client.post("/claims/CLM-1001/run").status_code == 409


def test_run_queue_decides_everything(client):
    assert client.post("/queue/run").status_code == 202
    statuses = {c["status"] for c in client.get("/claims").json()}
    assert statuses == {"decided", "awaiting_approval"}


def test_approve_flow_moves_claim_to_decided(client):
    client.post("/queue/run")
    held = client.get("/approvals").json()
    assert {h["claim_id"] for h in held} == {"CLM-1003", "CLM-1008", "CLM-1011"}
    pid = held[0]["payment_id"]
    resp = client.post(f"/payments/{pid}/approve", json={"handler_id": "HND-01"})
    assert resp.status_code == 200 and resp.json()["status"] == "paid"
    assert client.get(f"/claims/{held[0]['claim_id']}").json()["status"] == "decided"
    assert len(client.get("/approvals").json()) == 2


def test_approve_with_unauthorized_handler_403(client):
    client.post("/queue/run")
    pid = client.get("/approvals").json()[0]["payment_id"]
    assert client.post(f"/payments/{pid}/approve", json={"handler_id": "HND-99"}).status_code == 403
    assert len(client.get("/approvals").json()) == 3


def test_approve_unknown_payment_404(client):
    assert client.post("/payments/PAY-nope/approve", json={"handler_id": "HND-01"}).status_code == 404


def test_handlers_lists_approvers(client):
    ids = [h["handler_id"] for h in client.get("/handlers").json()]
    assert ids == ["HND-01", "HND-02", "HND-03"]


def test_instructions_endpoint_matches_file(client):
    assert client.get("/instructions").json() == {"text": load_instructions()}


@pytest.mark.parametrize("path", [
    "/evals", "/evals/decisions.json", "/data/evals/decisions.json",
    "/claims/..%2Fevals%2Fdecisions.json", "/claims/../evals/decisions.json", "/security",
])
def test_no_route_serves_evals(client, path):
    assert client.get(path).status_code == 404


def test_claims_left_in_progress_by_a_crash_go_back_to_waiting(db_path, data_dir):
    import json

    from app.db import connect, init_schema

    settings = Settings(_env_file=None, agent_provider="scripted", db_path=db_path, data_dir=data_dir)
    with TestClient(create_app(settings)):
        pass
    conn = connect(db_path)
    conn.execute("UPDATE claims SET status='in_progress' WHERE claim_id='CLM-1001'")
    conn.execute("INSERT INTO trace_events (claim_id, seq, kind, content) VALUES ('CLM-1001', 1, 'status', ?)",
                 (json.dumps({"status": "in_progress"}),))
    conn.commit()
    conn.close()

    with TestClient(create_app(settings)) as client:
        assert client.get("/claims").json()[0]["status"] == "waiting"
        assert client.get("/claims/CLM-1001/trace").json() == []
        assert client.post("/claims/CLM-1001/run").status_code == 202
        assert client.get("/claims/CLM-1001").json()["status"] == "decided"


def test_run_queue_for_selected_claims_only(client):
    resp = client.post("/queue/run", json={"claim_ids": ["CLM-1002", "CLM-1001"]})
    assert resp.status_code == 202 and resp.json() == {"queued": 2}
    statuses = {c["claim_id"]: c["status"] for c in client.get("/claims").json()}
    assert statuses["CLM-1001"] == "decided" and statuses["CLM-1002"] == "decided"
    assert statuses["CLM-1003"] == "waiting"


def test_run_queue_with_unknown_claim_id_404(client):
    assert client.post("/queue/run", json={"claim_ids": ["CLM-1001", "CLM-9999"]}).status_code == 404
    assert client.get("/claims").json()[0]["status"] == "waiting"


def test_claim_detail_exposes_the_agent_usage(client, db_path):
    import json

    from app.db import connect

    client.post("/claims/CLM-1001/run")
    assert client.get("/claims/CLM-1001").json()["usage"] is None  # the scripted agent reports none
    conn = connect(db_path)
    usage = {"calls": 2, "input_tokens": 300, "output_tokens": 130, "reasoning_tokens": 0, "cost_usd": 0.0035}
    conn.execute("INSERT INTO trace_events (claim_id, seq, kind, content) VALUES ('CLM-1001', 99, 'usage', ?)",
                 (json.dumps(usage),))
    conn.commit()
    assert client.get("/claims/CLM-1001").json()["usage"] == usage
