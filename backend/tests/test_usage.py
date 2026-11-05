"""Cost and time for every claim, recorded with the run they happened in."""
import pytest

from agent.worker import carry_on, work_claim
from app.payments import sign_off
from app.usage import claim_totals, per_claim, run_totals
from tests.fakes import FakeClock, RawDb, agent_env, approve, stub, tool_call

SEARCH = tool_call("search_documents", {"query": "delay"}, "s1")


@pytest.fixture
def env_for(db_path, data_dir):
    opened = []

    def make(outcomes=None):
        model = stub(outcomes)
        cm = agent_env(db_path, data_dir, model)
        env = cm.__enter__()
        opened.append(cm)
        return env, model

    yield make
    for cm in reversed(opened):
        cm.__exit__(None, None, None)


def model_calls(env, claim_id):
    return list(RawDb(env.engine).execute(
        "SELECT run_id, model, input_tokens, output_tokens, reasoning_tokens, cost_usd FROM model_calls "
        "WHERE claim_id = ? ORDER BY id", (claim_id,)))


def test_each_model_call_is_recorded_with_its_run(env_for):
    env, model = env_for({"CLM-1001": [[SEARCH, approve(100.0)]]})
    work_claim(env, "CLM-1001", run_id=7)
    calls = model_calls(env, "CLM-1001")
    assert len(calls) == model.calls == 2
    assert {c["run_id"] for c in calls} == {7} and {c["model"] for c in calls} == {"stub/model"}
    assert (calls[0]["input_tokens"], calls[0]["output_tokens"], calls[0]["reasoning_tokens"]) == (50, 5, 2)
    totals = run_totals(env.engine, 7)
    assert totals["calls"] == 2 and totals["cost_usd"] == pytest.approx(0.001)


def test_a_failed_attempt_counts_in_its_runs_cost(env_for):
    env, _ = env_for({"CLM-1001": [[SEARCH, RuntimeError("the provider broke")]]})
    work_claim(env, "CLM-1001", run_id=7)
    assert run_totals(env.engine, 7)["calls"] == 1
    assert run_totals(env.engine, 7)["cost_usd"] == pytest.approx(0.0005)


def test_work_outside_a_run_is_recorded_with_no_run(env_for):
    env, _ = env_for({"CLM-1003": [approve(3750.0)]})
    work_claim(env, "CLM-1003", run_id=7)
    sign_off(env.engine, env.ref, "PAY-CLM-1003", "HND-01")
    carry_on(env, "CLM-1003")  # after a sign-off, with no run
    runs = {c["run_id"] for c in model_calls(env, "CLM-1003")}
    assert runs == {7}  # paying needs no model call
    spans = list(RawDb(env.engine).execute("SELECT run_id FROM work_spans WHERE claim_id = 'CLM-1003' ORDER BY id"))
    assert [s["run_id"] for s in spans] == [7, None]


def test_time_leaves_out_the_wait_for_a_sign_off(env_for):
    env, _ = env_for({"CLM-1003": [approve(3750.0)]})
    env.clock = FakeClock(step=2.0)  # every reading moves on 2 s
    work_claim(env, "CLM-1003", run_id=7)  # pauses for the sign-off: one graph run
    env.clock.jump(86_400)  # a day waiting for the handler
    sign_off(env.engine, env.ref, "PAY-CLM-1003", "HND-01")
    carry_on(env, "CLM-1003")  # the second graph run, with no run
    assert claim_totals(env.engine, "CLM-1003")["seconds"] == 4.0
    assert run_totals(env.engine, 7)["seconds"] == 2.0


def test_per_claim_totals_of_a_run(env_for):
    env, _ = env_for({"CLM-1001": [approve(100.0)], "CLM-1002": [[SEARCH, approve(200.0)]]})
    env.clock = FakeClock(step=1.0)
    work_claim(env, "CLM-1001", run_id=7)
    work_claim(env, "CLM-1002", run_id=7)
    totals = per_claim(env.engine, 7)
    assert (totals["CLM-1001"]["calls"], totals["CLM-1002"]["calls"]) == (1, 2)
    assert totals["CLM-1001"]["seconds"] == 1.0


def test_a_claim_with_nothing_recorded_has_empty_totals(env_for):
    env, _ = env_for()
    assert claim_totals(env.engine, "CLM-1001") == {
        "calls": 0, "input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0, "cost_usd": None, "seconds": 0.0,
    }
