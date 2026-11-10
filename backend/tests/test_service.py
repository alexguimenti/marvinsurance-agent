import httpx
import pytest
from fastapi.testclient import TestClient

from agent.service import create_service
from agent.dispatcher import dispatch
from agent.worker import carry_on, open_env, work_claim
from app import runs
from app.api import create_app
from app.claims import get_claim
from app.config import Settings
from app.db import init_schema, make_engine
from app.payments import get_payment, sign_off
from app.reference import Reference
from app.seed import seed_claims
from tests.fakes import api_client, service_client, MINT, RawDb, agent_env, approve, stub


def make_settings(db_path, data_dir):
    return Settings(_env_file=None, agent_provider="scripted", db_path=db_path, data_dir=data_dir)


@pytest.fixture
def service(db_path, data_dir):
    with service_client(create_service(make_settings(db_path, data_dir), inline=True)) as c:
        yield c


def engine_for(db_path):
    return make_engine(Settings(_env_file=None, db_path=db_path), max_connections=2)


def test_health(service):
    assert service.get("/health").status_code == 200


def test_service_starts_a_dispatch_with_n(service, db_path):
    answer = service.post("/dispatches", json={"workers": 4})
    assert answer.status_code == 202 and answer.json() == {"status": "started", "run_id": 1}
    engine = engine_for(db_path)
    row = runs.latest_run(engine, "dispatch")
    assert (row["status"], row["workers"], row["model"], row["reasoning_effort"]) == ("finished", 4, "scripted", "default")
    rows = RawDb(engine).execute("SELECT COUNT(*) FROM claims WHERE status = 'waiting'")
    assert rows.fetchone()[0] == 0


@pytest.mark.parametrize("workers", [0, 26])
def test_service_refuses_n_outside_1_to_25(service, workers):
    assert service.post("/dispatches", json={"workers": workers}).status_code == 422


def test_service_refuses_a_second_start_while_one_runs(service, db_path):
    RawDb(engine_for(db_path)).execute(
        "INSERT INTO runs (kind, status, workers, model, reasoning_effort) VALUES ('dispatch', 'running', 3, 'm', 'none')"
    )
    assert service.post("/dispatches", json={"workers": 3}).status_code == 409


def test_service_start_marks_a_running_run_stopped(db_path, data_dir):
    engine = engine_for(db_path)
    init_schema(engine)
    RawDb(engine).execute(
        "INSERT INTO runs (kind, status, workers, model, reasoning_effort) VALUES ('dispatch', 'running', 3, 'm', 'none')"
    )
    with service_client(create_service(make_settings(db_path, data_dir), inline=True)) as service:
        assert runs.get_run(engine, 1)["status"] == "stopped"
        assert service.post("/dispatches", json={"workers": 3}).status_code == 202


def test_service_start_leaves_stopped_claims_alone(db_path, data_dir):
    engine = engine_for(db_path)
    init_schema(engine)
    seed_claims(engine, Reference(data_dir))
    RawDb(engine).execute("UPDATE claims SET status='in_progress', thread_id='CLM-1001-1' WHERE claim_id='CLM-1001'")
    with service_client(create_service(make_settings(db_path, data_dir), inline=True)):
        pass
    claim = get_claim(engine, "CLM-1001")
    assert claim["status"] == "in_progress" and claim["decision"] is None


def test_a_sign_off_through_the_backend_pays_the_claim(db_path, data_dir):
    """The dashboard's path end to end: the backend records the sign-off and pays, in the one request."""
    settings = make_settings(db_path, data_dir)
    with service_client(create_service(settings, inline=True)) as service:
        with api_client(create_app(settings, http=service, jwks=MINT), headers=MINT.header()) as backend:
            backend.post("/dispatches", json={"workers": 3})
            assert backend.get("/claims/CLM-1003").json()["status"] == "awaiting_approval"
            answer = backend.post("/payments/PAY-CLM-1003/approve")
            assert answer.status_code == 200 and answer.json()["status"] == "paid"
            detail = backend.get("/claims/CLM-1003").json()
    assert detail["status"] == "decided"
    assert detail["payment"]["status"] == "paid"
    assert detail["payment"]["approved_by"] == "HND-01"
    assert detail["payment"]["approved_by_name"] == "Dana Whitfield"
    assert detail["payment"]["approved_at"] and detail["payment"]["paid_at"]


class Down:
    def post(self, *args, **kwargs):
        raise httpx.ConnectError("the service is not running")


def test_a_sign_off_does_not_need_the_agents_service(db_path, data_dir):
    """The app on Lambda cannot reach the service: the sign-off pays with the service down."""
    with agent_env(db_path, data_dir, stub({"CLM-1003": [approve(2000.0)]})) as env:
        work_claim(env, "CLM-1003")
        with api_client(create_app(make_settings(db_path, data_dir), http=Down(), jwks=MINT), headers=MINT.header()) as backend:
            answer = backend.post("/payments/PAY-CLM-1003/approve")
            assert answer.status_code == 200
            assert backend.get("/claims/CLM-1003").json()["status"] == "decided"
        payment = get_payment(env.engine, "CLM-1003")
    assert (payment.status, payment.approved_by) == ("paid", "HND-01")


def test_a_dispatch_with_no_agent_is_503(db_path, data_dir):
    with api_client(create_app(make_settings(db_path, data_dir), http=Down(), jwks=MINT), headers=MINT.header()) as backend:
        answer = backend.post("/dispatches", json={"workers": 3})
        assert answer.status_code == 503 and "not running" in answer.json()["detail"]
        assert backend.get("/dispatches/latest").json()["run"] is None


@pytest.mark.parametrize("make", [
    lambda s: create_app(s, http=Down()),
    lambda s: create_service(s, inline=True),
])
def test_every_operation_follows_the_api_rules(db_path, data_dir, make):
    app = make(make_settings(db_path, data_dir))
    schema = app.openapi()
    ids = []
    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            where = f"{method} {path}"
            assert operation.get("operationId"), where
            assert not operation["operationId"].endswith(f"_{method}"), f"{where}: generated, not chosen"
            ids.append(operation["operationId"])
            text = operation.get("description", "")
            assert text and "\n" not in text.strip(), f"{where}: one-line docstring"
            assert all(p.get("required") for p in operation.get("parameters", [])), f"{where}: optional parameter"
            body = operation.get("requestBody")
            if body:
                assert body.get("required"), f"{where}: optional body"
    assert len(ids) == len(set(ids))
    for name, model in schema["components"]["schemas"].items():
        if name.endswith("Error"):
            continue
        assert set(model.get("required", [])) == set(model.get("properties", {})), f"{name}: an optional input"


# --- the final review's findings ---------------------------------------------------------------------

def test_a_database_error_before_a_start_does_not_lock_the_service(db_path, data_dir, monkeypatch):
    import agent.service as service_module

    real = service_module.api.running_run
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("the pooler closed the connection")
        return real()

    monkeypatch.setattr(service_module.api, "running_run", flaky)
    with service_client(create_service(make_settings(db_path, data_dir), inline=True), raise_server_exceptions=False) as service:
        assert service.post("/dispatches", json={"workers": 3}).status_code == 500
        assert service.post("/dispatches", json={"workers": 3}).status_code == 202


def test_a_start_that_fails_to_create_its_run_says_so(db_path, data_dir, monkeypatch):
    import agent.service as service_module

    def broken(*args, **kwargs):
        raise RuntimeError("could not write the run")

    real = service_module.api.start_dispatch
    monkeypatch.setattr(service_module.api, "start_dispatch", broken)
    with service_client(create_service(make_settings(db_path, data_dir), inline=True), raise_server_exceptions=False) as service:
        assert service.post("/dispatches", json={"workers": 3}).status_code == 500
        monkeypatch.setattr(service_module.api, "start_dispatch", real)
        assert service.post("/dispatches", json={"workers": 3}).status_code == 202


def test_an_evaluation_half_made_ends_stopped_and_frees_the_next_start(db_path, data_dir, monkeypatch):
    import app.evaluations as evaluations_module

    def broken(*args, **kwargs):
        raise RuntimeError("could not list the copies")

    real = evaluations_module.runs.add_run_claims
    monkeypatch.setattr(evaluations_module.runs, "add_run_claims", broken)
    with service_client(create_service(make_settings(db_path, data_dir), inline=True), raise_server_exceptions=False) as service:
        body = {"eval_set": "evaluation", "tiny": True, "workers": 3}
        assert service.post("/evaluations", json=body).status_code == 500
        assert runs.get_run(engine_for(db_path), 1)["status"] == "stopped"
        monkeypatch.setattr(evaluations_module.runs, "add_run_claims", real)
        assert service.post("/dispatches", json={"workers": 3}).status_code == 202


def test_a_service_error_with_a_plain_text_body_reaches_the_dashboard(db_path, data_dir):
    class Broken:
        def post(self, url, json=None, timeout=None, headers=None):
            return httpx.Response(500, text="Internal Server Error")

    with api_client(create_app(make_settings(db_path, data_dir), http=Broken(), jwks=MINT), headers=MINT.header()) as backend:
        answer = backend.post("/dispatches", json={"workers": 3})
        assert answer.status_code == 500 and "Internal Server Error" in answer.json()["detail"]


