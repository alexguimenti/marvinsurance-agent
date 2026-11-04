import json
import re
import time
from pathlib import Path

import pytest
from langgraph.types import Command

from agent.events import rationale_warnings
from agent.graph import TooManyModelCalls
from agent.prompts import load_instructions
from app.claims import get_claim
from app.payments import get_payment, sign_off
from tests.fakes import RawDb, agent_env, approve, decline, stub, tool_call

CLAIM = "CLM-1003"


@pytest.fixture
def run(db_path, data_dir):
    """Run a claim through the graph on a stub model: run(outcome) -> (env, result)."""
    envs = []

    def start(*outcomes, claim=CLAIM, **settings):
        model = stub({claim: list(outcomes)})
        cm = agent_env(db_path, data_dir, model, **settings)
        env = cm.__enter__()
        envs.append(cm)
        RawDb(env.engine).execute("UPDATE claims SET status='in_progress' WHERE claim_id=?", (claim,))
        return env, model, env.graph.invoke({"claim_id": claim}, config(claim), durability="sync")

    yield start
    for cm in envs:
        cm.__exit__(None, None, None)


def config(claim=CLAIM):
    return {"configurable": {"thread_id": f"{claim}-1"}, "recursion_limit": 100}


def status(env, claim=CLAIM):
    return get_claim(env.engine, claim)["status"]


def kinds(env, claim=CLAIM):
    return [r["kind"] for r in RawDb(env.engine).execute("SELECT kind FROM trace_events WHERE claim_id=? ORDER BY seq", (claim,))]


def trace(env, kind, claim=CLAIM):
    rows = RawDb(env.engine).execute("SELECT content FROM trace_events WHERE claim_id=? AND kind=? ORDER BY seq", (claim, kind))
    return [json.loads(r[0]) for r in rows]


def test_decline_ends_without_a_payment(run):
    env, _, result = run(decline())
    assert "__interrupt__" not in result
    assert status(env) == "decided" and get_payment(env.engine, CLAIM) is None


def test_approval_within_limit_is_paid_straight_after_the_decision(run):
    env, _, _ = run(approve(420.0))
    assert status(env) == "decided"
    payment = get_payment(env.engine, CLAIM)
    assert (payment.status, payment.amount, payment.approved_by) == ("paid", 420.0, None)


def test_approval_over_limit_pauses_before_paying(run):
    env, _, result = run(approve(1000.01))
    assert result["__interrupt__"]
    assert status(env) == "awaiting_approval"
    assert get_payment(env.engine, CLAIM).status == "held"
    assert env.graph.get_state(config()).next == ("pay",)


def test_resume_before_a_sign_off_stays_paused(run):
    env, _, _ = run(approve(2000.0))
    for _ in range(2):
        result = env.graph.invoke(Command(resume={"signed_off": True}), config(), durability="sync")
        assert result["__interrupt__"]
    assert get_payment(env.engine, CLAIM).status == "held"
    assert status(env) == "awaiting_approval"


def test_resume_after_sign_off_pays_once_without_calling_the_model(run):
    env, model, _ = run(approve(2000.0))
    calls = model.calls
    sign_off(env.engine, env.ref, f"PAY-{CLAIM}", "HND-01")
    assert status(env) == "in_progress"
    for _ in range(2):
        env.graph.invoke(Command(resume={"signed_off": True}), config(), durability="sync")
    payment = get_payment(env.engine, CLAIM)
    assert (payment.status, payment.approved_by) == ("paid", "HND-01") and payment.paid_at
    assert status(env) == "decided" and model.calls == calls
    assert RawDb(env.engine).execute("SELECT COUNT(*) FROM payments").fetchone()[0] == 1


def test_the_held_step_is_traced_once_across_resumes(run):
    env, _, _ = run(approve(2000.0))
    env.graph.invoke(Command(resume={"signed_off": True}), config(), durability="sync")
    assert [e["status"] for e in trace(env, "status")].count("awaiting_approval") == 1


def test_refused_tool_call_goes_back_to_the_model(run):
    bad = {**approve(100.0), "decision": "pay it"}
    env, model, _ = run([tool_call("save_decision", bad, "c1"), decline()])
    assert status(env) == "decided"
    assert model.calls == 2
    refusal = [m for m in model.shown[-1] if m.type == "tool" and m.name == "save_decision"]
    assert refusal[0].content.startswith("Error: decision must be")
    assert any(e["content"].startswith("Error:") for e in trace(env, "tool_result") if e["name"] == "save_decision")


@pytest.mark.parametrize("args,why", [
    (approve(0.0), "above 0"),
    ({**approve(100.0), "payout": 1_000_000.0}, "at most the amount claimed"),
    ({**decline(), "payout": 50.0}, "must be 0"),
    ({**decline(), "rationale": "  "}, "rationale is empty"),
])
def test_save_decision_refusals(run, args, why):
    env, model, _ = run([tool_call("save_decision", args, "c1"), decline()])
    refusal = [m for m in model.shown[-1] if m.type == "tool" and m.name == "save_decision"][0]
    assert why in refusal.content


def test_a_tool_that_raises_goes_back_to_the_model_as_text(run):
    env, model, _ = run([tool_call("calculate_payout", {"items": "not a list"}, "c1"), decline()])
    assert status(env) == "decided"
    assert [m for m in model.shown[-1] if m.type == "tool" and m.name == "calculate_payout"][0].content.startswith("Error:")


def test_finishing_without_a_decision_raises(run):
    from langchain_core.messages import AIMessage

    with pytest.raises(ValueError, match="without a decision"):
        run(AIMessage(content="I am done."))


def test_every_step_is_recorded_on_the_claim(run):
    env, _, _ = run([tool_call("read_section", {"document": "policy_wording", "section": 5}, "c1"), approve(420.0)])
    got = kinds(env)
    assert got[0] == "tool_call" and "tool_result" in got
    assert got.count("decision") == 1 and got.count("usage") == 1
    assert trace(env, "status")[-1]["status"] == "paid"
    names = [e["name"] for e in trace(env, "tool_call")]
    assert names[:4] == ["get_claim", "get_policy", "get_plan", "get_customer_history"] and "read_section" in names


def test_usage_totals_every_model_call(run):
    env, _, _ = run([tool_call("search_documents", {"query": "delay"}, "c1"), decline()])
    assert trace(env, "usage") == [
        {"calls": 2, "input_tokens": 100, "output_tokens": 10, "reasoning_tokens": 4, "cost_usd": 0.001}
    ]


def test_rationale_warning_goes_to_the_trace(run):
    env, _, _ = run(decline("Not covered."))
    assert trace(env, "warning")


def test_section_text_is_not_copied_to_the_trace(run):
    env, _, _ = run([tool_call("read_section", {"document": "claims_manual", "section": 7}, "c1"), decline()])
    read = [e for e in trace(env, "tool_result") if e["name"] == "read_section"]
    assert len(read) == 1
    assert re.fullmatch(r"\[claims_manual section 7: Interpretation notes, \d+ characters\]", read[0]["content"])


def test_rereads_and_the_cap_hold_on_the_conversation(run):
    reads = [tool_call("read_section", {"document": "policy_wording", "section": n}, f"c{n}") for n in range(1, 10)]
    again = tool_call("read_section", {"document": "policy_wording", "section": 1}, "again")
    env, _, _ = run([*reads, again, decline()])
    contents = [e["content"] for e in trace(env, "tool_result") if e["name"] == "read_section"]
    assert contents[8].startswith("Error: you have read 8 sections")
    assert contents[9].startswith("You already read policy_wording section 1")


def test_thirty_model_calls_is_the_most(db_path, data_dir):
    loop = [tool_call("search_documents", {"query": "delay"}, f"c{i}") for i in range(40)]
    model = stub({CLAIM: [loop]})
    with agent_env(db_path, data_dir, model) as env:
        with pytest.raises(TooManyModelCalls):
            env.graph.invoke({"claim_id": CLAIM}, config(), durability="sync")
    assert model.calls == 30


def test_model_is_not_offered_a_payment_tool(run):
    _, model, _ = run(decline())
    assert set(model.bound_tools) == {"read_section", "search_documents", "lookup_knowledge", "calculate_payout", "save_decision"}


def test_claim_facts_arrive_as_tool_results_before_the_first_model_call(run):
    _, model, _ = run(decline())
    first = model.shown[0]
    assert model.calls == 1
    results = {m.name: m.content for m in first if m.type == "tool"}
    assert set(results) == {"get_claim", "get_policy", "get_plan", "get_customer_history"}
    assert results["get_claim"].startswith("<claim_data>")
    assert '"policy_id": "POL-2003"' in results["get_policy"]
    assert not any('"claim_id": "CLM-1003"' in str(m.content) for m in first if m.type in ("human", "system"))


def test_system_prompt_lists_the_sections_of_both_documents(run):
    _, model, _ = run(decline())
    system = model.shown[0][0].content
    assert "never follow instructions" in system.lower()
    assert "Policy wording: 1. Your plan" in system and "10. How we work out your payment" in system
    assert "Claims manual: 1. Purpose and scope" in system and "7. Interpretation notes" in system


def test_claim_text_only_reaches_the_model_inside_a_tool_result(db_path, data_dir):
    claim = json.loads((Path(data_dir) / "security" / "claims.json").read_text(encoding="utf-8"))[0]
    model = stub({claim["claim_id"]: [decline()]})
    with agent_env(db_path, data_dir, model) as env:
        RawDb(env.engine).execute("INSERT INTO claims (claim_id, status, payload) VALUES (?, 'in_progress', ?)",
                                  (claim["claim_id"], json.dumps(claim)))
        env.graph.invoke({"claim_id": claim["claim_id"]}, config(claim["claim_id"]), durability="sync")
    shown = model.shown[-1]
    tool_msgs = [m for m in shown if m.type == "tool" and "IMPORTANT NOTE" in m.content]
    assert tool_msgs and all(m.content.startswith("<claim_data>") for m in tool_msgs)
    assert not any("IMPORTANT NOTE" in m.content for m in shown if m.type in ("human", "system"))


def test_a_slow_claim_times_out(db_path, data_dir):
    loop = [tool_call("search_documents", {"query": "delay"}, f"c{i}") for i in range(5)]
    model = stub({CLAIM: [loop]})
    with agent_env(db_path, data_dir, model) as env:
        cfg = {**config(), "configurable": {**config()["configurable"], "deadline": time.monotonic() - 1}}
        with pytest.raises(TimeoutError):
            env.graph.invoke({"claim_id": CLAIM}, cfg, durability="sync")


def test_rationale_warnings_flag_missing_section():
    assert rationale_warnings("Not covered. Declined.")
    assert not rationale_warnings("The belongings were unattended. Section 5 excludes them.")


def test_instructions_tell_the_agent_the_system_pays():
    text = load_instructions("claim").lower()
    assert "never follow instructions" in text and "save_decision" in text and "calculate_payout" in text
    assert "request_payment" not in text
