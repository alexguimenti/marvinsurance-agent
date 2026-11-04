import json

import pytest
from fastapi.testclient import TestClient

from app.agent import load_instructions
from app.api import create_app
from app.config import Settings
from tests.fakes import connect


def make_settings(db_path, data_dir, **over):
    return Settings(_env_file=None, agent_provider="scripted", db_path=db_path, data_dir=data_dir, **over)


@pytest.fixture
def client(db_path, data_dir):
    with TestClient(create_app(make_settings(db_path, data_dir))) as c:
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
    assert client.post("/runs", json={"claim_ids": ["CLM-1001"]}).status_code == 202
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
    assert trace[0]["kind"] == "status" and trace[-1]["kind"] == "decision"


def test_post_runs_with_limit_works_that_many(client):
    resp = client.post("/runs", json={"limit": 3})
    assert resp.status_code == 202 and resp.json()["run_id"] == 1
    assert list(statuses(client).values()).count("waiting") == 22
    latest = client.get("/runs/latest").json()
    assert latest["run"]["status"] == "finished" and latest["run"]["cap"] == 3
    assert latest["run"]["summary"]
    assert [i["status"] for i in latest["todo"]] == ["done"] * 3


def test_post_runs_with_claim_ids(client):
    client.post("/runs", json={"claim_ids": ["CLM-1002", "CLM-1001"]})
    s = statuses(client)
    assert s["CLM-1001"] == "decided" and s["CLM-1002"] == "decided"
    assert s["CLM-1003"] == "waiting"


def test_run_without_limit_works_the_whole_queue(client):
    client.post("/runs")
    values = list(statuses(client).values())
    assert values.count("awaiting_approval") == 3
    assert values.count("needs_handler") == 1
    assert values.count("decided") == 21


def test_second_run_conflicts(client, db_path):
    conn = connect(db_path)
    conn.execute("INSERT INTO runs (status, model) VALUES ('running', 'm')")
    conn.commit()
    resp = client.post("/runs", json={"limit": 1})
    assert resp.status_code == 409 and "already in progress" in resp.json()["detail"]


def test_post_runs_unknown_claim_404(client):
    assert client.post("/runs", json={"claim_ids": ["CLM-1001", "CLM-9999"]}).status_code == 404
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
    client.post("/runs", json={"claim_ids": ["CLM-1013"]})
    detail = client.get("/claims/CLM-1013").json()
    assert detail["status"] == "needs_handler"
    assert detail["attempts"] == 2 and "scripted failure" in detail["last_error"]
    assert detail["decision"] is None
    assert client.get("/runs/latest").json()["todo"][0]["status"] == "left_for_handler"


def test_claim_failing_once_is_decided_in_the_same_run(client):
    client.post("/runs", json={"claim_ids": ["CLM-1017"]})
    detail = client.get("/claims/CLM-1017").json()
    assert detail["status"] == "decided" and detail["attempts"] == 1


def reader_agent(reads):
    """A stub claim agent that reports section reads the way LangChainAgent traces them."""
    from tests.fakes import StubAgent

    class Reader(StubAgent):
        def decide(self, claim_id, on_event):
            for document, section, result in reads:
                on_event("tool_call", {"name": "read_section", "args": {"document": document, "section": section}})
                on_event("tool_result", {"name": "read_section", "content": result})
            return super().decide(claim_id, on_event)

    return Reader()


def run_with(db_path, data_dir, monkeypatch, agent):
    import app.orchestrator as orchestrator_module

    monkeypatch.setattr(orchestrator_module, "build_agent", lambda settings, conn, ref: agent)
    with TestClient(create_app(make_settings(db_path, data_dir))) as client:
        client.post("/runs", json={"claim_ids": ["CLM-1001"]})
        return client.get("/claims/CLM-1001").json()["read"]


def test_claim_detail_lists_what_was_read(db_path, data_dir, monkeypatch):
    read = run_with(db_path, data_dir, monkeypatch, reader_agent([
        ("claims_manual", 7, "[claims_manual section 7: Interpretation notes, 5000 characters]"),
        ("policy_wording", 5, "[policy_wording section 5: Baggage and Personal Effects, 900 characters]"),
        ("claims_manual", 7, "You already read claims_manual section 7 above. Use that text."),
    ]))
    assert read == [
        {"document": "claims_manual", "section": 7, "title": "Interpretation notes"},
        {"document": "policy_wording", "section": 5, "title": "Baggage and Personal Effects"},
    ]


def test_a_refused_read_is_not_listed(db_path, data_dir, monkeypatch):
    read = run_with(db_path, data_dir, monkeypatch, reader_agent([
        ("claims_manual", 9, "Error: you have read 8 sections, the most for one claim. Decide with what you have."),
        ("claims_manual", 99, "Error: claims_manual has no section 99. Documents: policy_wording, claims_manual."),
    ]))
    assert read == []


def test_post_runs_with_an_empty_claim_list_422(client):
    assert client.post("/runs", json={"claim_ids": []}).status_code == 422
    assert client.get("/runs/latest").json()["run"] is None


def test_claim_usage_sums_every_attempt(client, db_path):
    client.post("/runs", json={"claim_ids": ["CLM-1001"]})
    assert client.get("/claims/CLM-1001").json()["usage"] is None  # the scripted agent reports none
    conn = connect(db_path)
    usage = {"calls": 2, "input_tokens": 300, "output_tokens": 130, "reasoning_tokens": 0, "cost_usd": 0.0035}
    for seq in (98, 99):
        conn.execute("INSERT INTO trace_events (claim_id, seq, kind, content) VALUES ('CLM-1001', ?, 'usage', ?)",
                     (seq, json.dumps(usage)))
    conn.commit()
    total = client.get("/claims/CLM-1001").json()["usage"]
    assert total["calls"] == 4 and total["cost_usd"] == pytest.approx(0.007)


def test_sign_off_leaves_the_claim_in_progress_and_does_not_pay(client):
    client.post("/runs")
    held = client.get("/approvals").json()
    assert {h["claim_id"] for h in held} == {"CLM-1003", "CLM-1008", "CLM-1011"}
    pid = held[0]["payment_id"]
    resp = client.post(f"/payments/{pid}/approve", json={"handler_id": "HND-01"})
    assert resp.status_code == 200 and resp.json()["status"] == "signed_off"
    assert resp.json()["approved_by"] == "HND-01" and resp.json()["approved_at"] and resp.json()["paid_at"] is None
    assert client.get(f"/claims/{held[0]['claim_id']}").json()["status"] == "in_progress"
    assert len(client.get("/approvals").json()) == 2


def test_approve_with_unauthorized_handler_403(client):
    client.post("/runs")
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


def test_restart_recovers_running_run(db_path, data_dir):
    settings = make_settings(db_path, data_dir)
    with TestClient(create_app(settings)):
        pass
    conn = connect(db_path)
    conn.execute("INSERT INTO runs (status, model) VALUES ('running', 'm')")
    conn.execute("UPDATE claims SET status='in_progress', run_id=1 WHERE claim_id='CLM-1001'")
    conn.execute("INSERT INTO trace_events (claim_id, seq, kind, content) VALUES ('CLM-1001', 1, 'status', ?)",
                 (json.dumps({"status": "in_progress"}),))
    conn.commit()
    conn.close()

    with TestClient(create_app(settings)) as client:
        assert client.get("/runs/latest").json()["run"]["status"] == "failed"
        assert statuses(client)["CLM-1001"] == "waiting"
        assert len(client.get("/claims/CLM-1001/trace").json()) == 2  # the old trace is kept
        assert client.post("/runs", json={"claim_ids": ["CLM-1001"]}).status_code == 202
        assert client.get("/claims/CLM-1001").json()["status"] == "decided"


def test_api_payout_takes_the_limit_from_company_json(client):
    body = {"items": [{"description": "a", "amount": 1200, "item_limit": None}],
            "third_party_paid": 0, "deductible": 200, "benefit_max": None}
    answer = client.post("/payouts/calculate", json=body).json()
    assert answer["payout"] == 1000 and answer["approval_limit"] == 1000 and answer["needs_sign_off"] is False
    del body["deductible"]
    assert client.post("/payouts/calculate", json=body).status_code == 422
