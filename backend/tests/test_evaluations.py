"""Evaluations work their own copies of a labelled set, through the same dispatcher and claim agent."""
import pytest
from fastapi.testclient import TestClient

from agent.dispatcher import dispatch
from agent.scripted import ScriptedChatModel
from tests.fakes import create_service
from app.api import create_app
from app.claims import get_claim
from app.config import Settings
from app.db import make_engine
from app.evaluations import is_copy, start_evaluation
from app.payments import sign_off
from app.runs import RunActive, get_run, run_claim_ids, start_dispatch, stop_running
from tests.fakes import service_client, api_client, MINT, RawDb, agent_env

# Fields of the handlers' decisions in data/evals/: none may reach a claim's record.
ANSWER_KEY_FIELDS = {"handler_id", "reason", "failure_class", "tests", "needs", "tone", "difficulty", "over_approval_limit"}


@pytest.fixture
def env(db_path, data_dir):
    with agent_env(db_path, data_dir, ScriptedChatModel()) as opened:
        yield opened


def evaluate(env, eval_set="evaluation", tiny=False, workers=5):
    run_id = start_evaluation(env.engine, env.ref, eval_set, tiny, workers, env.model_id, None)
    dispatch(env, run_id, workers)
    return run_id


def queue_counts(env):
    """The queue page's counts, as the backend gives them."""
    with api_client(create_app(Settings(_env_file=None, db_path=str(env.settings.db_path), data_dir=str(env.ref.data_dir)), jwks=MINT), headers=MINT.header()) as client:
        statuses = [c["status"] for c in client.get("/claims").json()]
    return {s: statuses.count(s) for s in set(statuses)}, len(statuses)


def all_keys(value) -> set:
    if isinstance(value, dict):
        return set(value) | set().union(*(all_keys(v) for v in value.values()))
    if isinstance(value, list):
        return set().union(*(all_keys(v) for v in value)) if value else set()
    return set()


def test_an_evaluation_leaves_the_queue_as_it_was(env):
    before = queue_counts(env)
    run_id = evaluate(env)
    assert queue_counts(env) == before == ({"waiting": 26}, 26)
    assert len(run_claim_ids(env.engine, run_id)) == 25


def test_copies_get_new_ids_written_into_their_records(env):
    run_id = start_evaluation(env.engine, env.ref, "evaluation", False, 5, "scripted", None)
    copy = get_claim(env.engine, f"E{run_id}-CLM-1003")
    assert (copy["copy_of"], copy["evaluation_id"], copy["status"]) == ("CLM-1003", run_id, "waiting")
    payload = RawDb(env.engine).execute("SELECT payload FROM claims WHERE claim_id = ?", (f"E{run_id}-CLM-1003",)).fetchone()[0]
    assert f'"claim_id": "E{run_id}-CLM-1003"' in payload
    assert is_copy(env.engine, f"E{run_id}-CLM-1003") and not is_copy(env.engine, "CLM-1003")


def test_held_copies_count_as_finished_and_their_progress_is_cleared(env):
    run_id = evaluate(env)
    held = get_claim(env.engine, f"E{run_id}-CLM-1003")
    assert held["status"] == "awaiting_approval" and held["decision"]["decision"] == "approve"
    assert env.graph.get_state({"configurable": {"thread_id": held["thread_id"]}}).values == {}
    decided = get_claim(env.engine, f"E{run_id}-CLM-1001")
    assert env.graph.get_state({"configurable": {"thread_id": decided["thread_id"]}}).values == {}
    assert get_run(env.engine, run_id)["status"] == "finished"


def test_tiny_takes_the_five_tiny_claims_of_the_set(env):
    run_id = start_evaluation(env.engine, env.ref, "evaluation", True, 5, "scripted", None)
    assert sorted(c.split("-", 1)[1] for c in run_claim_ids(env.engine, run_id)) == [
        "CLM-1007", "CLM-1015", "CLM-1017", "CLM-1020", "CLM-1021"]


def test_held_out_copies_come_from_the_held_out_claims(env):
    run_id = evaluate(env, "held_out", tiny=True)
    ids = run_claim_ids(env.engine, run_id)
    assert sorted(c.split("-", 1)[1] for c in ids) == ["CLM-1104", "CLM-1106", "CLM-1110", "CLM-1112", "CLM-1114"]
    assert all(get_claim(env.engine, c)["decision"] is not None for c in ids)
    assert RawDb(env.engine).execute("SELECT COUNT(*) FROM claims WHERE claim_id LIKE 'CLM-11%'").fetchone()[0] == 0


def test_get_claim_on_a_copy_holds_nothing_from_the_handlers_decisions(env):
    run_id = evaluate(env, tiny=True)
    copy = get_claim(env.engine, f"E{run_id}-CLM-1007")
    assert copy["decision"]["model"] == env.model_id  # the agent's own decision, and nothing else
    assert not ANSWER_KEY_FIELDS & all_keys(copy)


def test_a_sign_off_is_refused_on_a_copy(env):
    run_id = evaluate(env)
    with pytest.raises(PermissionError):
        sign_off(env.engine, env.ref, f"PAY-E{run_id}-CLM-1003", "HND-01")


def test_a_dispatch_never_carries_on_a_copy(env):
    run_id = start_evaluation(env.engine, env.ref, "evaluation", True, 5, "scripted", None)
    RawDb(env.engine).execute("UPDATE claims SET status='in_progress', thread_id=claim_id || '-1' WHERE evaluation_id = ?", (run_id,))
    stop_running(env.engine)
    dispatch_id = start_dispatch(env.engine, 5, "scripted", None)
    dispatch(env, dispatch_id, 5)
    assert not any(c.startswith("E") for c in run_claim_ids(env.engine, dispatch_id))


def test_a_stopped_evaluation_shows_as_stopped(env):
    run_id = start_evaluation(env.engine, env.ref, "evaluation", True, 5, "scripted", None)
    stop_running(env.engine)  # the service starting again after a kill
    assert get_run(env.engine, run_id)["status"] == "stopped"


def test_an_evaluation_records_its_set_n_model_and_effort(env):
    run_id = start_evaluation(env.engine, env.ref, "held_out", True, 7, "some/model", "low")
    row = get_run(env.engine, run_id)
    assert (row["kind"], row["eval_set"], row["tiny"], row["workers"], row["model"], row["reasoning_effort"]) == (
        "evaluation", "held_out", True, 7, "some/model", "low")


def test_only_one_run_of_either_kind(env):
    start_dispatch(env.engine, 3, "m", None)
    with pytest.raises(RunActive):
        start_evaluation(env.engine, env.ref, "evaluation", True, 5, "m", None)


def test_an_unknown_set_is_refused(env):
    with pytest.raises(ValueError):
        start_evaluation(env.engine, env.ref, "harder", False, 5, "m", None)


def test_service_starts_an_evaluation_and_refuses_one_during_a_dispatch(db_path, data_dir):
    settings = Settings(_env_file=None, agent_provider="scripted", db_path=db_path, data_dir=data_dir)
    with service_client(create_service(settings, inline=True)) as service:
        body = {"eval_set": "held_out", "tiny": True, "workers": 5}
        assert service.invoke({"operation": "start_evaluation", **body}).json()["accepted"] is True
        raw = RawDb(make_engine(settings, max_connections=1))
        assert tuple(raw.execute("SELECT kind, status, eval_set FROM runs").fetchone()) == ("evaluation", "finished", "held_out")
        raw.execute("INSERT INTO runs (kind, status, workers, model, reasoning_effort) VALUES ('dispatch', 'running', 3, 'm', 'none')")
        assert "already running" in service.invoke({"operation": "start_evaluation", **body}).json()["refused"]
        assert service.invoke({"operation": "start_evaluation", **{**body, "eval_set": "harder"}}).status_code == 400


def test_backend_forwards_an_evaluation_and_hides_copies(db_path, data_dir):
    settings = Settings(_env_file=None, agent_provider="scripted", db_path=db_path, data_dir=data_dir)
    with service_client(create_service(settings, inline=True)) as service:
        with api_client(create_app(settings, http=service, jwks=MINT), headers=MINT.header()) as backend:
            body = {"eval_set": "evaluation", "tiny": False, "workers": 5}
            assert backend.post("/evaluations", json=body).status_code == 202
            assert len(backend.get("/claims").json()) == 26
            assert backend.get("/approvals").json() == []
            detail = backend.get("/claims/E1-CLM-1003").json()
            assert (detail["copy_of"], detail["evaluation_id"]) == ("CLM-1003", 1)
            assert backend.post("/payments/PAY-E1-CLM-1003/approve", ).status_code == 403
