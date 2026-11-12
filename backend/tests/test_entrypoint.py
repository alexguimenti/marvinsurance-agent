"""The agent as an AgentCore app: one entrypoint that takes a payload naming an operation, answers at once, and works on a thread."""
import subprocess
import sys
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient

import agent.entrypoint as entrypoint
from agent.operations_in import OPERATIONS, Payload  # noqa: F401
from agent.worker import work_claim
from app import runs
from app.api import create_app
from app.claims import get_claim
from app.config import Settings
from app.db import init_schema, make_engine
from app.payments import get_payment
from app.reference import Reference
from app.seed import seed_claims
from tests.fakes import MINT, TEST_KEY, RawDb, agent_env, api_client, approve, create_service, service_client, stub

BEARER = {"Authorization": f"Bearer {TEST_KEY}"}
WRONG = {"Authorization": "Bearer not-the-key"}


def make_settings(db_path, data_dir):
    return Settings(_env_file=None, agent_provider="scripted", db_path=db_path, data_dir=data_dir)


@pytest.fixture
def service(db_path, data_dir):
    with service_client(create_service(make_settings(db_path, data_dir), inline=True)) as c:
        yield c


def engine_for(db_path):
    return make_engine(Settings(_env_file=None, db_path=db_path), max_connections=2)


def ping(service) -> str:
    return service.get("/ping").json()["status"]


# --- importing, getting ready, and the key -----------------------------------------------------------------------

def test_importing_the_agent_opens_nothing():
    """A host that does not exist: importing the agent must not try to reach it, or read the key."""
    code = "import agent.entrypoint"
    env = {"PATH": __import__("os").environ["PATH"], "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", ""),
           "DATABASE_URL": "postgresql://u:p@192.0.2.1:6543/postgres", "UNDERCOVER_BACKEND": "http://192.0.2.1:9"}
    started = time.monotonic()
    done = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    assert time.monotonic() - started < 30


def test_health_answers_healthy_once_ready_without_the_key(db_path, data_dir):
    with service_client(create_service(make_settings(db_path, data_dir), inline=True), keyed=False) as keyless:
        answer = keyless.post("/invocations", json={"operation": "health"})
    assert answer.status_code == 200 and answer.json() == {"healthy": True}


def test_off_runtime_a_request_without_the_key_is_refused_but_health_is_not(db_path, data_dir):
    work = {"operation": "start_dispatch", "workers": 1}
    with service_client(create_service(make_settings(db_path, data_dir), inline=True), keyed=False) as keyless:
        assert keyless.post("/invocations", json=work).status_code == 401
        assert keyless.post("/invocations", json=work, headers=WRONG).status_code == 401
        assert keyless.post("/invocations", json={"operation": "health"}).status_code == 200
        assert keyless.post("/invocations", json=work, headers=BEARER).json()["accepted"] is True


def test_on_runtime_no_key_is_asked_for(db_path, data_dir, monkeypatch):
    monkeypatch.setenv("AGENTCORE_CREDENTIAL_OPENROUTER_API_KEY_NAME", "provider-of-openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "placeholder")  # the fetch sets it: this makes the test put it back
    monkeypatch.setattr("bedrock_agentcore.identity.auth.requires_api_key", fake_identity([]))
    with service_client(create_service(make_settings(db_path, data_dir), inline=True), keyed=False) as service:
        answer = service.post("/invocations", json={"operation": "start_dispatch", "workers": 1})
    assert answer.status_code == 200 and answer.json()["accepted"] is True


def fake_identity(calls: list):
    """Identity with no AWS: each credential's provider answers a value of its own."""

    def requires_api_key(*, provider_name, into="api_key"):
        def decorate(function):
            def fetch(*args, **kwargs):
                calls.append(provider_name)
                return function(*args, **{into: f"secret-of-{provider_name}"}, **kwargs)

            return fetch

        return decorate

    return requires_api_key


def test_on_runtime_the_first_request_fetches_each_credential_into_its_setting_once(db_path, data_dir, monkeypatch):
    import os

    calls = []
    monkeypatch.setenv("AGENTCORE_CREDENTIAL_OPENROUTER_API_KEY_NAME", "provider-of-openrouter")
    monkeypatch.setenv("AGENTCORE_CREDENTIAL_LANGFUSE_SECRET_KEY_NAME", "provider-of-langfuse")
    monkeypatch.setenv("OPENROUTER_API_KEY", "placeholder")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "placeholder")
    monkeypatch.setattr("bedrock_agentcore.identity.auth.requires_api_key", fake_identity(calls))
    monkeypatch.setattr(entrypoint, "open_env", lambda settings: __import__("contextlib").nullcontext(object()))  # no database here
    monkeypatch.setattr(entrypoint.api, "stop_running", lambda: [])
    monkeypatch.setattr(entrypoint.rounds, "stop_working_rounds", lambda: [])
    with service_client(create_service(make_settings(db_path, data_dir), inline=True), keyed=False) as service:
        for _ in range(2):
            assert service.post("/invocations", json={"operation": "health"}).json() == {"healthy": True}
    assert sorted(calls) == ["provider-of-langfuse", "provider-of-openrouter"]  # once each, not once a request
    assert os.environ["OPENROUTER_API_KEY"] == "secret-of-provider-of-openrouter"


def test_getting_ready_that_fails_answers_the_error_and_the_next_request_tries_again(db_path, data_dir, monkeypatch):
    real, tries = entrypoint.wait_for_backend, []

    def flaky(*args, **kwargs):
        tries.append(1)
        if len(tries) == 1:
            raise RuntimeError("the backend did not answer")
        return real(*args, **kwargs)

    monkeypatch.setattr(entrypoint, "wait_for_backend", flaky)
    with service_client(create_service(make_settings(db_path, data_dir), inline=True), raise_server_exceptions=False) as service:
        failed = service.post("/invocations", json={"operation": "health"})
        assert failed.status_code == 500 and "the backend did not answer" in failed.text
        assert service.post("/invocations", json={"operation": "health"}).json() == {"healthy": True}


def test_ready_without_the_key_answers_the_error(db_path, data_dir, monkeypatch):
    monkeypatch.delenv("UNDERCOVER_API_KEY")
    with TestClient(create_service(make_settings(db_path, data_dir), inline=True), raise_server_exceptions=False) as client:
        answer = client.post("/invocations", json={"operation": "health"})
    assert answer.status_code == 500 and "UNDERCOVER_API_KEY" in answer.text


def test_getting_ready_marks_a_running_run_stopped(db_path, data_dir):
    engine = engine_for(db_path)
    init_schema(engine)
    RawDb(engine).execute("INSERT INTO runs (kind, status, workers, model, reasoning_effort) VALUES ('dispatch', 'running', 3, 'm', 'none')")
    with service_client(create_service(make_settings(db_path, data_dir), inline=True)) as service:
        service.post("/invocations", json={"operation": "health"})
        assert runs.get_run(engine, 1)["status"] == "stopped"
        assert service.invoke({"operation": "start_dispatch", "workers": 3}).json()["accepted"] is True


def test_getting_ready_leaves_stopped_claims_alone(db_path, data_dir):
    engine = engine_for(db_path)
    init_schema(engine)
    seed_claims(engine, Reference(data_dir))
    RawDb(engine).execute("UPDATE claims SET status='in_progress', thread_id='CLM-1001-1' WHERE claim_id='CLM-1001'")
    with service_client(create_service(make_settings(db_path, data_dir), inline=True)) as service:
        service.post("/invocations", json={"operation": "health"})
    claim = get_claim(engine, "CLM-1001")
    assert claim["status"] == "in_progress" and claim["decision"] is None


# --- the operations -----------------------------------------------------------------------------------------------

def test_a_dispatch_is_accepted_and_works_the_queue(service, db_path):
    assert service.invoke({"operation": "start_dispatch", "workers": 4}).json()["accepted"] is True
    engine = engine_for(db_path)
    row = runs.latest_run(engine, "dispatch")
    assert (row["status"], row["workers"], row["model"], row["reasoning_effort"]) == ("finished", 4, "scripted", "default")
    assert RawDb(engine).execute("SELECT COUNT(*) FROM claims WHERE status = 'waiting'").fetchone()[0] == 0


def test_a_dispatch_while_one_runs_is_refused_with_the_reason(service, db_path):
    RawDb(engine_for(db_path)).execute("INSERT INTO runs (kind, status, workers, model, reasoning_effort) VALUES ('dispatch', 'running', 3, 'm', 'none')")
    # ready marks a leftover running run stopped, so this one goes in after the agent is ready
    service.post("/invocations", json={"operation": "health"})
    RawDb(engine_for(db_path)).execute("INSERT INTO runs (kind, status, workers, model, reasoning_effort) VALUES ('dispatch', 'running', 3, 'm', 'none')")
    answer = service.invoke({"operation": "start_dispatch", "workers": 3})
    assert answer.status_code == 200 and "already running" in answer.json()["refused"]


def test_an_evaluation_is_accepted_and_refused_during_a_dispatch(service, db_path):
    body = {"operation": "start_evaluation", "eval_set": "held_out", "tiny": True, "workers": 5}
    assert service.invoke(body).json()["accepted"] is True
    raw = RawDb(engine_for(db_path))
    assert tuple(raw.execute("SELECT kind, status, eval_set FROM runs").fetchone()) == ("evaluation", "finished", "held_out")
    raw.execute("INSERT INTO runs (kind, status, workers, model, reasoning_effort) VALUES ('dispatch', 'running', 3, 'm', 'none')")
    assert "already running" in service.invoke(body).json()["refused"]


def test_a_round_is_accepted_and_a_second_is_refused(service, db_path):
    assert service.invoke({"operation": "start_improver_round", "handler_id": "HND-01"}).json()["accepted"] is True
    assert "already open" in service.invoke({"operation": "start_improver_round", "handler_id": "HND-01"}).json()["refused"]


def test_a_decision_on_a_proposal_that_does_not_exist_is_refused(service):
    answer = service.invoke({"operation": "decide_round_proposal", "round_id": 1, "proposal_id": 99, "approve": True, "reason": "", "handler_id": "HND-01"})
    assert "refused" in answer.json()


def test_resume_claim_is_refused_for_a_claim_that_is_not_paused(service):
    answer = service.invoke({"operation": "resume_claim", "claim_id": "CLM-1001"})
    assert "not waiting for a handler's answer" in answer.json()["refused"]


@pytest.mark.parametrize("payload,says", [
    ({"operation": "start_dispatch"}, "start_dispatch: workers"),
    ({"operation": "start_dispatch", "workers": 0}, "start_dispatch: workers"),
    ({"operation": "start_dispatch", "workers": 26}, "start_dispatch: workers"),
    ({"operation": "start_evaluation", "eval_set": "harder", "tiny": True, "workers": 3}, "start_evaluation: eval_set"),
    ({"operation": "resume_claim"}, "resume_claim: claim_id"),
    ({"operation": "dance"}, "unknown operation 'dance'"),
    ({}, "unknown operation None"),
])
def test_a_payload_with_an_input_missing_or_wrong_is_a_400_that_says_what(service, payload, says):
    answer = service.invoke(payload)
    assert answer.status_code == 400 and says in answer.json()["error"]


def test_no_operation_has_an_optional_input():
    from agent import operations_in

    for model in (operations_in.Health, operations_in.StartDispatch, operations_in.StartEvaluation,
                  operations_in.StartImproverRound, operations_in.DecideRoundProposal, operations_in.ResumeClaim):
        assert all(field.is_required() for field in model.model_fields.values()), model.__name__
    assert set(OPERATIONS) == {"health", "start_dispatch", "start_evaluation", "start_improver_round", "decide_round_proposal", "resume_claim"}


# --- work on a thread, and the ping ------------------------------------------------------------------------------

def test_the_ping_is_busy_while_work_is_going_and_healthy_once_it_has_finished_or_failed(db_path, data_dir, monkeypatch):
    release, started = threading.Event(), threading.Event()
    outcomes = []

    def gated(env, run_id, workers):
        started.set()
        release.wait(30)
        entrypoint.api.finish_run(run_id)  # the dispatcher ends its run: so the next start is not refused
        if outcomes:
            raise RuntimeError("the work failed")

    monkeypatch.setattr(entrypoint, "dispatch", gated)
    with service_client(create_service(make_settings(db_path, data_dir))) as service:  # real threads
        assert ping(service) == "Healthy"
        assert service.invoke({"operation": "start_dispatch", "workers": 3}).json()["accepted"] is True
        assert started.wait(10) and ping(service) == "HealthyBusy"
        release.set()
        deadline = time.monotonic() + 10
        while ping(service) != "Healthy" and time.monotonic() < deadline:
            time.sleep(0.1)
        assert ping(service) == "Healthy"
        # and the same when the work fails
        release.clear(), started.clear()
        outcomes.append(1)
        assert service.invoke({"operation": "start_dispatch", "workers": 3}).json()["accepted"] is True
        assert started.wait(10) and ping(service) == "HealthyBusy"
        release.set()
        deadline = time.monotonic() + 10
        while ping(service) != "Healthy" and time.monotonic() < deadline:
            time.sleep(0.1)
        assert ping(service) == "Healthy"


# --- the dashboard's path through the backend, and the app's errors ----------------------------------------------

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
    assert detail["payment"]["status"] == "paid" and detail["payment"]["approved_by"] == "HND-01"
    assert detail["payment"]["approved_by_name"] == "Dana Whitfield"
    assert detail["payment"]["approved_at"] and detail["payment"]["paid_at"]


class Down:
    def post(self, *args, **kwargs):
        raise httpx.ConnectError("the agent is not running")


def test_a_sign_off_does_not_need_the_agent(db_path, data_dir):
    """The app on Lambda may not reach the agent: the sign-off pays with the agent down."""
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


def test_every_operation_of_the_app_follows_the_api_rules(db_path, data_dir):
    schema = create_app(make_settings(db_path, data_dir), http=Down()).openapi()
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


def test_a_database_error_before_a_start_does_not_lock_the_agent(db_path, data_dir, monkeypatch):
    real, calls = entrypoint.api.running_run, []

    def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("the pooler closed the connection")
        return real()

    monkeypatch.setattr(entrypoint.api, "running_run", flaky)
    with service_client(create_service(make_settings(db_path, data_dir), inline=True), raise_server_exceptions=False) as service:
        assert service.invoke({"operation": "start_dispatch", "workers": 3}).status_code == 500
        assert service.invoke({"operation": "start_dispatch", "workers": 3}).json()["accepted"] is True


def test_a_start_that_fails_to_create_its_run_says_so(db_path, data_dir, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("could not write the run")

    real = entrypoint.api.start_dispatch
    monkeypatch.setattr(entrypoint.api, "start_dispatch", broken)
    with service_client(create_service(make_settings(db_path, data_dir), inline=True), raise_server_exceptions=False) as service:
        assert service.invoke({"operation": "start_dispatch", "workers": 3}).status_code == 500
        monkeypatch.setattr(entrypoint.api, "start_dispatch", real)
        assert service.invoke({"operation": "start_dispatch", "workers": 3}).json()["accepted"] is True


def test_an_evaluation_half_made_ends_stopped_and_frees_the_next_start(db_path, data_dir, monkeypatch):
    import app.evaluations as evaluations_module

    def broken(*args, **kwargs):
        raise RuntimeError("could not list the copies")

    real = evaluations_module.runs.add_run_claims
    monkeypatch.setattr(evaluations_module.runs, "add_run_claims", broken)
    with service_client(create_service(make_settings(db_path, data_dir), inline=True), raise_server_exceptions=False) as service:
        body = {"operation": "start_evaluation", "eval_set": "evaluation", "tiny": True, "workers": 3}
        assert service.invoke(body).status_code == 500
        assert runs.get_run(engine_for(db_path), 1)["status"] == "stopped"
        monkeypatch.setattr(evaluations_module.runs, "add_run_claims", real)
        assert service.invoke({"operation": "start_dispatch", "workers": 3}).json()["accepted"] is True


def test_an_agent_error_with_a_plain_text_body_reaches_the_dashboard(db_path, data_dir):
    class Broken:
        def post(self, url, json=None, timeout=None, headers=None):
            return httpx.Response(500, text="Internal Server Error")

    with api_client(create_app(make_settings(db_path, data_dir), http=Broken(), jwks=MINT), headers=MINT.header()) as backend:
        answer = backend.post("/dispatches", json={"workers": 3})
        assert answer.status_code == 500 and "Internal Server Error" in answer.json()["detail"]
