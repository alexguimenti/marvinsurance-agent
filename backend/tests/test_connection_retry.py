"""When the platform answers "Rate Exceeded" (too many concurrent invocations), a request waits a moment and tries again; it was never run."""
import httpx
import pytest

from agent import connection
from agent.connection import Refused

RATE_EXCEEDED = {"Reason": "ConcurrentInvocationLimitExceeded", "Type": "User", "message": "Rate Exceeded."}


def client_answering(*answers):
    """A client whose backend gives each answer in turn, and remembers what it was sent."""
    sent, queue = [], list(answers)

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        status, body = queue.pop(0)
        return httpx.Response(status, json=body)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    client.sent = sent
    return client


@pytest.fixture
def pauses(monkeypatch):
    waited = []
    monkeypatch.setattr(connection, "_sleep", waited.append)
    return waited


def test_a_rate_exceeded_answer_is_tried_again_and_the_later_answer_is_used(pauses):
    client = client_answering((429, RATE_EXCEEDED), (429, RATE_EXCEEDED), (200, {"ok": True}))
    with connection.use(client):
        assert connection.call("POST", "/api/agent/claims/CLM-1/steps", json={"kind": "x"}) == {"ok": True}
    assert len(client.sent) == 3 and len(pauses) == 2
    assert pauses[1] > pauses[0]  # each wait is longer than the last


def test_it_gives_up_after_its_tries_and_says_what_the_platform_said(pauses):
    client = client_answering(*[(429, RATE_EXCEEDED)] * connection.RATE_TRIES)
    with connection.use(client), pytest.raises(Refused) as caught:
        connection.call("GET", "/api/claims")
    assert caught.value.status_code == 429 and "Rate Exceeded" in str(caught.value)
    assert len(client.sent) == connection.RATE_TRIES and len(pauses) == connection.RATE_TRIES - 1


def test_the_other_refusals_are_not_tried_again(pauses):
    client = client_answering((409, {"detail": "already held"}))
    with connection.use(client), pytest.raises(Refused):
        connection.call("POST", "/api/dispatches", json={"workers": 3})
    assert len(client.sent) == 1 and pauses == []
