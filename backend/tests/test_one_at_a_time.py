"""The app keeps one piece of the agent's work going at a time: it creates runs, rounds and reviews itself, under one lock, and asks
the agent to work them by id."""
import threading
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import QueuePool

from app import improvement, runs
from app.api import create_app
from app.config import Settings
from app.db import init_schema, make_engine
from app.settings_store import get_settings
from app.work import STOPPED_FOR_QUIET, WorkGoing, start_work
from tests.fakes import MINT, RawDb, api_client

DANA = MINT.header("HND-01")
OK_MODEL = "deepseek/deepseek-v4.1-flash"


def make_settings(db_path, data_dir):
    return Settings(_env_file=None, agent_provider="scripted", db_path=db_path, data_dir=data_dir)


class Agent:
    """The httpx the app calls the agent with: takes the work, or does what `answer` says, and writes each payload down."""

    def __init__(self, answer=None, error=None):
        self.answer, self.error, self.payloads = answer or {"accepted": True}, error, []

    def post(self, url, json=None, timeout=None, headers=None):
        self.payloads.append(json)
        if self.error:
            raise self.error
        return httpx.Response(200, json=self.answer, request=httpx.Request("POST", url))


@pytest.fixture
def raw(db_path, data_dir):
    engine = make_engine(make_settings(db_path, data_dir), max_connections=2)
    init_schema(engine)
    yield RawDb(engine)
    engine.dispose()


def backend(db_path, data_dir, agent):
    return api_client(create_app(make_settings(db_path, data_dir), http=agent, jwks=MINT), headers=DANA)


def insert_run(raw, kind="dispatch", started_ago=0, heard_ago=None, round_id=None, status="running"):
    at = (datetime.now(timezone.utc) - timedelta(minutes=started_ago)).strftime("%Y-%m-%d %H:%M:%S")
    heard = "NULL" if heard_ago is None else "'" + (datetime.now(timezone.utc) - timedelta(minutes=heard_ago)).strftime("%Y-%m-%d %H:%M:%S") + "'"
    raw.execute(f"INSERT INTO runs (kind, status, workers, model, reasoning_effort, started_at, heard_at, round_id) "
                f"VALUES ('{kind}', '{status}', 3, 'm', 'none', '{at}', {heard}, {round_id or 'NULL'})")


def open_round(raw, stage="improving", started_ago=0):
    engine = raw.engine
    round_id = improvement.start_round(engine, "HND-01", 1, get_settings(engine))
    raw.execute(f"UPDATE rounds SET stage = '{stage}', started_at = datetime('now', '-{started_ago} minutes') WHERE round_id = {round_id}")
    return round_id


@pytest.mark.parametrize("make,what", [
    (lambda raw: insert_run(raw, "dispatch"), "a dispatch"),
    (lambda raw: insert_run(raw, "evaluation"), "an evaluation"),
    (lambda raw: open_round(raw, "improving"), "round 1"),
    (lambda raw: open_round(raw, "evaluating"), "round 1"),
])
def test_a_second_start_is_refused_while_work_is_going_and_the_409_names_it(db_path, data_dir, raw, make, what):
    make(raw)
    agent = Agent()
    with backend(db_path, data_dir, agent) as client:
        for path, body in (("/dispatches", {"workers": 3}), ("/evaluations", {"eval_set": "evaluation", "tiny": True, "workers": 3}), ("/rounds", None)):
            answer = client.post(path, json=body)
            assert answer.status_code == 409 and answer.json()["detail"] == f"{what} is already running", path
    assert agent.payloads == []  # the agent was never asked


def test_a_round_waiting_for_a_handler_does_not_block_a_dispatch(db_path, data_dir, raw):
    open_round(raw, "awaiting_change")
    with backend(db_path, data_dir, Agent()) as client:
        assert client.post("/dispatches", json={"workers": 3}).status_code == 202


def test_a_start_creates_the_run_itself_and_asks_the_agent_for_it_by_id(db_path, data_dir, raw):
    agent = Agent()
    with backend(db_path, data_dir, agent) as client:
        answer = client.post("/dispatches", json={"workers": 4})
    assert answer.status_code == 202 and answer.json() == {"status": "started", "run_id": 1}
    assert agent.payloads == [{"operation": "work_run", "run_id": 1}]
    row = runs.get_run(raw.engine, 1)
    assert (row["kind"], row["status"], row["workers"], row["model"]) == ("dispatch", "running", 4, "scripted")


def test_two_starts_at_the_same_moment_only_one_goes_ahead(db_path, data_dir, raw):
    agent, codes, barrier = Agent(), [], threading.Barrier(2)
    with backend(db_path, data_dir, agent) as client:
        def go():
            barrier.wait()
            codes.append(client.post("/dispatches", json={"workers": 3}).status_code)

        threads = [threading.Thread(target=go) for _ in range(2)]
        [t.start() for t in threads]
        [t.join(60) for t in threads]
    assert sorted(codes) == [202, 409]
    assert raw.execute("SELECT COUNT(*) FROM runs")[0][0] == 1 and len(agent.payloads) == 1


def test_a_start_after_five_quiet_minutes_stops_the_old_work_with_its_reason_and_goes_ahead(db_path, data_dir, raw):
    insert_run(raw, "dispatch", started_ago=6)
    with backend(db_path, data_dir, Agent()) as client:
        assert client.post("/dispatches", json={"workers": 3}).status_code == 202
    old = runs.get_run(raw.engine, 1)
    assert old["status"] == "stopped" and old["error"] == STOPPED_FOR_QUIET


def test_the_agents_word_counts_from_the_later_of_the_start_and_its_last_word(db_path, data_dir, raw):
    insert_run(raw, "dispatch", started_ago=6, heard_ago=1)  # started long ago, heard from a minute ago
    with backend(db_path, data_dir, Agent()) as client:
        assert client.post("/dispatches", json={"workers": 3}).status_code == 409
    assert runs.get_run(raw.engine, 1)["status"] == "running"


def test_quiet_round_is_stopped_with_its_evaluation_and_the_new_work_goes_ahead(db_path, data_dir, raw):
    round_id = open_round(raw, "evaluating", started_ago=6)
    insert_run(raw, "evaluation", started_ago=6, round_id=round_id)
    with backend(db_path, data_dir, Agent()) as client:
        assert client.post("/dispatches", json={"workers": 3}).status_code == 202
    assert improvement.get_round(raw.engine, round_id)["stage"] == "stopped"
    assert improvement.get_round(raw.engine, round_id)["error"] == STOPPED_FOR_QUIET
    assert runs.get_run(raw.engine, 1)["status"] == "stopped"


def test_an_evaluation_a_round_made_is_never_stopped_on_its_own(db_path, data_dir, raw):
    round_id = open_round(raw, "evaluating")  # the round has just been heard from
    raw.execute("UPDATE rounds SET heard_at = datetime('now') WHERE round_id = 1")
    insert_run(raw, "evaluation", started_ago=30, round_id=round_id)  # its own clock is old, the round's is not
    with backend(db_path, data_dir, Agent()) as client:
        assert client.post("/dispatches", json={"workers": 3}).status_code == 409
    assert runs.get_run(raw.engine, 1)["status"] == "running"


@pytest.mark.parametrize("agent,code", [
    (Agent({"refused": "a dispatch is already running"}), 409),
    (Agent(error=httpx.ConnectError("refused")), 503),
])
def test_a_start_the_agent_did_not_take_is_undone_with_the_reason(db_path, data_dir, raw, agent, code):
    with backend(db_path, data_dir, agent) as client:
        assert client.post("/dispatches", json={"workers": 3}).status_code == code
        assert client.post("/rounds").status_code == code
    row = runs.get_run(raw.engine, 1)
    assert row["status"] == "stopped" and row["error"]
    assert improvement.get_round(raw.engine, 1)["stage"] == "stopped" and not improvement.get_round(raw.engine, 1)["open"]
    assert improvement.get_round(raw.engine, 1)["error"]


def test_a_timeout_leaves_the_work_going(db_path, data_dir, raw):
    with backend(db_path, data_dir, Agent(error=httpx.ReadTimeout("slow"))) as client:
        answer = client.post("/dispatches", json={"workers": 3})
    assert answer.status_code == 504 and "Reload the page" in answer.json()["detail"]
    assert runs.get_run(raw.engine, 1)["status"] == "running"


def waiting_proposal(raw, kind):
    engine = raw.engine
    round_id = open_round(raw, "improving")
    payload = {"instructions": "x", "model": OK_MODEL, "reasoning_effort": "low"} if kind == "change" else {"recipe": "1. Do it differently."}
    return round_id, improvement.add_proposal(engine, round_id, kind, payload, "because")


def test_a_review_the_agent_did_not_take_is_cleared_for_a_change(db_path, data_dir, raw):
    from app import configs

    round_id, proposal = waiting_proposal(raw, "change")
    with backend(db_path, data_dir, Agent({"refused": "round 1 is not open"})) as client:
        answer = client.post(f"/rounds/{round_id}/decision", json={"proposal_id": proposal, "approve": True, "reason": ""})
    assert answer.status_code == 409
    row = improvement.get_proposal(raw.engine, proposal)
    assert row["status"] == "waiting" and row["decided_by"] is None
    assert [v["version"] for v in configs.list_versions(raw.engine, "config")] == [1]  # the version the approval made is gone
    assert improvement.get_round(raw.engine, round_id)["stage"] == "awaiting_change"
    assert improvement.get_round(raw.engine, round_id)["version_tried"] is None


def test_a_review_the_agent_did_not_take_is_cleared_for_a_recipe(db_path, data_dir, raw):
    from app import configs

    round_id, proposal = waiting_proposal(raw, "recipe")
    with backend(db_path, data_dir, Agent(error=httpx.ConnectError("down"))) as client:
        assert client.post(f"/rounds/{round_id}/decision", json={"proposal_id": proposal, "approve": True, "reason": ""}).status_code == 503
    assert improvement.get_proposal(raw.engine, proposal)["status"] == "waiting"
    assert configs.current(raw.engine, "recipe")["version"] == 1  # the earlier recipe is current again
    assert [v["version"] for v in configs.list_versions(raw.engine, "recipe")] == [1]
    assert improvement.get_round(raw.engine, round_id)["stage"] == "awaiting_recipe"


def test_a_recorded_review_is_handed_over_by_id_and_makes_the_round_work(db_path, data_dir, raw):
    round_id, proposal = waiting_proposal(raw, "change")
    agent = Agent()
    with backend(db_path, data_dir, agent) as client:
        answer = client.post(f"/rounds/{round_id}/decision", json={"proposal_id": proposal, "approve": False, "reason": "Too broad."})
    assert answer.status_code == 202
    assert agent.payloads == [{"operation": "carry_round_on", "round_id": round_id, "proposal_id": proposal}]
    assert improvement.get_proposal(raw.engine, proposal)["decision_reason"] == "Too broad."
    assert improvement.get_round(raw.engine, round_id)["stage"] == "improving"  # working again, so nothing else starts beside it


def test_a_review_of_an_unknown_or_foreign_proposal_is_a_404_and_one_decided_twice_a_409(db_path, data_dir, raw):
    round_id, proposal = waiting_proposal(raw, "change")
    with backend(db_path, data_dir, Agent()) as client:
        assert client.post(f"/rounds/{round_id}/decision", json={"proposal_id": 99, "approve": True, "reason": ""}).status_code == 404
        assert client.post(f"/rounds/{round_id + 5}/decision", json={"proposal_id": proposal, "approve": True, "reason": ""}).status_code == 404
        assert client.post(f"/rounds/{round_id}/decision", json={"proposal_id": proposal, "approve": True, "reason": ""}).status_code == 202
        raw.execute(f"UPDATE rounds SET stage = 'awaiting_change' WHERE round_id = {round_id}")
        assert client.post(f"/rounds/{round_id}/decision", json={"proposal_id": proposal, "approve": False, "reason": ""}).status_code == 409


def test_a_request_uses_one_connection_at_a_time(db_path, data_dir, raw):
    """A pool of one refuses a second connection: a start that held one while it asked for another would time out here."""
    one = create_engine(f"sqlite:///{db_path}", poolclass=QueuePool, pool_size=1, max_overflow=0, pool_timeout=1)
    try:
        assert start_work(one, lambda: runs.start_dispatch(one, 3, "m", None, 1)) == 1
        with pytest.raises(WorkGoing):
            start_work(one, lambda: runs.start_dispatch(one, 3, "m", None, 1))
        improvement.stop_round_with_reason(one, 99, "none")  # a round that does not exist: nothing happens
    finally:
        one.dispose()


def test_stopping_a_round_stops_its_running_evaluation_and_nothing_else(db_path, data_dir, raw):
    round_id = open_round(raw, "evaluating")
    insert_run(raw, "evaluation", round_id=round_id)
    assert improvement.stop_round_with_reason(raw.engine, round_id, "it failed") is True
    assert runs.get_run(raw.engine, 1)["error"] == "it failed" and runs.get_run(raw.engine, 1)["status"] == "stopped"
    other = open_round(raw, "improving")
    insert_run(raw, "dispatch")
    improvement.stop_round_with_reason(raw.engine, other, "it failed")
    assert runs.get_run(raw.engine, 2)["status"] == "running"  # a dispatch is nobody's evaluation
