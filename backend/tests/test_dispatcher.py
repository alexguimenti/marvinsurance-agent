"""The dispatcher: a graph with no model that works a run's claims, up to N at once, and puts problems right."""
import pytest
from langchain_core.exceptions import ModelRateLimitError

import agent.dispatcher as dispatcher_module
from agent.dispatcher import CLAIM_RETRY, dispatch
from agent.scripted import ScriptedChatModel
from agent.tracing import trace_config
from app.claims import get_claim
from app.runs import (RunActive, claims_to_dispatch, get_run, run_claims_view, run_summary, start_dispatch,
                      stop_running)
from tests.fakes import RawDb, agent_env, approve, counting_stub, credit_error, decline, stub, tool_call

SEARCH = tool_call("search_documents", {"query": "delay"}, "s1")
FAST_RETRY = CLAIM_RETRY._replace(initial_interval=0.01, max_interval=0.01, jitter=False)
ALL = [f"CLM-{n}" for n in range(1001, 1026)]


@pytest.fixture
def env_for(db_path, data_dir):
    opened = []

    def make(model):
        cm = agent_env(db_path, data_dir, model)
        env = cm.__enter__()
        opened.append(cm)
        return env

    yield make
    for cm in reversed(opened):
        cm.__exit__(None, None, None)


def run(env, workers=3):
    run_id = start_dispatch(env.engine, workers, env.model_id, "low")
    dispatch(env, run_id, workers, retry=FAST_RETRY)
    return run_id


def claim(env, claim_id):
    return get_claim(env.engine, claim_id)


def decisions(env, claim_id):
    return RawDb(env.engine).execute("SELECT COUNT(*) FROM decisions WHERE claim_id = ?", (claim_id,)).fetchone()[0]


def test_never_more_than_n_claims_at_once(env_for):
    model = counting_stub(seconds=0.05, default=decline())  # approve(100) is more than some claims ask for
    env = env_for(model)
    run_id = run(env, workers=3)
    assert 1 < model.peak <= 3
    assert sorted(r["claim_id"] for r in run_claims_view(env.engine, run_id)) == ALL
    assert all(claim(env, c)["status"] in ("decided", "awaiting_approval") for c in ALL)
    assert get_run(env.engine, run_id)["status"] == "finished"


def test_oldest_submitted_first_and_claims_in_progress_first(env_for):
    env = env_for(stub(default=decline()))
    raw = RawDb(env.engine)
    raw.execute("UPDATE claims SET status='in_progress', thread_id='CLM-1020-1' WHERE claim_id='CLM-1020'")
    order = claims_to_dispatch(env.engine)
    submitted = {r["claim_id"]: r["submitted_on"] for r in raw.execute("SELECT claim_id, submitted_on FROM claims")}
    assert order[0] == "CLM-1020"
    rest = order[1:]
    assert [submitted[c] for c in rest] == sorted(submitted[c] for c in rest)
    assert len(order) == 25


def test_a_rate_limit_carries_on_as_the_same_attempt_with_one_decision(env_for):
    env = env_for(stub({"CLM-1001": [[SEARCH, ModelRateLimitError("429"), approve(100.0)]]}, default=decline()))
    run(env)
    row = claim(env, "CLM-1001")
    assert (row["status"], row["attempts"], row["thread_id"]) == ("decided", 0, "CLM-1001-1")
    assert decisions(env, "CLM-1001") == 1


def test_retries_run_out_and_the_claim_waits_for_the_next_dispatch(env_for):
    limits = [ModelRateLimitError("429") for _ in range(6)]
    env = env_for(stub({"CLM-1001": [[SEARCH, *limits, approve(100.0)]]}, default=decline()))
    run_id = run(env)
    assert claim(env, "CLM-1001")["status"] == "in_progress"
    assert get_run(env.engine, run_id)["status"] == "finished"
    assert run_summary(env.engine, run_id)["in_progress"] == ["CLM-1001"]
    assert claim(env, "CLM-1002")["status"] == "decided"  # the others were worked all the same


def test_a_failed_claim_is_tried_once_more_in_the_same_dispatch(env_for):
    env = env_for(stub({"CLM-1001": [RuntimeError("broken"), approve(100.0)]}, default=decline()))
    run(env)
    row = claim(env, "CLM-1001")
    assert (row["status"], row["attempts"], row["thread_id"]) == ("decided", 1, "CLM-1001-2")


def test_a_claim_that_fails_twice_is_left_for_a_handler(env_for):
    env = env_for(stub({"CLM-1001": [RuntimeError("one"), RuntimeError("two")]}, default=decline()))
    run_id = run(env)
    assert (claim(env, "CLM-1001")["status"], claim(env, "CLM-1001")["attempts"]) == ("needs_handler", 2)
    assert run_summary(env.engine, run_id)["left_for_handler"] == ["CLM-1001"]


def test_a_rate_limit_on_the_second_try_counts_the_attempt_once(env_for):
    env = env_for(stub({"CLM-1001": [RuntimeError("one"), [ModelRateLimitError("429"), approve(100.0)]]}, default=decline()))
    run(env)
    row = claim(env, "CLM-1001")
    assert (row["status"], row["attempts"]) == ("decided", 1)


def test_one_bad_claim_never_stops_the_dispatch(env_for, monkeypatch):
    env = env_for(stub(default=decline()))
    real = dispatcher_module.work

    def work(env, claim_id, run_id):
        if claim_id == "CLM-1001":
            raise ValueError("the harness broke")
        return real(env, claim_id, run_id)

    monkeypatch.setattr(dispatcher_module, "work", work)
    run_id = run(env)
    assert get_run(env.engine, run_id)["status"] == "finished"
    assert claim(env, "CLM-1002")["status"] == "decided"
    view = {r["claim_id"]: r for r in run_claims_view(env.engine, run_id)}
    assert view["CLM-1001"]["finished_at"] is not None


def test_a_claim_being_resumed_is_skipped(env_for):
    env = env_for(stub(default=decline()))
    with env.working("CLM-1003"):
        run_id = run(env)
    assert "CLM-1003" not in {r["claim_id"] for r in run_claims_view(env.engine, run_id)}
    assert claim(env, "CLM-1003")["status"] == "waiting"


def test_a_second_start_is_refused_while_one_runs(env_for):
    env = env_for(stub(default=decline()))
    start_dispatch(env.engine, 3, "stub", "low")
    with pytest.raises(RunActive):
        start_dispatch(env.engine, 3, "stub", "low")


def test_stop_running_marks_a_run_stopped_and_frees_the_next(env_for):
    env = env_for(stub(default=decline()))
    run_id = start_dispatch(env.engine, 3, "stub", "low")
    assert stop_running(env.engine) == [run_id]
    assert get_run(env.engine, run_id)["status"] == "stopped"
    start_dispatch(env.engine, 3, "stub", "low")


def test_the_next_dispatch_carries_on_what_a_stopped_one_left(env_for):
    env = env_for(stub({"CLM-1001": [[SEARCH, decline()]]}, default=decline()))
    RawDb(env.engine).execute("UPDATE claims SET status='decided' WHERE claim_id != 'CLM-1001'")
    from tests.fakes import Stopped

    env.model.outcomes["CLM-1001"] = [[SEARCH, Stopped(), decline()]]
    first = start_dispatch(env.engine, 3, "stub", "low")
    with pytest.raises(Stopped):
        dispatch(env, first, 3, retry=FAST_RETRY)
    stop_running(env.engine)
    run(env)
    row = claim(env, "CLM-1001")
    assert (row["status"], row["attempts"], row["thread_id"]) == ("decided", 0, "CLM-1001-1")


def test_the_summary_is_built_from_the_records(env_for):
    env = env_for(ScriptedChatModel())
    run_id = run(env, workers=25)
    summary = run_summary(env.engine, run_id)
    got = {r[0]: r[1] for r in RawDb(env.engine).execute("SELECT decision, COUNT(*) FROM decisions GROUP BY decision")}
    assert (summary["approved"], summary["declined"], summary["referred"]) == (got["approve"], got["decline"], got["refer"])
    assert [p["claim_id"] for p in summary["awaiting_sign_off"]] == ["CLM-1003", "CLM-1008", "CLM-1011"]
    assert summary["awaiting_sign_off"][0]["payout"] == 3750.0
    assert summary["left_for_handler"] == ["CLM-1013"]
    assert summary["in_progress"] == []


def test_each_claims_trace_carries_its_runs_tag():
    assert trace_config("CLM-1001", "CLM-1001-1", "m", 7)["metadata"]["langfuse_tags"] == ["CLM-1001", "m", "run-7"]
    assert trace_config("CLM-1001", "CLM-1001-1", "m", None)["metadata"]["langfuse_tags"] == ["CLM-1001", "m"]


def test_a_key_out_of_credit_stops_the_run_and_blames_no_claim(env_for):
    model = stub(default=credit_error())
    env = env_for(model)
    run_id = run(env, workers=1)
    row = get_run(env.engine, run_id)
    assert row["status"] == "stopped" and "credit" in row["error"]
    assert model.asked == ["CLM-1001"]  # the rest were not started: each would only meet the same refusal
    assert {claim(env, c)["status"] for c in ALL} == {"in_progress", "waiting"}  # no claim is left for a handler
    assert all(claim(env, c)["attempts"] == 0 for c in ALL)


def test_the_next_run_starts_clear_of_the_last_one_s_credit_trouble(env_for):
    env = env_for(stub(default=credit_error()))
    run(env, workers=1)
    assert env.out_of_credit.is_set()
    env.model.default = decline()
    run_id = run(env, workers=3)
    assert get_run(env.engine, run_id)["status"] == "finished"
