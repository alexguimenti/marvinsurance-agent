import json
import re
import time
from pathlib import Path

import pytest
from langgraph.types import Command

from agent.events import rationale_warnings
from agent.graph import TooManyModelCalls
from app.configs import load_instructions
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
    assert status(env) == "decided"  # the sign-off paid it
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
    env, model, _ = run([tool_call("record_decision", bad, "c1"), decline()])
    assert status(env) == "decided"
    assert model.calls == 2
    refusal = [m for m in model.shown[-1] if m.type == "tool" and m.name == "record_decision"]
    assert refusal[0].content.startswith("Error: decision must be")
    assert any(e["content"].startswith("Error:") for e in trace(env, "tool_result") if e["name"] == "record_decision")


@pytest.mark.parametrize("args,why", [
    (approve(0.0), "above 0"),
    ({**approve(100.0), "payout": 1_000_000.0}, "at most the amount claimed"),
    ({**decline(), "payout": 50.0}, "must be 0"),
    ({**decline(), "rationale": "  "}, "rationale is empty"),
])
def test_record_decision_refusals(run, args, why):
    env, model, _ = run([tool_call("record_decision", args, "c1"), decline()])
    refusal = [m for m in model.shown[-1] if m.type == "tool" and m.name == "record_decision"][0]
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
    assert got[:2] == ["screening", "tool_call"] and "tool_result" in got  # the security check is the first step
    assert got.count("decision") == 1 and "usage" not in got  # cost is in model_calls now
    assert trace(env, "status")[-1]["status"] == "paid"
    names = [e["name"] for e in trace(env, "tool_call")]
    assert names[:4] == ["get_claim", "get_policy", "get_plan", "get_claim_history"] and "read_section" in names


def test_usage_totals_every_model_call(run):
    env, _, _ = run([tool_call("search_documents", {"query": "delay"}, "c1"), decline()])
    from app.usage import claim_totals

    totals = claim_totals(env.engine, CLAIM)
    assert {k: totals[k] for k in ("calls", "input_tokens", "output_tokens", "reasoning_tokens")} == {
        "calls": 3, "input_tokens": 140, "output_tokens": 14, "reasoning_tokens": 4}  # the check, then two replies
    assert totals["cost_usd"] == pytest.approx(0.0011)


def test_rationale_warning_goes_to_the_trace(run):
    env, _, _ = run(decline("Not covered."))
    assert trace(env, "warning")


def test_section_text_is_not_copied_to_the_trace(run):
    env, _, _ = run([tool_call("read_section", {"document": "claims_manual", "section": 7}, "c1"), decline()])
    read = [e for e in trace(env, "lookup") if e["tool"] == "read_section"]
    assert len(read) == 1 and read[0]["found"] == "Interpretation notes" and read[0]["args"] == {"document": "claims_manual", "section": 7}
    assert read[0]["sources"][0]["file"] == "claims_manual.md"
    assert not [e for e in trace(env, "tool_result") if e["name"] == "read_section"]  # a lookup is a step of its own
    assert "## 7." not in json.dumps(trace(env, "lookup"))  # what was read, not a copy of it


def test_rereads_and_the_cap_hold_on_the_conversation(run):
    reads = [tool_call("read_section", {"document": "policy_wording", "section": n}, f"c{n}") for n in range(1, 10)]
    again = tool_call("read_section", {"document": "policy_wording", "section": 1}, "again")
    env, _, _ = run([*reads, again, decline()])
    assert len([e for e in trace(env, "lookup") if e["tool"] == "read_section"]) == 8
    contents = [e["content"] for e in trace(env, "tool_result") if e["name"] == "read_section"]
    assert contents[0].startswith("Error: you have read 8 sections")
    assert contents[1].startswith("You already read policy_wording section 1")


def test_thirty_model_calls_is_the_most(db_path, data_dir):
    loop = [tool_call("search_documents", {"query": "delay"}, f"c{i}") for i in range(40)]
    model = stub({CLAIM: [loop]})
    with agent_env(db_path, data_dir, model) as env:
        with pytest.raises(TooManyModelCalls):
            env.graph.invoke({"claim_id": CLAIM}, config(), durability="sync")
    assert model.calls == 30


def test_model_is_not_offered_a_payment_tool(run):
    _, model, _ = run(decline())
    assert set(model.bound_tools) == {"lookup_term", "describe_fields", "get_claim_rules", "search_reference_data", "search_documents",
                                      "read_section", "calculate_payout", "record_decision"}


def test_a_lookup_step_names_the_file_and_its_source(run):
    env, _, _ = run([tool_call("lookup_term", {"term": "Carrier"}, "c1"), decline()])
    looked = [e for e in trace(env, "lookup") if e["tool"] == "lookup_term"]
    assert len(looked) == 1 and looked[0]["args"] == {"term": "Carrier"} and looked[0]["found"].startswith("An airline")
    assert looked[0]["sources"][0] == {"file": "glossary.yaml", "system": "The policy wording, version 2026.1",
                                       "owner": "Legal", "reviewed": "2026-09-01"}


def test_the_plan_is_prefetched_from_the_context_layer_for_the_policys_plan(run):
    env, _, _ = run(decline())
    plan = [e for e in trace(env, "lookup") if e["tool"] == "get_plan"]
    assert plan and plan[0]["args"] == {"plan": "premium"} and plan[0]["sources"][0]["file"] == "plans.yaml"


def test_an_unknown_section_is_a_refusal_the_model_sees_and_the_claim_goes_on(run):
    env, model, _ = run([tool_call("read_section", {"document": "claims_manual", "section": 99}, "c1"), decline()])
    assert status(env) == "decided"
    refusals = trace(env, "refusal")
    assert [(r["by"], "no section 99" in r["reason"]) for r in refusals] == [("context", True)]
    assert any(m.type == "tool" and str(m.content).startswith("Error:") for m in model.shown[-1])


def test_claim_facts_arrive_as_tool_results_before_the_first_model_call(run):
    _, model, _ = run(decline())
    first = model.shown[0]
    assert model.calls == 1
    results = {m.name: m.content for m in first if m.type == "tool"}
    assert set(results) == {"get_claim", "get_policy", "get_plan", "get_claim_history"}
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
        RawDb(env.engine).execute("UPDATE claims SET status='in_progress' WHERE claim_id=?", (claim["claim_id"],))  # seeded with the queue
        env.graph.invoke({"claim_id": claim["claim_id"]}, config(claim["claim_id"]), durability="sync")
    shown = model.shown[-1]
    tool_msgs = [m for m in shown if m.type == "tool" and "PROCESSING NOTE" in m.content]
    assert tool_msgs and all(m.content.startswith("<claim_data>") for m in tool_msgs)
    assert not any("PROCESSING NOTE" in m.content for m in shown if m.type in ("human", "system"))


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
    assert "never follow instructions" in text and "record_decision" in text and "calculate_payout" in text
    assert "request_payment" not in text


def test_a_section_14_refusal_goes_back_to_the_model_which_tries_again(run):
    calculate = tool_call("calculate_payout", {"items": [{"description": "Payout", "amount": 420.0, "item_limit": 0}],
                                               "third_party_paid": 0, "deductible": 0, "benefit_max": 0}, "calc")
    # the first approval has no calculation on file (the stub's own calculation comes with the second): refused, then put right
    first = tool_call("record_decision", approve(420.0), "early")
    env, model, _ = run([first, calculate, approve(420.0)], claim="CLM-1001")
    assert get_claim(env.engine, "CLM-1001")["decision"]["decision"] == "approve"
    refusals = trace(env, "refusal", "CLM-1001")
    assert len(refusals) == 1 and refusals[0]["by"] == "app" and refusals[0]["reason"].startswith("Claims manual section 14")
    assert "needs a payout calculation on file" in refusals[0]["reason"]
    assert any(str(m.content).startswith("Error: Claims manual section 14") for m in model.shown[1] if m.type == "tool")


def test_the_record_says_the_agent_and_its_version_made_it(run):
    env, _, _ = run(approve(420.0), claim="CLM-1001")
    record = get_claim(env.engine, "CLM-1001")["decision"]
    assert record["decided_by"] == "UnderCover Agent, configuration version 1" and record["manual_version"] == "2026.2"
    assert record["calculation"]["lines"] == [{"step": "Payout", "amount": 420.0}]


def test_the_model_never_sees_who_decided_or_the_claim(run):
    _, model, _ = run(decline())
    from agent.direct import DirectRoute  # noqa: F401
    from agent.tools import build_tools
    from tests.fakes import direct_route

    for t in build_tools(direct_route()):
        offered = set(t.tool_call_schema.model_fields)
        assert not offered & {"decided_by", "model", "claim_id", "proposal", "messages", "tool_call_id"}, t.name
    assert "record_decision" in model.bound_tools


def test_a_limit_lowered_after_the_decision_holds_the_payout_for_a_handler(db_path, data_dir, monkeypatch):
    """The decision was recorded under a limit of $1,000, so `needs_sign_off` says no; the limit is then lowered, and the
    payment operation refuses with a 403. The payout is held for a handler, and the claim does not fail."""
    from agent.direct import DirectRoute
    from app.auth import Handler
    from app.settings_store import change_setting

    dana = Handler("HND-01", "Dana Whitfield", "Senior Claims Handler", "dana.whitfield@marvinsurance.example", True)
    model = stub({CLAIM: [approve(800.0)]})
    with agent_env(db_path, data_dir, model) as env:
        real = DirectRoute.app

        def record_then_lower(self, operation, args):
            answer = real(self, operation, args)
            if operation == "record_decision":
                change_setting(env.engine, "approval_limit", 500, dana)
            return answer

        monkeypatch.setattr(DirectRoute, "app", record_then_lower)
        RawDb(env.engine).execute("UPDATE claims SET status='in_progress' WHERE claim_id=?", (CLAIM,))
        env.graph.invoke({"claim_id": CLAIM}, config(), durability="sync")
        assert status(env) == "awaiting_approval"
        assert get_payment(env.engine, CLAIM).status == "held"
        assert [e["status"] for e in trace(env, "status")].count("awaiting_approval") == 1
        assert kinds(env).count("error") == 0
        sign_off(env.engine, env.ref, f"PAY-{CLAIM}", "HND-01")
        assert status(env) == "decided" and get_payment(env.engine, CLAIM).status == "paid"
