"""The Copilot's AgentCore app: one entrypoint, each payload naming its operation, nothing at import, ready on the first request."""
import contextlib
import json
import sys

import httpx
import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from agent import connection
from agent.copilot import entrypoint
from agent.copilot.entrypoint import build_copilot_app
from agent.copilot.payloads import BadPayload, parse
from agent.settings import AgentSettings
from tests.fakes import script
from tests.test_copilot_tools import App, DANA

THREAD = "conversation-" + "e" * 36
TOKEN = "tok-dana-secret"


def settings_for(tmp_path):
    return lambda: AgentSettings(_env_file=None, db_path=str(tmp_path / "copilot.db"), agent_provider="scripted")


def turn(message="Work the queue"):
    return {"operation": "turn", "conversation_id": THREAD, "turn_id": 7, "message": message, "token": TOKEN}


def test_health_answers_healthy(tmp_path):
    with TestClient(build_copilot_app(settings_for(tmp_path), inline=True)) as client:
        assert client.post("/invocations", json={"operation": "health"}).json() == {"healthy": True}
        assert client.get("/ping").json()["status"] == "Healthy"


def test_a_turn_is_accepted_at_once_and_worked_as_an_async_task(tmp_path):
    app = App({("GET", "/api/me"): (200, DANA), ("GET", "/api/claims"): (200, []), ("POST", "/api/dispatches"): (202, {"status": "started", "run_id": 3}),
               ("GET", "/api/claims/CLM-0000"): (404, {"detail": "unknown claim CLM-0000"})})
    copilot = build_copilot_app(settings_for(tmp_path), inline=True)
    with TestClient(copilot) as client, connection.use(httpx.Client(transport=httpx.MockTransport(app))):
        answer = client.post("/invocations", json=turn())
    assert answer.status_code == 200 and answer.json() == {"accepted": True}
    ended = app.sent("POST", "/api/copilot/turns/7/end")
    assert len(ended) == 1 and ended[0]["answer"].startswith("Here is where things stand") and ended[0]["model_calls"] == 4
    assert all(r.headers["authorization"] == f"Bearer {TOKEN}" and "x-api-key" not in r.headers for r in app.requests)


def test_the_app_asks_for_no_key(tmp_path):
    app = App({("GET", "/api/me"): (401, {"detail": "sign in again"})})
    with TestClient(build_copilot_app(settings_for(tmp_path), inline=True)) as client, connection.use(httpx.Client(transport=httpx.MockTransport(app))):
        assert client.post("/invocations", json=turn(), headers={}).status_code == 200  # no Authorization header: the payload carries the sign-in
    assert [r.url.path for r in app.requests] == ["/api/me"]  # and a refused sign-in ends it there


def test_the_async_task_is_registered_while_a_turn_works(tmp_path, monkeypatch):
    seen = []
    copilot = build_copilot_app(settings_for(tmp_path), inline=True)
    real = copilot.add_async_task
    monkeypatch.setattr(copilot, "add_async_task", lambda name, *a, **k: (seen.append(name), real(name, *a, **k))[1])
    with TestClient(copilot) as client, connection.use(httpx.Client(transport=httpx.MockTransport(App({("GET", "/api/me"): (200, DANA)})))):
        client.post("/invocations", json=turn())
    assert seen == ["turn 7"]


def fake_identity(calls):
    def requires_api_key(*, provider_name, into="api_key"):
        def decorate(function):
            def fetch(*args, **kwargs):
                calls.append(provider_name)
                return function(*args, **{into: f"secret-of-{provider_name}"}, **kwargs)

            return fetch

        return decorate

    return requires_api_key


def test_on_runtime_it_fetches_only_two_credentials(tmp_path, monkeypatch):
    calls = []
    for name in ("OPENROUTER_API_KEY", "DATABASE_URL", "UNDERCOVER_API_KEY"):
        monkeypatch.setenv(f"CREDENTIAL_{name}_NAME", f"provider-of-{name}")
        monkeypatch.setenv(name, "placeholder")
    monkeypatch.setattr("bedrock_agentcore.identity.auth.requires_api_key", fake_identity(calls))
    monkeypatch.setattr(entrypoint, "open_env", lambda settings: contextlib.nullcontext(object()))  # no database here
    with TestClient(build_copilot_app(settings_for(tmp_path), inline=True)) as client:
        for _ in range(2):
            assert client.post("/invocations", json={"operation": "health"}).json() == {"healthy": True}
    assert sorted(calls) == ["provider-of-DATABASE_URL", "provider-of-OPENROUTER_API_KEY"]  # once each, and never the agent's key
    import os

    assert os.environ["OPENROUTER_API_KEY"] == "secret-of-provider-of-OPENROUTER_API_KEY" and os.environ["UNDERCOVER_API_KEY"] == "placeholder"


def test_getting_ready_that_fails_answers_the_error_and_the_next_request_tries_again(tmp_path, monkeypatch):
    real, tries = entrypoint.open_env, []

    def flaky(*args, **kwargs):
        tries.append(1)
        if len(tries) == 1:
            raise RuntimeError("the checkpoint pool did not open")
        return real(*args, **kwargs)

    monkeypatch.setattr(entrypoint, "open_env", flaky)
    with TestClient(build_copilot_app(settings_for(tmp_path), inline=True), raise_server_exceptions=False) as client:
        assert client.post("/invocations", json={"operation": "health"}).status_code == 500
        assert client.post("/invocations", json={"operation": "health"}).json() == {"healthy": True}
    assert len(tries) == 2


@pytest.mark.parametrize("payload, why", [
    ({"operation": "turn", "conversation_id": "short", "turn_id": 1, "message": "hi", "token": TOKEN}, "conversation_id"),
    ({"operation": "turn", "conversation_id": THREAD, "turn_id": 1, "message": "hi"}, "token"),
    ({"operation": "turn", "conversation_id": THREAD, "turn_id": 1, "message": "", "token": TOKEN}, "message"),
    ({"operation": "answer", "conversation_id": THREAD, "turn_id": 1, "answer": "maybe", "token": TOKEN}, "answer"),
    ({"operation": "dance"}, "unknown operation"),
])
def test_a_bad_payload_is_400_and_never_echoes_the_token(tmp_path, payload, why):
    with TestClient(build_copilot_app(settings_for(tmp_path), inline=True)) as client:
        answer = client.post("/invocations", json=payload)
    assert answer.status_code == 400 and why in answer.text and TOKEN not in answer.text


def test_parse_gives_the_operation_it_names():
    assert parse({"operation": "health"}).operation == "health"
    assert parse({"operation": "answer", "conversation_id": THREAD, "turn_id": 2, "answer": "cancel", "token": "t"}).answer == "cancel"
    with pytest.raises(BadPayload):
        parse(["not", "an", "object"])


def test_importing_the_module_opens_nothing():
    """Every Runtime session starts from one snapshot taken after import: nothing may happen at import."""
    sys.modules.pop("agent.copilot.entrypoint", None)
    import importlib

    module = importlib.import_module("agent.copilot.entrypoint")
    assert module.app.state_holder == {}  # no environment, no pool, no credentials
    assert not any(t.name == "mcp-call" for t in __import__("threading").enumerate())
