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
from tests.fakes import MINT, RawDb


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
    with TestClient(create_service(make_settings(db_path, data_dir), inline=True)) as service:
        assert runs.get_run(engine, 1)["status"] == "stopped"
        assert service.post("/dispatches", json={"workers": 3}).status_code == 202


def test_a_sign_off_during_a_dispatch_is_paid_and_not_taken_twice(db_path, data_dir):
    """The resume holds CLM-1003 while the dispatch runs: the dispatch leaves it to the resume, which pays it."""
    engine = hold(db_path, data_dir)
    sign_off(engine, Reference(data_dir), "PAY-CLM-1003", "HND-01")
    with open_env(make_settings(db_path, data_dir)) as env:
        with env.working("CLM-1003"):
            run_id = runs.start_dispatch(env.engine, 3, env.model_id, None)
            dispatch(env, run_id, 3)
        assert "CLM-1003" not in runs.run_claim_ids(env.engine, run_id)
        carry_on(env, "CLM-1003")
    assert get_payment(engine, "CLM-1003").status == "paid"
    assert RawDb(engine).execute("SELECT COUNT(*) FROM decisions WHERE claim_id='CLM-1003'").fetchone()[0] == 1


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
        with TestClient(create_app(settings, http=service, jwks=MINT), headers=MINT.header()) as backend:
            backend.post("/dispatches", json={"workers": 3})
            assert backend.get("/claims/CLM-1003").json()["status"] == "awaiting_approval"
            answer = backend.post("/payments/PAY-CLM-1003/approve")
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
    with TestClient(create_app(make_settings(db_path, data_dir), http=Down(), jwks=MINT), headers=MINT.header()) as backend:
        answer = backend.post("/payments/PAY-CLM-1003/approve")
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

    with TestClient(create_app(make_settings(db_path, data_dir), http=Spy(), jwks=MINT), headers=MINT.header()) as backend:
        backend.post("/payments/PAY-CLM-1003/approve")
    assert seen == [("http://127.0.0.1:8002/claims/CLM-1003/resume", "signed_off")]


def test_a_repeated_sign_off_does_not_call_the_service_again(db_path, data_dir):
    hold(db_path, data_dir)
    seen = []

    class Spy:
        def post(self, url, **kwargs):
            seen.append(url)
            return httpx.Response(202)

    with TestClient(create_app(make_settings(db_path, data_dir), http=Spy(), jwks=MINT), headers=MINT.header()) as backend:
        backend.post("/payments/PAY-CLM-1003/approve")
        backend.post("/payments/PAY-CLM-1003/approve")
    assert len(seen) == 2  # a retry is harmless and helps if the first call was lost


def test_a_dispatch_with_no_agent_is_503(db_path, data_dir):
    with TestClient(create_app(make_settings(db_path, data_dir), http=Down(), jwks=MINT), headers=MINT.header()) as backend:
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


def test_a_resume_that_raises_tries_once_more(db_path, data_dir, monkeypatch):
    import agent.service as service_module

    engine = hold(db_path, data_dir)
    sign_off(engine, Reference(data_dir), "PAY-CLM-1003", "HND-01")
    real = service_module.carry_on
    calls = []

    def flaky(env, claim_id):
        calls.append(claim_id)
        if len(calls) == 1:
            raise RuntimeError("the connection was closed")
        return real(env, claim_id)

    monkeypatch.setattr(service_module, "carry_on", flaky)
    with TestClient(create_service(make_settings(db_path, data_dir), inline=True)):
        pass
    assert get_payment(engine, "CLM-1003").status == "paid"


def test_the_sweep_pays_a_sign_off_whose_call_was_lost(db_path, data_dir):
    from agent.service import sweep_signed_off

    settings = make_settings(db_path, data_dir)
    engine = hold(db_path, data_dir)
    with open_env(settings) as env:
        sweep_signed_off(env)  # nothing signed off yet: nothing happens
        assert get_payment(engine, "CLM-1003").status == "held"
        sign_off(engine, Reference(data_dir), "PAY-CLM-1003", "HND-01")
        sweep_signed_off(env)
    assert get_payment(engine, "CLM-1003").status == "paid"


def test_the_sweep_survives_a_database_error(db_path, data_dir, monkeypatch):
    import agent.service as service_module

    def down(engine):
        raise RuntimeError("the database went away")

    monkeypatch.setattr(service_module, "signed_off_claims", down)
    with open_env(make_settings(db_path, data_dir)) as env:
        service_module.sweep_signed_off(env)  # logged, and the next sweep tries again


# --- the final review's findings ---------------------------------------------------------------------

def test_a_database_error_before_a_start_does_not_lock_the_service(db_path, data_dir, monkeypatch):
    import agent.service as service_module

    real = service_module.runs.running_run
    calls = []

    def flaky(engine):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("the pooler closed the connection")
        return real(engine)

    monkeypatch.setattr(service_module.runs, "running_run", flaky)
    with TestClient(create_service(make_settings(db_path, data_dir), inline=True), raise_server_exceptions=False) as service:
        assert service.post("/dispatches", json={"workers": 3}).status_code == 500
        assert service.post("/dispatches", json={"workers": 3}).status_code == 202


def test_a_start_that_fails_to_create_its_run_says_so(db_path, data_dir, monkeypatch):
    import agent.service as service_module

    def broken(*args, **kwargs):
        raise RuntimeError("could not write the run")

    monkeypatch.setattr(service_module.runs, "start_dispatch", broken)
    with TestClient(create_service(make_settings(db_path, data_dir), inline=True), raise_server_exceptions=False) as service:
        assert service.post("/dispatches", json={"workers": 3}).status_code == 500
        monkeypatch.undo()
        assert service.post("/dispatches", json={"workers": 3}).status_code == 202


def test_an_evaluation_half_made_ends_stopped_and_frees_the_next_start(db_path, data_dir, monkeypatch):
    import app.evaluations as evaluations_module

    def broken(*args, **kwargs):
        raise RuntimeError("could not list the copies")

    monkeypatch.setattr(evaluations_module.runs, "add_run_claims", broken)
    with TestClient(create_service(make_settings(db_path, data_dir), inline=True), raise_server_exceptions=False) as service:
        body = {"eval_set": "evaluation", "tiny": True, "workers": 3}
        assert service.post("/evaluations", json=body).status_code == 500
        assert runs.get_run(engine_for(db_path), 1)["status"] == "stopped"
        monkeypatch.undo()
        assert service.post("/dispatches", json={"workers": 3}).status_code == 202


def test_a_service_error_with_a_plain_text_body_reaches_the_dashboard(db_path, data_dir):
    class Broken:
        def post(self, url, json=None, timeout=None):
            return httpx.Response(500, text="Internal Server Error")

    with TestClient(create_app(make_settings(db_path, data_dir), http=Broken(), jwks=MINT), headers=MINT.header()) as backend:
        answer = backend.post("/dispatches", json={"workers": 3})
        assert answer.status_code == 500 and "Internal Server Error" in answer.json()["detail"]


def test_a_resume_waits_for_a_claim_still_being_worked(db_path, data_dir):
    """A sign-off that lands while the worker is still finishing the pause: the resume waits for it, not the sweep."""
    import threading
    import time

    from agent.service import resume

    engine = hold(db_path, data_dir)
    sign_off(engine, Reference(data_dir), "PAY-CLM-1003", "HND-01")
    with open_env(make_settings(db_path, data_dir)) as env:
        held = threading.Event()

        def busy():
            with env.working("CLM-1003"):
                held.set()
                time.sleep(1.5)

        worker = threading.Thread(target=busy)
        worker.start()
        held.wait()
        resume(env, "CLM-1003")
        worker.join()
    assert get_payment(engine, "CLM-1003").status == "paid"
