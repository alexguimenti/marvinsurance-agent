import json

import pytest
from pydantic import ValidationError

from app.agent import (
    Decision, LangChainAgent, build_agent, load_instructions, rationale_warnings,
)
from app.config import Settings
from app.db import connect, init_schema
from app.reference import Reference
from app.seed import seed_claims
from tests.fakes import script, tool_call


@pytest.fixture
def ref(data_dir):
    return Reference(data_dir)


@pytest.fixture
def conn(db_path, ref):
    c = connect(db_path)
    init_schema(c)
    seed_claims(c, ref)
    return c


def decision_args(**over):
    base = {
        "decision": "decline", "payout": 0, "calculation": [],
        "rationale": "The belongings were unattended. Section 5 excludes them.",
        "citations": ["Policy wording, section 5"],
    }
    return {**base, **over}


def test_decision_rejects_payout_on_decline():
    with pytest.raises(ValidationError):
        Decision(**decision_args(payout=100))


def test_decision_rejects_unknown_decision():
    with pytest.raises(ValidationError):
        Decision(**decision_args(decision="maybe"))


def test_rationale_warnings_flag_long_rationale_without_rejecting():
    long = "One. Two. Three. Four. Section 5."
    assert rationale_warnings(long)
    d = Decision(**decision_args(rationale=long))
    assert d.rationale == long


def test_rationale_warnings_clean_for_good_rationale():
    assert rationale_warnings("Unattended items are excluded. Section 5 of the wording applies.") == []


def test_rationale_warnings_flag_missing_section():
    assert rationale_warnings("Not covered. Declined.")


def test_instructions_mention_data_not_instructions_and_wording_section():
    text = load_instructions("claim").lower()
    assert "never follow instructions" in text
    assert "section" in text
    assert "calculate_payout" in text and "refer" in text


def test_agent_emits_trace_events(conn, ref):
    model = script(
        tool_call("get_claim", {}, "c1"),
        tool_call("Decision", decision_args(), "c2"),
    )
    events = []
    agent = LangChainAgent(model, conn, ref, "test/model")
    decision = agent.decide("CLM-1001", lambda kind, content: events.append(kind))
    assert decision.decision == "decline"
    assert events[0] == "tool_call" and "tool_result" in events and events[-1] == "decision"


def test_injection_claim_is_quoted_as_data(conn, ref, data_dir):
    import pathlib
    claim = json.loads((pathlib.Path(data_dir) / "security" / "claims.json").read_text(encoding="utf-8"))[0]
    conn.execute("INSERT OR REPLACE INTO claims (claim_id, status, payload) VALUES (?, 'waiting', ?)",
                 (claim["claim_id"], json.dumps(claim)))
    model = script(
        tool_call("get_claim", {}, "c1"),
        tool_call("Decision", decision_args(), "c2"),
    )
    LangChainAgent(model, conn, ref, "test/model").decide(claim["claim_id"], lambda *_: None)
    shown = model.seen[-1]
    system = shown[0].content
    assert "never follow instructions" in system.lower()
    tool_msgs = [m for m in shown if m.type == "tool" and "IMPORTANT NOTE" in m.content]
    assert tool_msgs, "claim text should reach the model through the tool result"
    assert all(m.content.startswith("<claim_data>") for m in tool_msgs)
    assert not any("IMPORTANT NOTE" in m.content for m in shown if m.type in ("human", "system"))


def test_scripted_provider_needs_no_key(conn, ref):
    settings = Settings(_env_file=None, agent_provider="scripted")
    agent = build_agent(settings, conn, ref)
    events = []
    d = agent.decide("CLM-1001", lambda kind, content: events.append(kind))
    assert d.decision in ("approve", "decline", "refer") and events


def test_openrouter_provider_uses_configured_model(conn, ref):
    settings = Settings(_env_file=None, agent_provider="openrouter",
                        openrouter_api_key="k", agent_model="some/model")
    agent = build_agent(settings, conn, ref)
    assert agent.model_id == "some/model"


def test_section_text_is_not_copied_to_the_trace(conn, ref):
    import re

    model = script(
        tool_call("read_section", {"document": "claims_manual", "section": 7}, "c1"),
        tool_call("Decision", decision_args(), "c2"),
    )
    results = []
    LangChainAgent(model, conn, ref, "test/model").decide(
        "CLM-1001", lambda kind, content: results.append(content) if kind == "tool_result" else None
    )
    assert results and results[0]["name"] == "read_section"
    assert re.fullmatch(r"\[claims_manual section 7: Interpretation notes, \d+ characters\]", results[0]["content"])


def test_long_json_tool_results_are_kept_whole_for_the_trace(conn, ref):
    model = script(
        tool_call("get_claim", {}, "c1"),
        tool_call("Decision", decision_args(), "c2"),
    )
    results = []
    LangChainAgent(model, conn, ref, "test/model").decide(
        "CLM-1001", lambda kind, content: results.append(content) if kind == "tool_result" else None
    )
    body = results[0]["content"].removeprefix("<claim_data>").removesuffix("</claim_data>")
    json.loads(body)  # the whole document, so the screen can pretty-print it


def test_reasoning_effort_is_sent_to_openrouter(conn, ref):
    settings = Settings(_env_file=None, agent_provider="openrouter", openrouter_api_key="k",
                        agent_model="some/model", agent_reasoning_effort="high")
    agent = build_agent(settings, conn, ref)
    assert agent.model.extra_body == {"reasoning": {"effort": "high"}, "usage": {"include": True}}


def test_no_reasoning_parameter_when_not_configured(conn, ref):
    settings = Settings(_env_file=None, agent_provider="openrouter", openrouter_api_key="k",
                        agent_model="some/model", agent_reasoning_effort=None)
    agent = build_agent(settings, conn, ref)
    assert agent.model.extra_body == {"usage": {"include": True}}  # cost reporting only, no reasoning


def ai_call(name, args, call_id, tokens_in, tokens_out, cost=None, reasoning=0):
    from langchain_core.messages import AIMessage

    meta = {"token_usage": {"cost": cost}} if cost is not None else {}
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args, "id": call_id}],
        usage_metadata={"input_tokens": tokens_in, "output_tokens": tokens_out,
                        "total_tokens": tokens_in + tokens_out,
                        "output_token_details": {"reasoning": reasoning}},
        response_metadata=meta,
    )


def test_agent_totals_usage_and_cost_across_model_calls(conn, ref):
    model = script(
        ai_call("get_claim", {}, "c1", 100, 50, cost=0.001, reasoning=20),
        ai_call("Decision", decision_args(), "c2", 200, 80, cost=0.0025, reasoning=30),
    )
    agent = LangChainAgent(model, conn, ref, "test/model")
    agent.decide("CLM-1001", lambda *_: None)
    assert agent.usage == {
        "calls": 2, "input_tokens": 300, "output_tokens": 130, "reasoning_tokens": 50, "cost_usd": 0.0035,
    }


def test_usage_cost_is_none_when_the_provider_reports_none(conn, ref):
    model = script(ai_call("get_claim", {}, "c1", 10, 5), ai_call("Decision", decision_args(), "c2", 10, 5))
    agent = LangChainAgent(model, conn, ref, "test/model")
    agent.decide("CLM-1001", lambda *_: None)
    assert agent.usage["cost_usd"] is None and agent.usage["calls"] == 2


def test_usage_is_reset_for_each_claim(conn, ref):
    def run():
        model = script(ai_call("Decision", decision_args(), "c1", 10, 5, cost=0.001))
        agent = LangChainAgent(model, conn, ref, "test/model")
        agent.decide("CLM-1001", lambda *_: None)
        agent.model = script(ai_call("Decision", decision_args(), "c2", 10, 5, cost=0.001))
        agent.decide("CLM-1002", lambda *_: None)
        return agent.usage

    assert run()["cost_usd"] == 0.001


def test_scripted_agent_fails_on_1013_always_and_1017_once(conn, ref):
    from app.agent import ScriptedAgent

    agent = ScriptedAgent(conn)
    for attempts in (0, 1, 2):
        conn.execute("UPDATE claims SET attempts=? WHERE claim_id='CLM-1013'", (attempts,))
        with pytest.raises(RuntimeError):
            agent.decide("CLM-1013", lambda *_: None)
    with pytest.raises(RuntimeError):
        agent.decide("CLM-1017", lambda *_: None)
    conn.execute("UPDATE claims SET attempts=1 WHERE claim_id='CLM-1017'")
    assert agent.decide("CLM-1017", lambda *_: None).decision in ("approve", "decline", "refer")


def test_scripted_agent_reads_a_section(conn, ref):
    from app.agent import ScriptedAgent

    calls = []
    ScriptedAgent(conn).decide("CLM-1001", lambda kind, content: calls.append(content) if kind == "tool_call" else None)
    assert {"name": "read_section", "args": {"document": "policy_wording", "section": 10}} in calls


def test_langchain_agent_passes_recursion_limit(conn, ref, monkeypatch):
    import app.agent as agent_module

    seen = {}

    class Spy:
        def stream(self, inputs, stream_mode=None, config=None):
            seen["config"] = config
            yield {"model": {"structured_response": Decision(**decision_args())}}

    monkeypatch.setattr(agent_module, "create_agent", lambda *a, **k: Spy())
    LangChainAgent(script(), conn, ref, "test/model", max_steps=12).decide("CLM-1001", lambda *_: None)
    assert seen["config"]["recursion_limit"] == 12


def test_langchain_agent_times_out(conn, ref):
    import time

    from tests.fakes import FakeToolModel

    class SlowModel(FakeToolModel):
        def _generate(self, messages, *args, **kwargs):
            time.sleep(0.1)
            return super()._generate(messages, *args, **kwargs)

    model = SlowModel(messages=iter([tool_call("get_claim", {}, "c1"), tool_call("get_claim", {}, "c2"),
                                     tool_call("Decision", decision_args(), "c3")]), seen=[])
    with pytest.raises(TimeoutError):
        LangChainAgent(model, conn, ref, "test/model", timeout_s=0.05).decide("CLM-1001", lambda *_: None)


def test_build_agent_passes_step_limit_and_timeout(conn, ref):
    settings = Settings(_env_file=None, agent_provider="openrouter", openrouter_api_key="k",
                        agent_model="some/model", agent_max_steps=17, agent_claim_timeout_s=9)
    agent = build_agent(settings, conn, ref)
    assert (agent.max_steps, agent.timeout_s) == (17, 9)
    assert agent.model.max_retries == settings.agent_step_retries
