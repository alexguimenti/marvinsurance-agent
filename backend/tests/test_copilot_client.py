"""The app's calls to the Copilot: over HTTP on a laptop, or to its runtime with the conversation as the session."""
import io
import json

import httpx
import pytest
from botocore.exceptions import ClientError

from app import copilot_client
from app.agent_client import AgentMaybe, AgentNotReached, AgentRefused
from app.config import COPILOT_MODEL, Settings

CONVERSATION = "conversation-" + "a" * 36
PAYLOAD = {"operation": "turn", "conversation_id": CONVERSATION, "turn_id": 1, "message": "hi", "token": "tok-1"}


def settings(**kwargs):
    return Settings(_env_file=None, **kwargs)


class Http:
    def __init__(self, answer=None, error=None):
        self.answer, self.error, self.posts = answer or {"accepted": True}, error, []

    def post(self, url, json=None, timeout=None, headers=None):
        self.posts.append((url, json, timeout, headers))
        if self.error:
            raise self.error
        return httpx.Response(200, json=self.answer, request=httpx.Request("POST", url))


def conflict():
    return ClientError({"Error": {"Code": "ConflictException", "Message": "the session is starting"}}, "InvokeAgentRuntime")


class Boto:
    """boto3's bedrock-agentcore client: each call takes the next outcome, an exception or an answer."""

    def __init__(self, *outcomes):
        self.outcomes, self.calls = list(outcomes), []

    def invoke_agent_runtime(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0) if self.outcomes else {"accepted": True}
        if isinstance(outcome, Exception):
            raise outcome
        return {"response": io.BytesIO(json.dumps(outcome).encode())}


def runtime_settings():
    s = settings()
    s.copilot_runtime_arn = "arn:aws:bedrock-agentcore:us-east-1:1:runtime/copilot-x"
    return s


def test_over_http_with_no_key():
    http = Http()
    assert copilot_client.send(settings(), PAYLOAD, CONVERSATION, http) == {"accepted": True}
    url, body, timeout, headers = http.posts[0]
    assert url == "http://127.0.0.1:8081/invocations" and body == PAYLOAD and timeout == 10
    assert "Authorization" not in headers and "X-API-Key" not in headers
    assert headers["X-Amzn-Bedrock-AgentCore-Runtime-Session-Id"] == CONVERSATION


def test_over_runtime_with_the_conversation_as_session():
    boto = Boto()
    copilot_client.send(runtime_settings(), PAYLOAD, CONVERSATION, client=boto)
    call = boto.calls[0]
    assert call["agentRuntimeArn"].endswith("runtime/copilot-x") and call["runtimeSessionId"] == CONVERSATION
    assert json.loads(call["payload"]) == PAYLOAD


def test_a_conflict_is_retried_then_goes_through():
    boto, slept = Boto(conflict(), conflict()), []
    assert copilot_client.send(runtime_settings(), PAYLOAD, CONVERSATION, client=boto, sleep=slept.append) == {"accepted": True}
    assert len(boto.calls) == 3 and slept == [1, 1]


def test_four_conflicts_are_not_reached():
    boto, slept = Boto(*[conflict() for _ in range(5)]), []
    with pytest.raises(AgentNotReached):
        copilot_client.send(runtime_settings(), PAYLOAD, CONVERSATION, client=boto, sleep=slept.append)
    assert len(boto.calls) == 5 and len(slept) == 4  # the call, then four retries


def test_another_client_error_is_not_retried():
    other = ClientError({"Error": {"Code": "AccessDeniedException", "Message": "no"}}, "InvokeAgentRuntime")
    boto = Boto(other)
    with pytest.raises(AgentNotReached):
        copilot_client.send(runtime_settings(), PAYLOAD, CONVERSATION, client=boto, sleep=lambda s: None)
    assert len(boto.calls) == 1


def test_a_refusal_and_a_timeout_are_told_apart():
    with pytest.raises(AgentRefused, match="busy"):
        copilot_client.send(settings(), PAYLOAD, CONVERSATION, Http({"refused": "busy"}))
    with pytest.raises(AgentMaybe):
        copilot_client.send(settings(), PAYLOAD, CONVERSATION, Http(error=httpx.ReadTimeout("slow")))
    with pytest.raises(AgentNotReached):
        copilot_client.send(settings(), PAYLOAD, CONVERSATION, Http(error=httpx.ConnectError("down")))


def test_copilot_model_is_luna_low_or_model_name(monkeypatch):
    assert COPILOT_MODEL == ("openai/gpt-6-luna", "low")
    assert settings().copilot_model == ("openai/gpt-6-luna", "low")
    monkeypatch.setenv("MODEL_NAME", "some/model")
    assert settings().copilot_model == ("some/model", "default")


def test_the_settings_come_from_the_process_environment_only(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text("UNDERCOVER_COPILOT=http://elsewhere:1\nUNDERCOVER_COPILOT_RUNTIME=arn:x\n")
    s = Settings(_env_file=env)
    assert s.copilot_url == "http://127.0.0.1:8081" and s.copilot_runtime_arn is None
    monkeypatch.setenv("UNDERCOVER_COPILOT", "http://elsewhere:1")
    monkeypatch.setenv("UNDERCOVER_COPILOT_RUNTIME", "arn:x")
    s = Settings(_env_file=None)
    assert s.copilot_url == "http://elsewhere:1" and s.copilot_runtime_arn == "arn:x"
