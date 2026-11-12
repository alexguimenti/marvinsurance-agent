"""The app's two routes to the agent: HTTP with the key on a laptop, a Runtime session on AWS. Stand-ins only: no AWS is called."""
import io
import json

import httpx
import pytest
from botocore.exceptions import ClientError, ConnectTimeoutError, EndpointConnectionError, IncompleteReadError, ReadTimeoutError

from app import agent_client
from app.agent_client import AgentMaybe, AgentNotReached, AgentRefused, send
from app.api import create_app
from app.config import Settings
from tests.fakes import MINT, TEST_KEY, api_client

ARN = "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/agent-abc"
PAYLOAD = {"operation": "start_dispatch", "workers": 3}


def settings(**more):
    return Settings(_env_file=None, agent_provider="scripted", **more)


class Http:
    """The httpx the app posts with: answers `answer`, or raises `error`, and writes down what it was sent."""

    def __init__(self, answer=None, error=None):
        self.answer, self.error, self.sent = answer, error, []

    def post(self, url, json=None, timeout=None, headers=None):
        self.sent.append({"url": url, "json": json, "timeout": timeout, "headers": headers})
        if self.error:
            raise self.error
        return self.answer


def ok(body=None, status=200):
    return httpx.Response(status, json={"accepted": True} if body is None else body, request=httpx.Request("POST", "http://agent"))


class Runtime:
    """boto3's bedrock-agentcore client: answers a stream, or raises."""

    def __init__(self, body=b'{"accepted": true}', error=None, read_error=None):
        self.body, self.error, self.read_error, self.sent = body, error, read_error, []

    def invoke_agent_runtime(self, **kwargs):
        self.sent.append(kwargs)
        if self.error:
            raise self.error
        owner = self

        class Stream(io.BytesIO):
            def read(self, *args):
                if owner.read_error:
                    raise owner.read_error
                return super().read(*args)

        return {"response": Stream(self.body)}


def error_response(code="AccessDeniedException"):
    return ClientError({"Error": {"Code": code, "Message": "no"}}, "InvokeAgentRuntime")


# --- the laptop route ------------------------------------------------------------------------------------------

def test_the_laptop_route_sends_the_key_and_the_payload_to_invocations_in_ten_seconds():
    http = Http(ok())
    assert send(settings(agent_url="http://127.0.0.1:8080"), PAYLOAD, http)["accepted"] is True
    sent = http.sent[0]
    assert sent["url"] == "http://127.0.0.1:8080/invocations" and sent["json"] == PAYLOAD and sent["timeout"] == 10
    assert sent["headers"] == {"Authorization": f"Bearer {TEST_KEY}"}


def test_the_laptop_routes_default_address_is_8080(monkeypatch):
    monkeypatch.delenv("UNDERCOVER_AGENT", raising=False)
    assert settings().agent_url == "http://127.0.0.1:8080"


def test_a_refusal_on_the_laptop_route_is_passed_on():
    with pytest.raises(AgentRefused) as raised:
        send(settings(), PAYLOAD, Http(ok({"refused": "a dispatch is already running"})))
    assert raised.value.reason == "a dispatch is already running"


def test_a_failed_connection_on_the_laptop_route_took_nothing():
    for error in (httpx.ConnectError("refused"), httpx.ConnectTimeout("slow")):
        with pytest.raises(AgentNotReached) as raised:
            send(settings(), PAYLOAD, Http(error=error))
        assert raised.value.status is None


def test_an_error_answer_on_the_laptop_route_took_nothing_and_says_what_the_agent_said():
    with pytest.raises(AgentNotReached) as raised:
        send(settings(), PAYLOAD, Http(ok({"error": "workers: Input should be at most 25"}, status=400)))
    assert raised.value.status == 400 and "at most 25" in str(raised.value)
    with pytest.raises(AgentNotReached) as raised:
        send(settings(), PAYLOAD, Http(httpx.Response(500, text="Internal Server Error", request=httpx.Request("POST", "http://agent"))))
    assert raised.value.status == 500 and "Internal Server Error" in str(raised.value)


def test_a_timeout_or_a_cut_off_answer_on_the_laptop_route_may_have_started_the_work():
    for error in (httpx.ReadTimeout("slow"), httpx.RemoteProtocolError("cut off")):
        with pytest.raises(AgentMaybe):
            send(settings(), PAYLOAD, Http(error=error))
    with pytest.raises(AgentMaybe):
        send(settings(), PAYLOAD, Http(httpx.Response(200, text="not json", request=httpx.Request("POST", "http://agent"))))


# --- the Runtime route -----------------------------------------------------------------------------------------

def runtime_settings(monkeypatch):
    monkeypatch.setenv("UNDERCOVER_AGENT_RUNTIME", ARN)
    return settings()


def test_the_runtime_route_sends_the_payload_with_a_new_session_id_of_33_characters_or_more_and_no_key(monkeypatch):
    client = Runtime()
    config = runtime_settings(monkeypatch)
    assert send(config, PAYLOAD, client=client) == {"accepted": True}
    send(config, PAYLOAD, client=client)
    first, second = client.sent
    assert first["agentRuntimeArn"] == ARN and json.loads(first["payload"]) == PAYLOAD
    assert len(first["runtimeSessionId"]) >= 33 and first["runtimeSessionId"] != second["runtimeSessionId"]
    assert "Authorization" not in first and not any("key" in name.lower() for name in first)


def test_the_runtime_route_makes_one_attempt_and_waits_5_seconds_to_connect_and_20_to_read():
    config = agent_client.RUNTIME_CONFIG
    assert config.retries["total_max_attempts"] == 1 and config.connect_timeout == 5 and config.read_timeout == 20


def test_a_refusal_on_the_runtime_route_is_passed_on(monkeypatch):
    with pytest.raises(AgentRefused, match="already running"):
        send(runtime_settings(monkeypatch), PAYLOAD, client=Runtime(b'{"refused": "a dispatch is already running"}'))


def test_a_failed_connection_or_an_error_answer_on_the_runtime_route_took_nothing(monkeypatch):
    config = runtime_settings(monkeypatch)
    for error in (EndpointConnectionError(endpoint_url="https://x"), ConnectTimeoutError(endpoint_url="https://x"), error_response()):
        with pytest.raises(AgentNotReached):
            send(config, PAYLOAD, client=Runtime(error=error))


def test_a_timeout_or_a_cut_off_answer_on_the_runtime_route_may_have_started_the_work(monkeypatch):
    config = runtime_settings(monkeypatch)
    with pytest.raises(AgentMaybe):
        send(config, PAYLOAD, client=Runtime(error=ReadTimeoutError(endpoint_url="https://x")))
    with pytest.raises(AgentMaybe):
        send(config, PAYLOAD, client=Runtime(read_error=IncompleteReadError(actual_bytes=1, expected_bytes=9)))


def test_with_a_runtime_set_the_laptop_is_not_called(monkeypatch):
    http = Http(ok())
    with pytest.raises(AgentMaybe):  # no client stand-in and no AWS here: boto3 is not reached through httpx
        send(runtime_settings(monkeypatch), PAYLOAD, http, client=Runtime(error=RuntimeError("some other failure")))
    assert http.sent == []


# --- what the handler is told ----------------------------------------------------------------------------------

@pytest.fixture
def backend(db_path, data_dir):
    def make(http):
        return api_client(create_app(settings(db_path=db_path, data_dir=data_dir), http=http, jwks=MINT), headers=MINT.header())

    return make


def test_a_dispatch_the_agent_refused_is_a_409_with_its_reason(backend):
    with backend(Http(ok({"refused": "a dispatch is already running"}))) as client:
        answer = client.post("/dispatches", json={"workers": 3})
    assert answer.status_code == 409 and answer.json()["detail"] == "a dispatch is already running"


def test_a_dispatch_the_agent_may_have_started_says_to_reload_the_page(backend):
    with backend(Http(error=httpx.ReadTimeout("slow"))) as client:
        answer = client.post("/dispatches", json={"workers": 3})
    assert answer.status_code == 504 and "Reload the page" in answer.json()["detail"]
    with backend(Http(error=httpx.ConnectError("refused"))) as client:
        assert client.post("/dispatches", json={"workers": 3}).status_code == 503
