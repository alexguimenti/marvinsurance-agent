"""The Copilot's calls to the app carry the handler's sign-in and never the agent's key."""
import httpx
import pytest

from agent import connection
from agent.connection import Refused
from agent.copilot import app_api
from agent.copilot.asker import Asker
from tests.fakes import TEST_KEY


class App:
    """A stand-in for the app: writes each request down and answers from `answers` (path to JSON), or 200 with `{}`."""

    def __init__(self, answers=None, status=200):
        self.requests, self.answers, self.status = [], answers or {}, status

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.status, json=self.answers.get(request.url.path, {}))

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))


def test_a_call_with_a_token_never_sends_the_key(monkeypatch):
    monkeypatch.setenv("UNDERCOVER_API_KEY", TEST_KEY)  # set, as on the claim agent's side
    app = App({"/api/me": {"handler_id": "HND-01"}})
    with connection.use(app.client()):
        app_api.who("tok-1")
        app_api.post("tok-1", "/api/dispatches", {"workers": 2})
        connection.call("GET", "/api/me")  # the claim agent's own call still sends the key
    first, second, third = app.requests
    for sent in (first, second):
        assert sent.headers["authorization"] == "Bearer tok-1" and "x-api-key" not in sent.headers
    assert third.headers["x-api-key"] == TEST_KEY and "authorization" not in third.headers


def test_the_asker_repr_hides_the_token():
    asker = Asker("tok-secret", {"handler_id": "HND-01"}, "conversation-x", 3)
    assert "tok-secret" not in repr(asker) and "HND-01" in repr(asker)
    assert asker.token == "tok-secret"


def test_who_returns_the_handler_and_a_refusal_raises():
    me = {"handler_id": "HND-01", "name": "Dana Whitfield", "can_approve_over_limit": True}
    with connection.use(App({"/api/me": me}).client()):
        assert app_api.who("tok-1") == me
    with connection.use(App(status=401).client()):
        with pytest.raises(Refused) as refused:
            app_api.who("tok-expired")
    assert refused.value.status_code == 401


def test_the_reports_go_to_the_copilots_routes():
    app = App({"/api/copilot/turns/7/steps": {"step_id": 11}, "/api/copilot/turns/7/pause": {"claim_id": "CLM-1003"}})
    with connection.use(app.client()):
        assert app_api.record_step("tok-1", 7, "claims", "get_claim", "CLM-1003", {"claim_id": "CLM-1003"}) == 11
        app_api.finish_step("tok-1", 11, "done", "the claim", sources=None, reason=None)
        assert app_api.pause("tok-1", 7, "CLM-1003") == {"claim_id": "CLM-1003"}
        app_api.end("tok-1", 7, "All done.", 3, 0.01)
        app_api.fail("tok-1", 7, "boom")
    assert [r.url.path for r in app.requests] == ["/api/copilot/turns/7/steps", "/api/copilot/steps/11", "/api/copilot/turns/7/pause",
                                                   "/api/copilot/turns/7/end", "/api/copilot/turns/7/fail"]
    assert all(r.headers["authorization"] == "Bearer tok-1" and "x-api-key" not in r.headers for r in app.requests)
