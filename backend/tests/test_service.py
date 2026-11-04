import httpx
import pytest
from fastapi.testclient import TestClient

from agent.service import create_service
from agent.worker import open_env, work_claim
from app.api import create_app
from app.claims import get_claim
from app.config import Settings
from app.db import init_schema, make_engine
from app.payments import get_payment, sign_off
from app.reference import Reference
from app.seed import seed_claims
from tests.fakes import RawDb


def make_settings(db_path, data_dir):
    return Settings(_env_file=None, agent_provider="scripted", db_path=db_path, data_dir=data_dir)


@pytest.fixture
def service(db_path, data_dir):
    with TestClient(create_service(make_settings(db_path, data_dir), inline=True)) as c:
        yield c


def engine_for(db_path):
    return make_engine(Settings(_env_file=None, db_path=db_path), max_connections=2)


def test_health(service):
    assert service.get("/health").status_code == 200


def test_run_with_a_limit(service, db_path):
    answer = service.post("/runs", json={"limit": 2})
    assert answer.status_code == 202 and answer.json()["run_id"] == 1
    rows = RawDb(engine_for(db_path)).execute("SELECT COUNT(*) FROM claims WHERE status != 'waiting'")
    assert rows.fetchone()[0] == 2


def test_run_of_selected_claims(service, db_path):
    assert service.post("/runs/selected", json={"claim_ids": ["CLM-1004"]}).status_code == 202
    assert get_claim(engine_for(db_path), "CLM-1004")["status"] == "decided"


def test_selected_run_needs_known_claims(service):
    assert service.post("/runs/selected", json={"claim_ids": ["CLM-9999"]}).status_code == 404
    assert service.post("/runs/selected", json={"claim_ids": []}).status_code == 422


def test_second_run_while_one_is_alive_is_refused(service, db_path):
    RawDb(engine_for(db_path)).execute(
        "INSERT INTO runs (status, model, heartbeat_at) VALUES ('running', 'm', CURRENT_TIMESTAMP)"
    )
    assert service.post("/runs", json={"limit": 1}).status_code == 409


def hold(db_path, data_dir):
    """CLM-1003 waiting for a sign-off, as a run on the scripted agent leaves it."""
    settings = make_settings(db_path, data_dir)
    engine = make_engine(settings, 1)
    init_schema(engine)
    seed_claims(engine, Reference(data_dir))
    with open_env(settings) as env:
        work_claim(env, "CLM-1003")
    return engine


def test_service_resume_pays_once_even_when_asked_twice(db_path, data_dir):
    engine = hold(db_path, data_dir)
    sign_off(engine, Reference(data_dir), "PAY-CLM-1003", "HND-01")
    # the service's start pays it; the two calls after that find nothing left to do
    with TestClient(create_service(make_settings(db_path, data_dir), inline=True)) as service:
        for _ in range(2):
            assert service.post("/claims/CLM-1003/resume").status_code == 202
    assert get_payment(engine, "CLM-1003").status == "paid"
    assert RawDb(engine).execute("SELECT COUNT(*) FROM payments").fetchone()[0] == 1


def test_resume_before_the_sign_off_pays_nothing(db_path, data_dir):
    engine = hold(db_path, data_dir)
    with TestClient(create_service(make_settings(db_path, data_dir), inline=True)) as service:
        assert service.post("/claims/CLM-1003/resume").status_code == 202
    assert get_payment(engine, "CLM-1003").status == "held"
    assert get_claim(engine, "CLM-1003")["status"] == "awaiting_approval"


def test_resume_of_an_unknown_claim(service):
    assert service.post("/claims/CLM-9999/resume").status_code == 404


def test_service_start_pays_claims_signed_off_while_it_was_away(db_path, data_dir):
    engine = hold(db_path, data_dir)
    sign_off(engine, Reference(data_dir), "PAY-CLM-1003", "HND-01")
    assert get_claim(engine, "CLM-1003")["status"] == "in_progress"
    with TestClient(create_service(make_settings(db_path, data_dir), inline=True)):
        pass
    assert get_payment(engine, "CLM-1003").status == "paid"
    assert get_claim(engine, "CLM-1003")["status"] == "decided"


def test_service_start_leaves_stopped_claims_alone(db_path, data_dir):
    engine = engine_for(db_path)
    init_schema(engine)
    seed_claims(engine, Reference(data_dir))
    RawDb(engine).execute("UPDATE claims SET status='in_progress', thread_id='CLM-1001-1' WHERE claim_id='CLM-1001'")
    with TestClient(create_service(make_settings(db_path, data_dir), inline=True)):
        pass
    claim = get_claim(engine, "CLM-1001")
    assert claim["status"] == "in_progress" and claim["decision"] is None


def test_a_full_sign_off_through_backend_and_service(db_path, data_dir):
    """The dashboard's path end to end: the backend saves the sign-off, then the service pays."""
    settings = make_settings(db_path, data_dir)
    with TestClient(create_service(settings, inline=True)) as service:
        with TestClient(create_app(settings, http=service)) as backend:
            backend.post("/runs/selected", json={"claim_ids": ["CLM-1003"]})
            assert backend.get("/claims/CLM-1003").json()["status"] == "awaiting_approval"
            answer = backend.post("/payments/PAY-CLM-1003/approve", json={"handler_id": "HND-01"})
            assert answer.status_code == 200 and answer.json()["status"] == "signed_off"
            detail = backend.get("/claims/CLM-1003").json()
    assert detail["status"] == "decided"
    assert detail["payment"]["status"] == "paid"
    assert detail["payment"]["approved_by"] == "HND-01"
    assert detail["payment"]["approved_by_name"] == "Dana Whitfield"
    assert detail["payment"]["approved_at"] and detail["payment"]["paid_at"]


class Down:
    def post(self, *args, **kwargs):
        raise httpx.ConnectError("the service is not running")


def test_sign_off_stands_when_the_service_is_down(db_path, data_dir):
    engine = hold(db_path, data_dir)
    with TestClient(create_app(make_settings(db_path, data_dir), http=Down())) as backend:
        answer = backend.post("/payments/PAY-CLM-1003/approve", json={"handler_id": "HND-01"})
        assert answer.status_code == 200
        assert backend.get("/claims/CLM-1003").json()["status"] == "in_progress"
    payment = get_payment(engine, "CLM-1003")
    assert (payment.status, payment.approved_by) == ("signed_off", "HND-01")


def test_sign_off_reaches_the_service_after_it_is_saved(db_path, data_dir):
    engine = hold(db_path, data_dir)
    seen = []

    class Spy:
        def post(self, url, **kwargs):
            seen.append((url, get_payment(engine, "CLM-1003").status))
            return httpx.Response(202)

    with TestClient(create_app(make_settings(db_path, data_dir), http=Spy())) as backend:
        backend.post("/payments/PAY-CLM-1003/approve", json={"handler_id": "HND-01"})
    assert seen == [("http://127.0.0.1:8002/claims/CLM-1003/resume", "signed_off")]


def test_a_repeated_sign_off_does_not_call_the_service_again(db_path, data_dir):
    hold(db_path, data_dir)
    seen = []

    class Spy:
        def post(self, url, **kwargs):
            seen.append(url)
            return httpx.Response(202)

    with TestClient(create_app(make_settings(db_path, data_dir), http=Spy())) as backend:
        backend.post("/payments/PAY-CLM-1003/approve", json={"handler_id": "HND-01"})
        backend.post("/payments/PAY-CLM-1003/approve", json={"handler_id": "HND-02"})
    assert len(seen) == 2  # a retry is harmless and helps if the first call was lost


def test_run_with_no_agent_is_503(db_path, data_dir):
    with TestClient(create_app(make_settings(db_path, data_dir), http=Down())) as backend:
        answer = backend.post("/runs", json={"limit": 1})
        assert answer.status_code == 503 and "not running" in answer.json()["detail"]
        assert backend.get("/runs/latest").json()["run"] is None


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


def test_a_resume_that_loses_its_connection_tries_once_more(db_path, data_dir, monkeypatch):
    import agent.service as service_module

    engine = hold(db_path, data_dir)
    sign_off(engine, Reference(data_dir), "PAY-CLM-1003", "HND-01")
    real = service_module.carry_on
    calls = []

    def flaky(env, claim_id):
        calls.append(claim_id)
        if len(calls) == 1:
            return f"{claim_id}: an error after the decision (OperationalError: the connection was closed)."
        return real(env, claim_id)

    monkeypatch.setattr(service_module, "carry_on", flaky)
    with TestClient(create_service(make_settings(db_path, data_dir), inline=True)):
        pass
    assert calls == ["CLM-1003", "CLM-1003"]
    assert get_payment(engine, "CLM-1003").status == "paid"
