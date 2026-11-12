"""The agent's API key: which routes take it, which refuse it, and what happens with none set."""
import httpx
import pytest
from fastapi.testclient import TestClient

from agent import connection
from tests.fakes import create_service
from app.api import create_app
from app.config import Settings
from tests.fakes import MINT, TEST_KEY, api_client, service_client

KEY = {"X-API-Key": TEST_KEY}
WRONG = {"X-API-Key": "not-the-key"}


def make_settings(db_path, data_dir):
    return Settings(_env_file=None, agent_provider="scripted", db_path=db_path, data_dir=data_dir)


@pytest.fixture
def client(db_path, data_dir):
    """The backend with no credentials of its own: each test brings its own."""
    with api_client(create_app(make_settings(db_path, data_dir), jwks=MINT)) as c:
        yield c


@pytest.fixture
def client_no_key(db_path, data_dir, monkeypatch):
    monkeypatch.delenv("UNDERCOVER_API_KEY")
    with api_client(create_app(make_settings(db_path, data_dir), jwks=MINT)) as c:
        yield c


def test_a_shared_read_takes_the_key_or_a_sign_in_and_refuses_neither(client):
    assert client.get("/claims/CLM-1001", headers=KEY).status_code == 200
    assert client.get("/claims/CLM-1001", headers=MINT.header()).status_code == 200
    assert client.get("/claims/CLM-1001").status_code == 401
    assert client.get("/claims/CLM-1001", headers=WRONG).status_code == 401


def test_a_handler_action_refuses_only_the_key(client):
    for method, path, body in [
        ("POST", "/dispatches", {"workers": 1}),
        ("POST", "/evaluations", {"eval_set": "evaluation", "tiny": True, "workers": 1}),
        ("POST", "/rounds", None),
        ("POST", "/payments/PAY-CLM-1003/approve", None),
        ("POST", "/rounds/1/decision", {"proposal_id": 1, "approve": True, "reason": ""}),
        ("PUT", "/settings/payout_approval_limit", {"value": 500}),
    ]:
        assert client.request(method, path, json=body, headers=KEY).status_code == 401, path


def test_a_dashboard_read_refuses_only_the_key(client):
    for path in ("/claims", "/evaluations", "/approvals", "/settings", "/configuration", "/rounds", "/claims/CLM-1001/trace"):
        assert client.get(path, headers=KEY).status_code == 401, path
        assert client.get(path, headers=MINT.header()).status_code == 200, path


def test_with_no_key_set_handlers_work_and_every_key_route_is_refused(client_no_key):
    assert client_no_key.get("/health").status_code == 200
    assert client_no_key.get("/claims", headers=MINT.header()).status_code == 200
    assert client_no_key.get("/claims/CLM-1001", headers=MINT.header()).status_code == 200
    assert client_no_key.get("/claims/CLM-1001", headers=KEY).status_code == 401
    assert client_no_key.get("/claims/CLM-1001", headers={"X-API-Key": ""}).status_code == 401


@pytest.mark.parametrize("bad", ["", " ", "é", "x" * 10_000])
def test_a_malformed_key_is_401_never_500(client, bad):
    assert client.get("/claims/CLM-1001", headers={"X-API-Key": bad.encode("utf-8")}).status_code == 401


def test_an_empty_configured_key_never_matches(db_path, data_dir, monkeypatch):
    monkeypatch.setenv("UNDERCOVER_API_KEY", "")
    with api_client(create_app(make_settings(db_path, data_dir), jwks=MINT)) as c:
        assert c.get("/claims/CLM-1001", headers={"X-API-Key": ""}).status_code == 401
        assert c.get("/claims/CLM-1001", headers={"X-API-Key": "x"}).status_code == 401


def test_the_backend_sends_the_key_to_the_service(db_path, data_dir):
    class Spy:
        seen = []

        def post(self, url, json=None, timeout=None, headers=None):
            self.seen.append(headers)
            return httpx.Response(202, json={"status": "started"})

    with api_client(create_app(make_settings(db_path, data_dir), http=Spy(), jwks=MINT), headers=MINT.header()) as backend:
        assert backend.post("/dispatches", json={"workers": 1}).status_code == 202
    assert Spy.seen == [{"Authorization": f"Bearer {TEST_KEY}"}]


BEARER = {"Authorization": f"Bearer {TEST_KEY}"}
WRONG_BEARER = {"Authorization": "Bearer not-the-key"}


@pytest.fixture
def service(db_path, data_dir):
    settings = make_settings(db_path, data_dir)
    with api_client(create_app(settings, jwks=MINT)) as backend, connection.use(backend):  # the backend it records through
        with TestClient(create_service(settings, inline=True)) as svc:
            yield svc


def test_the_agent_refuses_a_call_without_the_key(service):
    work = {"operation": "work_run", "run_id": 1}
    assert service.post("/invocations", json=work).status_code == 401
    assert service.post("/invocations", json=work, headers=WRONG_BEARER).status_code == 401
    assert service.post("/invocations", json=work, headers={"X-API-Key": TEST_KEY}).status_code == 401  # it takes a bearer token
    assert service.post("/invocations", json={"operation": "health"}).status_code == 200
    assert service.post("/invocations", json=work, headers=BEARER).status_code == 200


def test_the_agent_refuses_to_get_ready_without_the_key(db_path, data_dir, monkeypatch):
    monkeypatch.delenv("UNDERCOVER_API_KEY")
    with TestClient(create_service(make_settings(db_path, data_dir), inline=True), raise_server_exceptions=False) as client:
        answer = client.post("/invocations", json={"operation": "health"})
    assert answer.status_code == 500 and "UNDERCOVER_API_KEY" in answer.text