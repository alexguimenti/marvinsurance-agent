import json

import pytest

from agent.checkpoints import clear_checkpoints
from langchain_core.exceptions import ModelRateLimitError, ModelTimeoutError

from agent.worker import carry_on, signed_off_claims, stopped_claims, work, work_claim
from app.claims import get_claim
from app.config import Settings
from app.payments import get_payment, sign_off
from app.reference import Reference
from app.seed import reseed
from tests.fakes import RawDb, Stopped, agent_env, approve, credit_error, decline, refer, stub, tool_call

CLAIM = "CLM-1003"
SEARCH = tool_call("search_documents", {"query": "delay"}, "s1")
SEARCH2 = tool_call("search_documents", {"query": "meals"}, "s2")


@pytest.fixture
def env_for(db_path, data_dir):
    opened = []

    def make(outcomes=None, **settings):
        model = stub(outcomes)
        cm = agent_env(db_path, data_dir, model, **settings)
        env = cm.__enter__()
        opened.append(cm)
        return env, model

    yield make
    for cm in reversed(opened):
        cm.__exit__(None, None, None)


def raw(env):
    return RawDb(env.engine)


def claim(env, cid=CLAIM):
    return get_claim(env.engine, cid)


def kinds(env):
    return [r["kind"] for r in raw(env).execute("SELECT kind FROM trace_events ORDER BY seq")]


def count(env, table):
    return raw(env).execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_approve_under_limit_is_paid_and_decided(env_for):
    env, _ = env_for({CLAIM: [approve(420.0)]})
    assert work_claim(env, CLAIM) == "CLM-1003: approve $420.00, paid"
    row = claim(env)
    assert row["status"] == "decided" and row["thread_id"] == "CLM-1003-1"
    assert (row["decision"]["manual_version"], row["decision"]["wording_version"]) == ("2026.2", "2026.1")
    assert row["decision"]["model"] == "stub/model"


def test_over_limit_is_held(env_for):
    env, _ = env_for({CLAIM: [approve(3750.0)]})
    assert work_claim(env, CLAIM) == "CLM-1003: approve $3,750.00, held for sign-off"
    assert claim(env)["status"] == "awaiting_approval"
    assert get_payment(env.engine, CLAIM).status == "held"


@pytest.mark.parametrize("outcome,word", [(decline(), "decline"), (refer(), "refer")])
def test_decline_and_refer_are_decided_without_payment(env_for, outcome, word):
    env, _ = env_for({CLAIM: [outcome]})
    assert work_claim(env, CLAIM) == f"CLM-1003: {word}"
    assert claim(env)["status"] == "decided" and count(env, "payments") == 0


def test_first_failure_goes_back_to_waiting(env_for):
    env, _ = env_for({CLAIM: [ValueError("no decision")]})
    line = work_claim(env, CLAIM)
    row = claim(env)
    assert (row["status"], row["attempts"], row["thread_id"]) == ("waiting", 1, None)
    assert "no decision" in row["last_error"]
    assert line.startswith("CLM-1003: attempt 1 of 2 failed (ValueError: no decision)") and "back in the queue" in line
    assert count(env, "decisions") == 0


def test_two_failures_leave_it_for_a_handler(env_for):
    env, _ = env_for({CLAIM: [ValueError("one"), ValueError("two")]})
    work_claim(env, CLAIM)
    line = work_claim(env, CLAIM)
    row = claim(env)
    assert (row["status"], row["attempts"]) == ("needs_handler", 2) and "Left for a handler" in line
    assert count(env, "decisions") == 0 and count(env, "payments") == 0


def test_claim_that_errors_starts_afresh_on_a_new_thread(env_for):
    env, model = env_for({CLAIM: [[SEARCH, ValueError("boom")], approve(420.0)]})
    work_claim(env, CLAIM)
    assert work_claim(env, CLAIM) == "CLM-1003: approve $420.00, paid"
    row = claim(env)
    assert (row["thread_id"], row["attempts"]) == ("CLM-1003-2", 1)
    second_try = model.shown[-1]
    assert not any(m.type == "tool" and m.name == "search_documents" for m in second_try)  # nothing carried over


def test_stopped_claim_carries_on_from_its_last_step(db_path, data_dir):
    model = stub({CLAIM: [[SEARCH, SEARCH2, Stopped(), approve(420.0)]]})
    with agent_env(db_path, data_dir, model) as env:
        with pytest.raises(Stopped):
            work_claim(env, CLAIM)
    assert model.calls == 3

    # a new process: new connections, a new saver over the same checkpoints
    with agent_env(db_path, data_dir, model) as fresh:
        row = claim(fresh)
        assert (row["status"], row["thread_id"], row["attempts"]) == ("in_progress", "CLM-1003-1", 0)
        assert stopped_claims(fresh.engine) == [CLAIM]
        assert carry_on(fresh, CLAIM) == "CLM-1003: approve $420.00, paid"
        row = claim(fresh)
        assert (row["status"], row["thread_id"], row["attempts"]) == ("decided", "CLM-1003-1", 0)
        assert count(fresh, "decisions") == 1 and count(fresh, "payments") == 1
    assert model.calls == 4  # the two completed searches were not made again
    tool_runs = [m for m in model.shown[-1] if m.type == "tool" and m.name == "search_documents"]
    assert len(tool_runs) == 2


def test_killed_before_the_first_checkpoint_starts_on_the_same_thread(env_for):
    env, _ = env_for({CLAIM: [approve(420.0)]})
    raw(env).execute("UPDATE claims SET status='in_progress', thread_id='CLM-1003-1' WHERE claim_id=?", (CLAIM,))
    assert carry_on(env, CLAIM) == "CLM-1003: approve $420.00, paid"
    row = claim(env)
    assert (row["thread_id"], row["attempts"]) == ("CLM-1003-1", 0)


def test_carrying_on_a_finished_thread_reports_from_the_records(env_for):
    env, model = env_for({CLAIM: [approve(420.0)]})
    work_claim(env, CLAIM)
    raw(env).execute("UPDATE claims SET status='in_progress' WHERE claim_id=?", (CLAIM,))
    calls = model.calls
    assert carry_on(env, CLAIM).startswith("CLM-1003: approve $420.00")
    assert model.calls == calls


def test_error_after_the_decision_is_carried_on_not_retried(env_for, monkeypatch):
    import agent.graph as graph_module

    env, model = env_for({CLAIM: [approve(420.0)]})
    real = graph_module.pay
    failures = [RuntimeError("the database went away")]

    def flaky(*args):
        if failures:
            raise failures.pop()
        return real(*args)

    monkeypatch.setattr(graph_module, "pay", flaky)
    line = work_claim(env, CLAIM)
    row = claim(env)
    assert (row["status"], row["attempts"], row["thread_id"]) == ("in_progress", 0, "CLM-1003-1")
    assert "next run" in line and row["decision"]["payout"] == 420.0
    assert stopped_claims(env.engine) == [CLAIM]
    calls = model.calls

    assert carry_on(env, CLAIM) == "CLM-1003: approve $420.00, paid"
    row = claim(env)
    assert (row["status"], row["attempts"]) == ("decided", 0) and model.calls == calls
    assert count(env, "decisions") == 1 and count(env, "payments") == 1


def test_repeated_decide_step_after_a_stop_counts_as_done(env_for, monkeypatch):
    import agent.graph as graph_module

    env, _ = env_for({CLAIM: [approve(420.0)]})
    real = graph_module.pay
    stops = [Stopped()]

    def stopping(*args):
        if stops:
            raise stops.pop()
        return real(*args)

    monkeypatch.setattr(graph_module, "pay", stopping)
    with pytest.raises(Stopped):
        work_claim(env, CLAIM)
    assert carry_on(env, CLAIM) == "CLM-1003: approve $420.00, paid"
    assert count(env, "decisions") == 1 and count(env, "payments") == 1


def test_sign_off_then_carrying_on_pays_once(env_for):
    env, model = env_for({CLAIM: [approve(2000.0)]})
    work_claim(env, CLAIM)
    calls = model.calls
    assert signed_off_claims(env.engine) == []
    sign_off(env.engine, env.ref, f"PAY-{CLAIM}", "HND-01")
    assert signed_off_claims(env.engine) == [CLAIM]
    assert claim(env)["status"] == "in_progress"
    assert carry_on(env, CLAIM) == "CLM-1003: approve $2,000.00, paid"
    assert carry_on(env, CLAIM).startswith("CLM-1003: not worked")
    assert model.calls == calls and count(env, "payments") == 1
    assert signed_off_claims(env.engine) == []
    payment = get_payment(env.engine, CLAIM)
    assert payment.approved_by == "HND-01" and payment.paid_at


def test_a_claim_on_hold_is_not_carried_on_before_its_sign_off(env_for):
    env, _ = env_for({CLAIM: [approve(2000.0)]})
    work_claim(env, CLAIM)
    assert stopped_claims(env.engine) == []
    assert carry_on(env, CLAIM).startswith("CLM-1003: not worked")
    assert get_payment(env.engine, CLAIM).status == "held"


def test_sign_off_during_the_pause_is_not_left_waiting(env_for, monkeypatch):
    """The handler signs while the graph is still on its way to the pause: the claim is paid, not left waiting."""
    import agent.worker as worker_module

    env, _ = env_for({CLAIM: [approve(2000.0)]})
    real = worker_module.invoke

    def sign_then_return(env_, *args):
        result = real(env_, *args)
        if get_payment(env_.engine, CLAIM).status == "held":
            sign_off(env_.engine, env_.ref, f"PAY-{CLAIM}", "HND-01")
        return result

    monkeypatch.setattr(worker_module, "invoke", sign_then_return)
    assert work_claim(env, CLAIM) == "CLM-1003: approve $2,000.00, paid"
    assert claim(env)["status"] == "decided"


def test_after_the_pause_the_trace_goes_in_the_same_session(env_for, monkeypatch):
    import agent.worker as worker_module

    env, _ = env_for({CLAIM: [approve(2000.0)]})
    sessions = []
    real = worker_module.trace_config

    def spy(claim_id, thread_id, model, run_id, round_id=None):
        sessions.append((claim_id, thread_id))
        return real(claim_id, thread_id, model, run_id, round_id)

    monkeypatch.setattr(worker_module, "trace_config", spy)
    work_claim(env, CLAIM)
    sign_off(env.engine, env.ref, f"PAY-{CLAIM}", "HND-01")
    carry_on(env, CLAIM)
    assert set(sessions) == {(CLAIM, "CLM-1003-1")} and len(sessions) >= 2


def test_decided_claim_is_not_worked_again(env_for):
    env, model = env_for({CLAIM: [approve(420.0)]})
    work_claim(env, CLAIM)
    assert work_claim(env, CLAIM) == "CLM-1003: not worked, the claim is decided."
    assert model.asked == [CLAIM] and claim(env)["attempts"] == 0


def test_unknown_claim_is_not_worked(env_for):
    env, model = env_for()
    assert work_claim(env, "CLM-9999") == "CLM-9999: not worked, there is no such claim."
    assert model.asked == []


def test_trace_events_carry_the_run_id(env_for):
    env, _ = env_for()
    raw(env).execute("INSERT INTO runs (kind, status, workers, model, reasoning_effort) VALUES ('dispatch', 'running', 3, 'm', 'none')")
    work_claim(env, CLAIM, run_id=1)
    assert {r[0] for r in raw(env).execute("SELECT run_id FROM trace_events")} == {1}
    assert claim(env)["run_id"] == 1


def test_trace_seq_keeps_counting_across_attempts(env_for):
    env, _ = env_for({CLAIM: [ValueError("one"), approve(420.0)]})
    work_claim(env, CLAIM)
    work_claim(env, CLAIM)
    seqs = [r[0] for r in raw(env).execute("SELECT seq FROM trace_events ORDER BY id")]
    assert seqs == list(range(1, len(seqs) + 1))


def test_each_attempt_records_its_usage(env_for):
    env, _ = env_for({CLAIM: [[SEARCH, ValueError("one")], approve(420.0)]})
    work_claim(env, CLAIM)
    work_claim(env, CLAIM)
    calls = raw(env).execute("SELECT COUNT(*) FROM model_calls WHERE claim_id = ?", (CLAIM,)).fetchone()[0]
    assert calls == 2  # one from the failed attempt, one from the decision


def test_the_error_event_says_which_attempt_failed(env_for):
    env, _ = env_for({CLAIM: [ValueError("no decision")]})
    work_claim(env, CLAIM)
    error = json.loads(raw(env).execute("SELECT content FROM trace_events WHERE kind='error'").fetchone()[0])
    assert error["message"].startswith("Attempt 1 of 2 failed (ValueError: no decision)")


def test_max_attempts_one_leaves_it_for_a_handler_at_once(env_for):
    env, _ = env_for({CLAIM: [ValueError("x")]})
    work_claim(env, CLAIM, max_attempts=1)
    assert claim(env)["status"] == "needs_handler"


def test_reseed_leaves_no_checkpoint_behind(env_for, data_dir):
    env, model = env_for({CLAIM: [approve(2000.0), decline()]})
    work_claim(env, CLAIM)
    assert claim(env)["status"] == "awaiting_approval"

    reseed(env.engine, Reference(data_dir))
    clear_checkpoints(env.settings)

    assert env.graph.get_state({"configurable": {"thread_id": "CLM-1003-1"}}).values == {}
    assert work_claim(env, CLAIM) == "CLM-1003: decline"  # worked from nothing, on the same thread name
    assert count(env, "payments") == 0


def test_a_claim_worked_inside_another_traced_call_starts_its_own_trace(env_for, monkeypatch):
    """Langfuse takes the session from a trace's root. A claim is worked inside a tool of the queue agent, and it
    must not hang under that: the work before a pause would go missing from the claim's session."""
    from langchain_core.callbacks import BaseCallbackHandler
    from langchain_core.runnables import RunnableLambda

    import agent.worker as worker_module

    roots = []

    class Recorder(BaseCallbackHandler):
        def on_chain_start(self, serialized, inputs, *, run_id, parent_run_id=None, metadata=None, **kwargs):
            roots.append(parent_run_id)

    real = worker_module.trace_config
    monkeypatch.setattr(worker_module, "trace_config",
                        lambda *args: {**real(*args), "callbacks": [Recorder()]})
    env, _ = env_for({CLAIM: [decline()]})

    RunnableLambda(lambda _: work_claim(env, CLAIM)).invoke("the queue agent's tool")

    assert roots[0] is None


def test_a_sign_off_stopped_between_its_two_steps_is_still_paid(env_for):
    """The payment says signed off, but the process died before the claim moved on, or a hold landed just after."""
    env, _ = env_for({CLAIM: [approve(2000.0)]})
    work_claim(env, CLAIM)
    raw(env).execute("UPDATE payments SET status='signed_off', approved_by='HND-01', approved_at=CURRENT_TIMESTAMP")
    assert claim(env)["status"] == "awaiting_approval"
    assert signed_off_claims(env.engine) == [CLAIM]
    assert carry_on(env, CLAIM) == "CLM-1003: approve $2,000.00, paid"
    assert claim(env)["status"] == "decided"
    assert signed_off_claims(env.engine) == []


# --- the one function the dispatcher calls ----------------------------------------------------------

def test_work_starts_a_waiting_claim(env_for):
    env, _ = env_for({CLAIM: [approve(420.0)]})
    assert work(env, CLAIM, 7) == "CLM-1003: approve $420.00, paid"
    assert claim(env)["run_id"] == 7


def test_work_carries_on_a_claim_in_progress_as_the_same_attempt(env_for):
    env, model = env_for({CLAIM: [[SEARCH, Stopped(), decline()]]})
    with pytest.raises(Stopped):
        work(env, CLAIM, 7)
    assert work(env, CLAIM, 8) == "CLM-1003: decline"
    row = claim(env)
    assert (row["attempts"], row["thread_id"]) == (0, "CLM-1003-1")
    assert count(env, "decisions") == 1


def test_work_leaves_any_other_claim_alone(env_for):
    env, model = env_for({CLAIM: [decline()]})
    work(env, CLAIM, 7)
    assert work(env, CLAIM, 8) == "CLM-1003: not worked, the claim is decided."
    assert model.calls == 1


def test_a_rate_limit_leaves_the_claim_in_progress_on_its_thread(env_for):
    env, _ = env_for({CLAIM: [[SEARCH, ModelRateLimitError("429"), decline()]]})
    with pytest.raises(ModelRateLimitError):
        work(env, CLAIM, 7)
    row = claim(env)
    assert (row["status"], row["attempts"], row["thread_id"]) == ("in_progress", 0, "CLM-1003-1")
    assert "will carry on" in json.loads(raw(env).execute(
        "SELECT content FROM trace_events WHERE kind='error' ORDER BY seq DESC").fetchone()[0])["message"]
    assert work(env, CLAIM, 7) == "CLM-1003: decline"  # the retry carries on from the last saved step
    assert (claim(env)["attempts"], count(env, "decisions")) == (0, 1)


def test_a_timeout_is_let_through_the_same_way(env_for):
    env, _ = env_for({CLAIM: [[ModelTimeoutError("slow"), decline()]]})
    with pytest.raises(ModelTimeoutError):
        work(env, CLAIM, 7)
    assert (claim(env)["status"], claim(env)["attempts"]) == ("in_progress", 0)


def test_any_other_error_still_fails_the_attempt(env_for):
    env, _ = env_for({CLAIM: [RuntimeError("broken")]})
    assert "back in the queue" in work(env, CLAIM, 7)
    assert (claim(env)["status"], claim(env)["attempts"]) == ("waiting", 1)


def test_a_key_out_of_credit_is_not_the_claims_failure(env_for):
    env, _ = env_for({CLAIM: [credit_error()]})
    line = work_claim(env, CLAIM)
    row = claim(env)
    assert (row["status"], row["attempts"], row["last_error"]) == ("in_progress", 0, None)  # not left for a handler
    assert "credit" in line and env.out_of_credit.is_set()
