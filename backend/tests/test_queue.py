import json
import re
from datetime import datetime, timedelta, timezone

import pytest
from langchain_core.messages import AIMessage

from agent.prompts import load_instructions
from agent.queue import LangChainQueueAgent, begin_run, build_run_tools, run_harness, work_run
from agent.scripted import ScriptedQueueAgent
from agent.worker import work_claim
from app import runs
from app.claims import get_claim
from app.payments import get_payment, sign_off
from tests.fakes import RawDb, Stopped, agent_env, approve, decline, script, stub, tool_call

FIVE = ["CLM-1001", "CLM-1002", "CLM-1004", "CLM-1006", "CLM-1007"]


@pytest.fixture
def env_for(db_path, data_dir):
    opened = []

    def make(outcomes=None, default=None, **settings):
        model = stub(outcomes, default)
        cm = agent_env(db_path, data_dir, model, **settings)
        env = cm.__enter__()
        opened.append(cm)
        return env, model

    yield make
    for cm in reversed(opened):
        cm.__exit__(None, None, None)


def statuses(env, ids=None):
    rows = RawDb(env.engine).execute("SELECT claim_id, status FROM claims ORDER BY claim_id").fetchall()
    return {r[0]: r[1] for r in rows if ids is None or r[0] in ids}


def scripted_run(env, cap=None, scope=None):
    run_id = runs.start_run(env.engine, "scripted", cap=cap, scope=scope)
    run_harness(env, run_id, ScriptedQueueAgent())
    return run_id


def tools(env, run_id):
    return {t.name: t for t in build_run_tools(env, run_id)}


def test_scripted_run_with_cap_works_that_many_and_leaves_the_rest(env_for):
    env, _ = env_for(default=approve(100.0))
    run_id = scripted_run(env, cap=5)
    assert list(statuses(env).values()).count("waiting") == 20
    row = runs.get_run(env.engine, run_id)
    assert row["status"] == "finished" and row["summary"]
    assert [i["status"] for i in runs.todo_list(env.engine, run_id)] == ["done"] * 5


def test_run_on_one_named_claim(env_for):
    env, _ = env_for()
    scripted_run(env, scope=["CLM-1004"])
    assert [cid for cid, s in statuses(env).items() if s != "waiting"] == ["CLM-1004"]


def test_failed_claim_does_not_stop_the_run(env_for):
    env, _ = env_for({"CLM-1002": [RuntimeError("down"), RuntimeError("down")]})
    run_id = scripted_run(env, scope=FIVE)
    s = statuses(env, FIVE)
    assert s.pop("CLM-1002") == "needs_handler"
    assert set(s.values()) == {"decided"}
    assert runs.get_run(env.engine, run_id)["status"] == "finished"


def test_claim_failing_once_is_retried_in_the_same_run_and_decided(env_for):
    env, model = env_for({"CLM-1002": [RuntimeError("blip")]})
    run_id = scripted_run(env, scope=FIVE)
    assert statuses(env, ["CLM-1002"]) == {"CLM-1002": "decided"}
    assert model.asked.count("CLM-1002") == 2 and model.asked[-1] == "CLM-1002"  # retried after the others
    assert runs.get_run(env.engine, run_id)["status"] == "finished"


def test_work_claim_tool_refuses_past_the_cap_and_outside_scope(env_for):
    env, model = env_for()
    run_id = runs.start_run(env.engine, "m", cap=1, scope=["CLM-1001", "CLM-1002"])
    t = tools(env, run_id)
    assert t["work_claim"].invoke({"claim_id": "CLM-1004"}) == "CLM-1004: not worked, it is not part of this run."
    assert t["work_claim"].invoke({"claim_id": "CLM-1001"}).startswith("CLM-1001: approve")
    assert t["work_claim"].invoke({"claim_id": "CLM-1002"}) == "CLM-1002: not worked, the run's cap of 1 claims is reached."
    assert model.asked == ["CLM-1001"]


def test_list_waiting_claims_shows_scope_cap_and_taken(env_for):
    env, _ = env_for()
    run_id = runs.start_run(env.engine, "m", cap=1, scope=["CLM-1001", "CLM-1002"])
    listed = json.loads(tools(env, run_id)["list_waiting_claims"].invoke({}))
    assert (listed["cap"], listed["taken"]) == (1, 0)
    assert [c["claim_id"] for c in listed["claims"]] == ["CLM-1001", "CLM-1002"]
    assert {"claim_type", "summary", "amount_claimed", "attempts"} <= set(listed["claims"][0])


def test_finish_run_refuses_while_claims_are_missed(env_for):
    env, _ = env_for()
    run_id = runs.start_run(env.engine, "m", scope=["CLM-1001"])
    t = tools(env, run_id)
    assert t["finish_run"].invoke({"summary": "all done"}) == "Not finished. Still to work: CLM-1001"
    t["work_claim"].invoke({"claim_id": "CLM-1001"})
    assert t["finish_run"].invoke({"summary": "all done"}) == "Run finished."
    assert runs.get_run(env.engine, run_id)["summary"] == "all done"


def test_todo_tools_return_errors_as_text(env_for):
    env, _ = env_for()
    run_id = runs.start_run(env.engine, "m")
    t = tools(env, run_id)
    planned = json.loads(t["set_todo"].invoke({"items": [{"title": "Work CLM-1001", "claim_id": "CLM-1001"}]}))
    assert planned[0]["status"] == "todo"
    assert t["update_todo"].invoke({"item_id": planned[0]["item_id"], "status": "done"}).startswith("Error:")
    assert t["update_todo"].invoke({"item_id": 999, "status": "todo"}).startswith("Error:")


def test_a_held_claim_is_done_on_the_list(env_for):
    env, _ = env_for({"CLM-1003": [approve(3750.0)]})
    run_id = scripted_run(env, scope=["CLM-1003"])
    assert runs.todo_list(env.engine, run_id)[0]["status"] == "done"
    assert runs.get_run(env.engine, run_id)["status"] == "finished"


def test_the_queue_agent_sees_only_one_line_reports(env_for):
    env, _ = env_for({"CLM-1001": [decline()]})
    boss = script(
        tool_call("work_claim", {"claim_id": "CLM-1001"}, "w1"),
        tool_call("finish_run", {"summary": "One claim declined."}, "f1"),
        AIMessage(content="Done."),
    )
    run_id = runs.start_run(env.engine, "fake/boss", scope=["CLM-1001"])
    run_harness(env, run_id, LangChainQueueAgent(boss, "fake/boss", 50))
    shown = boss.seen[-1]
    assert "CLM-1001: decline" in [m.content for m in shown if m.type == "tool"]
    assert not any("<claim_data>" in str(m.content) for m in shown)
    assert runs.get_run(env.engine, run_id)["status"] == "finished"


def test_each_claim_starts_from_a_clean_conversation(env_for):
    env, model = env_for(default=decline())
    scripted_run(env, scope=["CLM-1001", "CLM-1002"])
    second = model.shown[1]
    assert "CLM-1002" in second[1].content
    assert not any("CLM-1001" in str(m.content) for m in second)


class Exploding:
    model_id = "boom"
    usage = None

    def run(self, tools, on_event, resuming=False):
        {t.name: t for t in tools}["work_claim"].invoke({"claim_id": "CLM-1001"})
        raise RuntimeError("the queue agent crashed")


def test_queue_agent_crash_fails_the_run_and_keeps_what_was_decided(env_for):
    env, _ = env_for()
    run_id = runs.start_run(env.engine, "boom", scope=FIVE)
    run_harness(env, run_id, Exploding())
    row = runs.get_run(env.engine, run_id)
    assert row["status"] == "failed" and "crashed" in row["error"]
    assert statuses(env, ["CLM-1001"]) == {"CLM-1001": "decided"}


def test_queue_agent_stopping_early_records_missed_claims(env_for):
    env, _ = env_for()
    boss = script(AIMessage(content="I will stop here."))
    run_id = runs.start_run(env.engine, "fake/boss", scope=["CLM-1001", "CLM-1002"])
    run_harness(env, run_id, LangChainQueueAgent(boss, "fake/boss", 50))
    row = runs.get_run(env.engine, run_id)
    assert row["status"] == "failed" and "CLM-1001" in row["error"] and "CLM-1002" in row["error"]


class Thrifty(ScriptedQueueAgent):
    usage = {"calls": 1, "input_tokens": 100, "output_tokens": 10, "reasoning_tokens": 5, "cost_usd": 0.0005}


def test_run_events_record_the_queue_agents_steps(env_for):
    env, _ = env_for()
    run_id = scripted_run(env, scope=["CLM-1001"])
    kinds = [e["kind"] for e in runs.run_events(env.engine, run_id)]
    assert "tool_call" in kinds and "tool_result" in kinds


def test_run_instructions_quote_no_claim():
    text = load_instructions("run")
    assert "finish_run" in text and "set_todo" in text and "work_claim" in text
    assert not re.search(r"CLM-\d{4}", text)


# --- runs that were stopped ------------------------------------------------------

def test_finishing_a_run_leaves_a_signed_off_claim_in_progress(env_for):
    env, _ = env_for({"CLM-1003": [approve(3750.0)]})
    run_id = runs.start_run(env.engine, "m", scope=["CLM-1003"])
    work_claim(env, "CLM-1003", run_id)
    sign_off(env.engine, env.ref, "PAY-CLM-1003", "HND-01")
    runs.finish(env.engine, run_id, "finished")
    assert statuses(env, ["CLM-1003"]) == {"CLM-1003": "in_progress"}


def test_run_can_finish_while_a_signed_off_claim_is_being_paid(env_for):
    env, _ = env_for({"CLM-1003": [approve(3750.0)]})
    run_id = runs.start_run(env.engine, "m", scope=["CLM-1003"])
    work_claim(env, "CLM-1003", run_id)
    sign_off(env.engine, env.ref, "PAY-CLM-1003", "HND-01")
    assert runs.missed_claims(env.engine, run_id) == []


def test_in_progress_with_no_decision_is_still_owed(env_for):
    env, _ = env_for()
    run_id = runs.start_run(env.engine, "m", scope=["CLM-1001"])
    RawDb(env.engine).execute("UPDATE claims SET status='in_progress', run_id=? WHERE claim_id='CLM-1001'", (run_id,))
    assert runs.missed_claims(env.engine, run_id) == ["CLM-1001"]


def stopped_run(env, claim="CLM-1001", cap=None, scope=None):
    """A run whose process died partway through a claim."""
    run_id = runs.start_run(env.engine, env.model_id, cap, scope)
    with pytest.raises(Stopped):
        work_claim(env, claim, run_id)
    RawDb(env.engine).execute("UPDATE runs SET heartbeat_at = NULL WHERE run_id = ?", (run_id,))
    return run_id


def test_a_run_first_carries_on_stopped_and_signed_off_claims(env_for):
    search = tool_call("search_documents", {"query": "delay"}, "s1")
    env, model = env_for({"CLM-1001": [[search, Stopped(), approve(420.0)]], "CLM-1003": [approve(3750.0)]})
    work_claim(env, "CLM-1003")  # held; no run owns it any more
    sign_off(env.engine, env.ref, "PAY-CLM-1003", "HND-01")
    stopped_run(env, scope=["CLM-1001"])

    run_id, adopted = begin_run(env, 1, ["CLM-1002"])
    work_run(env, run_id, adopted, ScriptedQueueAgent())

    assert get_payment(env.engine, "CLM-1003").status == "paid"
    assert get_claim(env.engine, "CLM-1001")["status"] == "decided"
    assert get_claim(env.engine, "CLM-1001")["attempts"] == 0
    assert model.asked.count("CLM-1001") == 1


def test_stopped_run_is_finished_by_the_next_with_its_own_cap_and_scope(env_for):
    env, _ = env_for({"CLM-1001": [[Stopped(), approve(420.0)]]})
    run_id = stopped_run(env, cap=2, scope=["CLM-1001", "CLM-1002", "CLM-1004"])

    next_id, adopted = begin_run(env, 25, None)
    assert (next_id, adopted) == (run_id, True)
    work_run(env, next_id, adopted, ScriptedQueueAgent())

    row = runs.get_run(env.engine, run_id)
    assert row["status"] == "finished" and row["cap"] == 2
    assert "stopped earlier" in row["summary"]
    assert list(statuses(env).values()).count("decided") == 2  # the cap held, the new request's 25 did not apply
    assert RawDb(env.engine).execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1


def test_a_live_run_in_another_process_is_refused(env_for):
    env, _ = env_for()
    run_id = runs.start_run(env.engine, "m")
    with pytest.raises(runs.RunActive):
        begin_run(env, 1, None)
    assert runs.get_run(env.engine, run_id)["status"] == "running"


def test_a_dead_run_is_adopted(env_for):
    env, _ = env_for()
    run_id = runs.start_run(env.engine, "m")
    now = datetime.now(timezone.utc)
    assert runs.live_run(env.engine, now) is not None and runs.stale_run(env.engine, now) is None
    later = now + timedelta(seconds=runs.RUN_STALE_S + 1)
    assert runs.live_run(env.engine, later) is None and runs.stale_run(env.engine, later)["run_id"] == run_id


def test_stopping_with_ctrl_c_lets_the_next_run_adopt_at_once(env_for):
    env, _ = env_for()

    class CtrlC:
        model_id, usage = "m", None

        def run(self, tools, on_event, resuming=False):
            raise KeyboardInterrupt

    run_id = runs.start_run(env.engine, "m")
    with pytest.raises(KeyboardInterrupt):
        work_run(env, run_id, False, CtrlC())
    assert begin_run(env, 1, None) == (run_id, True)


def test_todo_keeps_done_items_on_an_adopted_run(env_for):
    env, _ = env_for({"CLM-1002": [[Stopped(), decline()]]})
    run_id = runs.start_run(env.engine, env.model_id, scope=["CLM-1001", "CLM-1002", "CLM-1004"])
    t = tools(env, run_id)
    t["set_todo"].invoke({"items": [{"title": f"Work {c}", "claim_id": c} for c in ("CLM-1001", "CLM-1002", "CLM-1004")]})
    t["work_claim"].invoke({"claim_id": "CLM-1001"})
    with pytest.raises(Stopped):
        work_claim(env, "CLM-1002", run_id)
    RawDb(env.engine).execute("UPDATE runs SET heartbeat_at = NULL")

    next_id, adopted = begin_run(env, 1, None)
    work_run(env, next_id, adopted, ScriptedQueueAgent())

    todo = {i["claim_id"]: i["status"] for i in runs.todo_list(env.engine, run_id)}
    assert todo == {"CLM-1001": "done", "CLM-1002": "done", "CLM-1004": "done"}
    row = RawDb(env.engine).execute("SELECT attempts, thread_id FROM claims WHERE claim_id='CLM-1002'").fetchone()
    assert tuple(row) == (0, "CLM-1002-1")


def test_the_queue_agent_is_told_when_it_is_finishing_a_stopped_run(env_for):
    env, _ = env_for()
    boss = script(tool_call("finish_run", {"summary": "Nothing left."}, "f1"), AIMessage(content="Done."))
    run_id = runs.start_run(env.engine, "fake/boss", scope=[])
    run_harness(env, run_id, LangChainQueueAgent(boss, "fake/boss", 50), resuming=True)
    assert "stopped earlier" in boss.seen[0][1].content
