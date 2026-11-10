"""The agent reaches the backend only over HTTP, with the API key, through one client."""
import ast
from pathlib import Path

import httpx
import pytest
from langgraph.types import RetryPolicy

from agent import api, connection
from agent.dispatcher import dispatch
from agent.worker import work_claim
from tests.fakes import TEST_KEY, RawDb, agent_env, approve, decline, stub

AGENT = Path(__file__).resolve().parents[1] / "agent"
CLAIM = "CLM-1003"


def recording_client(seen: list, status: int = 200, body=None):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("x-api-key"))
        return httpx.Response(status, json={} if body is None else body)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_the_client_sends_the_key_read_as_each_request_goes(monkeypatch):
    seen = []
    with connection.use(recording_client(seen)):
        monkeypatch.setenv("UNDERCOVER_API_KEY", "first")  # set after the module was imported
        connection.call("GET", "/api/health")
        monkeypatch.setenv("UNDERCOVER_API_KEY", "second")
        connection.call("GET", "/api/health")
        monkeypatch.delenv("UNDERCOVER_API_KEY")
        connection.call("GET", "/api/health")
    assert seen == ["first", "second", None]


def test_the_client_asks_the_backend_named_in_the_environment(monkeypatch):
    urls = []
    client = httpx.Client(transport=httpx.MockTransport(lambda r: urls.append(str(r.url)) or httpx.Response(200, json={})))
    monkeypatch.setenv("UNDERCOVER_BACKEND", "http://backend.example:9000/")
    with connection.use(client):
        connection.call("GET", "/api/health", params={"a": "b"})
    assert urls == ["http://backend.example:9000/api/health?a=b"]


def test_a_404_is_a_lookup_error_and_another_refusal_says_why():
    with connection.use(recording_client([], 404, {"detail": "no claim CLM-9"})):
        with pytest.raises(LookupError, match="no claim CLM-9"):
            connection.call("GET", "/api/claims/CLM-9")
    with connection.use(recording_client([], 403, {"detail": "over the limit of $1,000.00"})):
        with pytest.raises(connection.Refused) as caught:
            connection.call("POST", "/api/claims/CLM-1/pay", json={"amount": 5000})
    assert caught.value.status_code == 403 and "over the limit" in str(caught.value)


def imports_from_app(path: Path) -> set[str]:
    found = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module and (node.module == "app" or node.module.startswith("app.")):
            found |= {f"{node.module}:{a.name}" for a in node.names}
        elif isinstance(node, ast.Import):
            found |= {f"{a.name}:" for a in node.names if a.name.startswith("app")}
    return found


def test_the_tools_reach_the_records_only_through_the_client():
    for name in ("tools.py", "graph.py", "worker.py"):
        text = (AGENT / name).read_text(encoding="utf-8")
        assert "sqlalchemy" not in text and "app.claims" not in text, name


ALLOWED = {
    "app.config", "app.documents", "app.reference",
    "app.db:CONNECTION_WAIT_S", "app.db:SCHEMA", "app.db:row_level_security",
    "app.auth:Handler", "app.auth:key_matches",
}


@pytest.mark.parametrize("path", sorted(AGENT.glob("*.py")), ids=lambda p: p.name)
def test_only_the_improvers_client_reaches_the_scores(path):
    """No module of the agent imports anything from the backend but what is shared, and only the improver imports
    the improver's client."""
    for item in imports_from_app(path):
        module, _, name = item.partition(":")
        assert module in ALLOWED or item in ALLOWED, f"{path.name} imports {item}"
    text = path.read_text(encoding="utf-8")
    if path.name not in ("improver.py", "improver_api.py", "service.py"):
        assert "improver_api" not in text, path.name


def test_a_decision_goes_through_the_backend_and_a_repeat_counts_as_done(db_path, data_dir):
    with agent_env(db_path, data_dir, stub()) as env:
        decision = approve(420.0)
        first = api.record_decision(CLAIM, decision, "m")
        again = api.record_decision(CLAIM, decision, "m")
        assert first["decision"]["payout"] == again["decision"]["payout"] == 420.0
        assert RawDb(env.engine).execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 1
        with pytest.raises(connection.Refused) as caught:
            api.record_decision(CLAIM, approve(100.0), "m")
        assert caught.value.status_code == 409


def test_a_backend_restart_uses_up_no_attempt_and_the_claim_carries_on(db_path, data_dir, monkeypatch):
    """The backend is out of reach for one call: the claim is retried from its last saved step, not failed."""
    model = stub(default=decline())
    with agent_env(db_path, data_dir, model) as env:
        real, down = api.record_model_call, [httpx.ConnectError("the backend is restarting")]

        def flaky(*args):
            if down:
                raise down.pop()
            return real(*args)

        monkeypatch.setattr(api, "record_model_call", flaky)
        run_id = api.start_dispatch(1, "stub/model", None, None)
        dispatch(env, run_id, 1, RetryPolicy(initial_interval=0.01, max_interval=0.02, max_attempts=3, retry_on=(httpx.TransportError,)))
        db = RawDb(env.engine)
        assert db.execute("SELECT COUNT(*) FROM claims WHERE attempts > 0 OR status = 'needs_handler'").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 26  # every claim carried on to its decision
        assert not db.execute("SELECT 1 FROM trace_events WHERE kind = 'error' AND content LIKE '%failed%'")


def test_a_review_is_recorded_for_the_handler_with_the_key(db_path, data_dir, monkeypatch):
    from agent import improver_api

    payload = {"instructions": "Decide carefully.", "model": "openai/gpt-6-luna", "reasoning_effort": "low"}
    with agent_env(db_path, data_dir, stub()):
        round_id = improver_api.start_round("HND-01")
        proposal = improver_api.add_proposal(round_id, "change", payload, "a reason")
        monkeypatch.delenv("UNDERCOVER_API_KEY")  # without the key nothing is recorded, whoever is named
        with pytest.raises(connection.Refused) as caught:
            improver_api.decide_proposal(proposal, True, "", "HND-01")
        assert caught.value.status_code == 401
        monkeypatch.setenv("UNDERCOVER_API_KEY", TEST_KEY)
        decided = improver_api.decide_proposal(proposal, True, "", "HND-01")
        assert decided["decided_by"] == "HND-01" and decided["version"] == 2
        with pytest.raises(LookupError):  # the name must be a handler's
            improver_api.start_round("HND-99")
