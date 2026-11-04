import json

import pytest
from langchain_core.messages import AIMessage

from app import runs
from app.agent import LangChainAgent
from app.config import Settings
from app.db import init_schema
from tests.fakes import connect
from app.orchestrator import LangChainOrchestrator, build_run_tools, run_harness
from app.reference import Reference
from app.scripted import ScriptedAgent, ScriptedOrchestrator
from app.seed import seed_claims
from tests.fakes import STUB_USAGE, StubAgent, script, tool_call

FIVE = ["CLM-1001", "CLM-1002", "CLM-1004", "CLM-1006", "CLM-1007"]
DECISION = {
    "decision": "decline", "payout": 0, "calculation": [],
    "rationale": "The belongings were unattended. Section 5 excludes them.",
    "citations": ["Policy wording, section 5"],
}


@pytest.fixture
def ref(data_dir):
    return Reference(data_dir)


@pytest.fixture
def conn(db_path, ref):
    c = connect(db_path)
    init_schema(c.engine)
    seed_claims(c.engine, ref)
    return c


@pytest.fixture
def settings(db_path, data_dir):
    return Settings(_env_file=None, agent_provider="scripted", db_path=db_path, data_dir=data_dir)


def statuses(conn, ids=None):
    rows = conn.execute("SELECT claim_id, status FROM claims ORDER BY claim_id").fetchall()
    return {r[0]: r[1] for r in rows if ids is None or r[0] in ids}


def run_row(conn, run_id):
    return runs.get_run(conn.engine, run_id)


def scripted_run(conn, ref, settings, agent=None, cap=None, scope=None):
    run_id = runs.start_run(conn.engine, "scripted", cap=cap, scope=scope)
    run_harness(conn.engine, ref, settings, run_id, ScriptedOrchestrator(), agent or ScriptedAgent(conn.engine))
    return run_id


def test_scripted_run_with_cap_works_that_many_and_leaves_the_rest(conn, ref, settings):
    run_id = scripted_run(conn, ref, settings, cap=5)
    values = list(statuses(conn).values())
    assert values.count("waiting") == 20
    row = run_row(conn, run_id)
    assert row["status"] == "finished" and row["summary"]
    assert [i["status"] for i in runs.todo_list(conn.engine, run_id)] == ["done"] * 5


def test_run_without_cap_works_every_waiting_claim(conn, ref, settings):
    run_id = scripted_run(conn, ref, settings)
    values = list(statuses(conn).values())
    assert "waiting" not in values and "in_progress" not in values
    assert values.count("needs_handler") == 1  # CLM-1013 always fails in the scripted provider
    assert run_row(conn, run_id)["status"] == "finished"


def test_run_on_one_named_claim(conn, ref, settings):
    scripted_run(conn, ref, settings, scope=["CLM-1004"])
    changed = [cid for cid, s in statuses(conn).items() if s != "waiting"]
    assert changed == ["CLM-1004"]


def test_failed_claim_does_not_stop_the_run(conn, ref, settings):
    agent = StubAgent({"CLM-1002": [RuntimeError("down"), RuntimeError("down")]})
    run_id = scripted_run(conn, ref, settings, agent=agent, scope=FIVE)
    s = statuses(conn, FIVE)
    assert s.pop("CLM-1002") == "needs_handler"
    assert set(s.values()) == {"decided"}
    assert run_row(conn, run_id)["status"] == "finished"


def test_claim_failing_once_is_retried_in_the_same_run_and_decided(conn, ref, settings):
    agent = StubAgent({"CLM-1002": [RuntimeError("blip")]})
    run_id = scripted_run(conn, ref, settings, agent=agent, scope=FIVE)
    assert statuses(conn, ["CLM-1002"]) == {"CLM-1002": "decided"}
    assert agent.asked.count("CLM-1002") == 2
    assert agent.asked.index("CLM-1002") < agent.asked.index("CLM-1007")  # retried after the others
    assert agent.asked[-1] == "CLM-1002"
    assert run_row(conn, run_id)["status"] == "finished"


def tools(conn, ref, run_id, agent=None):
    return {t.name: t for t in build_run_tools(conn.engine, ref, agent or StubAgent(), run_id, 2)}


def test_work_claim_tool_refuses_past_the_cap_and_outside_scope(conn, ref):
    run_id = runs.start_run(conn.engine, "m", cap=1, scope=["CLM-1001", "CLM-1002"])
    agent = StubAgent()
    t = tools(conn, ref, run_id, agent)
    assert t["work_claim"].invoke({"claim_id": "CLM-1004"}) == "CLM-1004: not worked, it is not part of this run."
    assert t["work_claim"].invoke({"claim_id": "CLM-1001"}).startswith("CLM-1001: approve")
    assert t["work_claim"].invoke({"claim_id": "CLM-1002"}) == \
        "CLM-1002: not worked, the run's cap of 1 claims is reached."
    assert agent.asked == ["CLM-1001"]
    assert statuses(conn, ["CLM-1002", "CLM-1004"]) == {"CLM-1002": "waiting", "CLM-1004": "waiting"}


def test_list_waiting_claims_shows_scope_cap_and_taken(conn, ref):
    run_id = runs.start_run(conn.engine, "m", cap=1, scope=["CLM-1001", "CLM-1002"])
    listed = json.loads(tools(conn, ref, run_id)["list_waiting_claims"].invoke({}))
    assert (listed["cap"], listed["taken"]) == (1, 0)
    assert [c["claim_id"] for c in listed["claims"]] == ["CLM-1001", "CLM-1002"]
    assert {"claim_type", "summary", "amount_claimed", "attempts"} <= set(listed["claims"][0])


def test_finish_run_refuses_while_claims_are_missed(conn, ref):
    run_id = runs.start_run(conn.engine, "m", scope=["CLM-1001"])
    t = tools(conn, ref, run_id)
    assert t["finish_run"].invoke({"summary": "all done"}) == "Not finished. Still to work: CLM-1001"
    t["work_claim"].invoke({"claim_id": "CLM-1001"})
    assert t["finish_run"].invoke({"summary": "all done"}) == "Run finished."
    assert run_row(conn, run_id)["summary"] == "all done"


def test_todo_tools_return_errors_as_text(conn, ref):
    run_id = runs.start_run(conn.engine, "m")
    t = tools(conn, ref, run_id)
    planned = json.loads(t["set_todo"].invoke({"items": [{"title": "Work CLM-1001", "claim_id": "CLM-1001"}]}))
    assert planned[0]["status"] == "todo"
    assert t["update_todo"].invoke({"item_id": planned[0]["item_id"], "status": "done"}).startswith("Error:")
    assert t["update_todo"].invoke({"item_id": 999, "status": "todo"}).startswith("Error:")


def subagent(conn, ref, *claims):
    """A LangChainAgent over a fake model that reads each claim and then declines it."""
    messages = []
    for i, _ in enumerate(claims):
        messages += [tool_call("Decision", DECISION, f"d{i}")]
    model = script(*messages)
    return LangChainAgent(model, conn.engine, ref, "fake/sub"), model


def test_langchain_orchestrator_sees_only_one_line_reports(conn, ref, settings):
    agent, _ = subagent(conn, ref, "CLM-1001")
    boss = script(
        tool_call("work_claim", {"claim_id": "CLM-1001"}, "w1"),
        tool_call("finish_run", {"summary": "One claim declined."}, "f1"),
        AIMessage(content="Done."),
    )
    run_id = runs.start_run(conn.engine, "fake/boss", scope=["CLM-1001"])
    run_harness(conn.engine, ref, settings, run_id, LangChainOrchestrator(boss, "fake/boss", 50), agent)

    shown = boss.seen[-1]
    tool_text = [m.content for m in shown if m.type == "tool"]
    assert "CLM-1001: decline" in tool_text
    assert not any("<claim_data>" in str(m.content) for m in shown)
    assert run_row(conn, run_id)["status"] == "finished"


def test_each_claim_gets_a_fresh_subagent(conn, ref, settings):
    agent, sub = subagent(conn, ref, "CLM-1001", "CLM-1002")
    run_id = runs.start_run(conn.engine, "scripted", scope=["CLM-1001", "CLM-1002"])
    run_harness(conn.engine, ref, settings, run_id, ScriptedOrchestrator(), agent)
    first_call_of_second_claim = sub.seen[1]
    # the system prompt, the request and the facts fetched for it: nothing from CLM-1001
    assert "CLM-1002" in first_call_of_second_claim[1].content
    assert not any("CLM-1001" in str(m.content) for m in first_call_of_second_claim)


class Exploding:
    model_id = "boom"
    usage = None

    def run(self, tools, on_event):
        by_name = {t.name: t for t in tools}
        by_name["work_claim"].invoke({"claim_id": "CLM-1001"})
        raise RuntimeError("orchestrator crashed")


def test_orchestrator_crash_fails_the_run_and_frees_claims(conn, ref, settings):
    run_id = runs.start_run(conn.engine, "boom", scope=FIVE)
    conn.execute("UPDATE claims SET status='in_progress', run_id=? WHERE claim_id='CLM-1007'", (run_id,))
    conn.commit()
    run_harness(conn.engine, ref, settings, run_id, Exploding(), StubAgent())
    row = run_row(conn, run_id)
    assert row["status"] == "failed" and "orchestrator crashed" in row["error"]
    assert "in_progress" not in statuses(conn).values()
    assert statuses(conn, ["CLM-1001"]) == {"CLM-1001": "decided"}


def test_orchestrator_stopping_early_records_missed_claims(conn, ref, settings):
    boss = script(AIMessage(content="I will stop here."))
    run_id = runs.start_run(conn.engine, "fake/boss", scope=["CLM-1001", "CLM-1002"])
    run_harness(conn.engine, ref, settings, run_id, LangChainOrchestrator(boss, "fake/boss", 50), StubAgent())
    row = run_row(conn, run_id)
    assert row["status"] == "failed"
    assert "CLM-1001" in row["error"] and "CLM-1002" in row["error"]


class Thrifty(ScriptedOrchestrator):
    usage = {**STUB_USAGE, "calls": 1, "cost_usd": 0.0005}


def test_run_usage_sums_orchestrator_and_claims(conn, ref, settings):
    run_id = runs.start_run(conn.engine, "scripted", scope=["CLM-1001", "CLM-1002"])
    run_harness(conn.engine, ref, settings, run_id, Thrifty(), StubAgent())
    usage = json.loads(run_row(conn, run_id)["usage"])
    assert usage["calls"] == 1 + 2 * 2
    assert usage["cost_usd"] == pytest.approx(0.0005 + 2 * 0.001)


def test_run_events_record_the_orchestrator_steps(conn, ref, settings):
    run_id = scripted_run(conn, ref, settings, scope=["CLM-1001"])
    kinds = [e["kind"] for e in runs.run_events(conn.engine, run_id)]
    assert "tool_call" in kinds and "tool_result" in kinds


def test_run_instructions_quote_no_claim():
    import re

    from app.agent import load_instructions

    text = load_instructions("run")
    assert "finish_run" in text and "set_todo" in text and "work_claim" in text
    assert not re.search(r"CLM-\d{4}", text)
