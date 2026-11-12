"""`resume_claim` on the agent, and the sign-off that asks for it."""
import logging

import httpx
import pytest

from agent.service import create_service
from app.api import create_app
from app.claims import get_claim
from app.config import Settings
from app.db import make_engine
from app.payments import get_payment
from tests.fakes import MINT, TEST_KEY, RawDb, api_client, service_client

BIG = "CLM-1003"  # the scripted model approves $3,750: over the limit


def settings_for(db_path, data_dir):
    return Settings(_env_file=None, agent_provider="scripted", db_path=db_path, data_dir=data_dir)


@pytest.fixture
def service(db_path, data_dir):
    with service_client(create_service(settings_for(db_path, data_dir), inline=True)) as c:
        yield c


def thread_state(db_path, claim_id=BIG):
    """The saved place of a claim's thread, read by a graph of its own."""
    from agent.worker import open_env
    from tests.fakes import ScreenStub

    settings = settings_for(db_path, str(__import__("tests.conftest", fromlist=["DATA_DIR"]).DATA_DIR))
    with open_env(settings, screen_model=ScreenStub()) as env:
        engine = make_engine(settings, max_connections=1)
        thread = get_claim(engine, claim_id)["thread_id"]
        with env.claim_graph() as graph:
            saved = graph.get_state({"configurable": {"thread_id": thread}})
        engine.dispose()
        return saved


class Recorder:
    """The httpx the app calls the agent with, writing down each post, or failing as a service that is down does."""

    def __init__(self, fail: Exception | None = None):
        self.posts, self.fail = [], fail

    def post(self, url, json=None, timeout=None, headers=None):
        self.posts.append((url, json, headers))
        if self.fail:
            raise self.fail
        return httpx.Response(202, json={"status": "resuming"}, request=httpx.Request("POST", url))


def backend(db_path, data_dir, http):
    return api_client(create_app(settings_for(db_path, data_dir), http=http, jwks=MINT))


def test_resume_claim_refuses_a_claim_that_is_not_paused(service):
    assert service.post("/claims/CLM-1001/resume").status_code == 409  # waiting
    assert "not waiting for a handler's answer" in service.post("/claims/CLM-1001/resume").json()["detail"]
    service.post("/dispatches", json={"workers": 25})
    assert service.post("/claims/CLM-1001/resume").status_code == 409  # decided and paid
    assert service.post("/claims/CLM-0000/resume").status_code == 409


def test_a_resume_after_a_sign_off_ends_the_work(service, db_path, data_dir):
    service.post("/dispatches", json={"workers": 25})
    assert thread_state(db_path).next == ("wait_for_sign_off",)  # paused, its place saved
    with backend(db_path, data_dir, Recorder()) as app:  # the sign-off: pays, and asks the agent to resume
        signed = app.post("/payments/PAY-CLM-1003/approve", headers=MINT.header("HND-01"))
        assert signed.status_code == 200
    assert service.post("/claims/CLM-1003/resume").status_code == 202
    saved = thread_state(db_path)
    assert not saved.next and not saved.interrupts  # the work ended
    engine = make_engine(settings_for(db_path, data_dir), max_connections=1)
    assert get_payment(engine, BIG).status == "paid"
    assert RawDb(engine).execute("SELECT COUNT(*) FROM payments WHERE claim_id = ?", (BIG,)).fetchone()[0] == 1


def test_two_resumes_of_one_claim_pay_once(service, db_path, data_dir):
    service.post("/dispatches", json={"workers": 25})
    with backend(db_path, data_dir, Recorder()) as app:
        app.post("/payments/PAY-CLM-1003/approve", headers=MINT.header("HND-01"))
    assert service.post("/claims/CLM-1003/resume").status_code == 202
    assert service.post("/claims/CLM-1003/resume").status_code == 409  # no longer paused
    engine = make_engine(settings_for(db_path, data_dir), max_connections=1)
    assert RawDb(engine).execute("SELECT COUNT(*) FROM payments WHERE claim_id = ?", (BIG,)).fetchone()[0] == 1


def test_a_resume_runs_beside_a_dispatch(db_path, data_dir, monkeypatch):
    import threading
    import time

    import agent.service as service_module

    settings = settings_for(db_path, data_dir)
    with service_client(create_service(settings, inline=True)) as first:
        first.post("/dispatches", json={"workers": 25})  # three claims over the limit pause
    with backend(db_path, data_dir, Recorder()) as app:
        app.post("/payments/PAY-CLM-1003/approve", headers=MINT.header("HND-01"))
    release = threading.Event()
    monkeypatch.setattr(service_module, "dispatch", lambda env, run_id, workers: release.wait(30))
    with service_client(create_service(settings, inline=False)) as second:
        assert second.post("/dispatches", json={"workers": 1}).status_code == 202  # a dispatch holds the service's lock
        assert second.post("/dispatches", json={"workers": 1}).status_code == 409
        assert second.post("/claims/CLM-1003/resume").status_code == 202  # and a resume goes ahead beside it
        deadline = time.monotonic() + 30
        while thread_state(db_path).next and time.monotonic() < deadline:
            time.sleep(0.2)
        assert not thread_state(db_path).next  # it finished while the dispatch was still going
        release.set()


def test_a_sign_off_asks_the_agent_to_resume_with_the_key(db_path, data_dir):
    recorder = Recorder()
    with backend(db_path, data_dir, recorder) as app:
        app.post("/agent/claims/CLM-1003/take", json={"run_id": None}, headers={"X-API-Key": TEST_KEY})
        from tests.fakes import record_approval

        record_approval(app, BIG, 2000.0)
        app.post(f"/agent/claims/{BIG}/hold", headers={"X-API-Key": TEST_KEY})
        answer = app.post("/payments/PAY-CLM-1003/approve", headers=MINT.header("HND-01"))
    assert answer.status_code == 200 and answer.json()["status"] == "paid" and answer.json()["claim"]["status"] == "decided"
    url, _, headers = recorder.posts[0]
    assert url.endswith("/claims/CLM-1003/resume") and headers == {"X-API-Key": TEST_KEY}


def test_the_sign_off_stands_when_the_agent_cannot_be_reached(db_path, data_dir, caplog):
    with backend(db_path, data_dir, Recorder(fail=httpx.ConnectError("the agent is down"))) as app:
        key = {"X-API-Key": TEST_KEY}
        app.post(f"/agent/claims/{BIG}/take", json={"run_id": None}, headers=key)
        from tests.fakes import record_approval

        record_approval(app, BIG, 2000.0)
        app.post(f"/agent/claims/{BIG}/hold", headers=key)
        with caplog.at_level(logging.WARNING):
            answer = app.post("/payments/PAY-CLM-1003/approve", headers=MINT.header("HND-01"))
        assert answer.status_code == 200 and answer.json()["status"] == "paid"  # and the claim, whatever the call did
    assert any("could not ask the agent to resume CLM-1003" in r.message for r in caplog.records)


def test_a_sign_off_goes_through_only_while_the_claim_is_waiting(db_path, data_dir):
    with backend(db_path, data_dir, Recorder()) as app:
        key = {"X-API-Key": TEST_KEY}
        app.post(f"/agent/claims/{BIG}/take", json={"run_id": None}, headers=key)
        from tests.fakes import record_approval

        record_approval(app, BIG, 2000.0)  # approved, but not held: not waiting for a sign-off
        RawDb(make_engine(settings_for(db_path, data_dir), max_connections=1)).execute(
            "INSERT INTO payments (payment_id, claim_id, amount, status) VALUES ('PAY-CLM-1003', 'CLM-1003', 2000, 'held')")
        assert app.post("/payments/PAY-CLM-1003/approve", headers=MINT.header("HND-01")).status_code == 409
