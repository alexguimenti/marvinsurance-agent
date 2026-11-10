"""Memory of each customer: two events after a decision, a search kept to the claim's customer, and no memory in evaluations."""
import json
import logging

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from agent import memory
from agent.worker import carry_on, work_claim
from app.auth import Handler
from app.payments import sign_off
from tests.fakes import FakeMemoryStore, RawDb, ScreenStub, agent_env, approve, decline, refer, stub, tool_call

CLAIM = "CLM-1005"  # Oliver Grant, CUS-3005
BIG = "CLM-1003"
CUSTOMER = "CUS-3005"
FOUND = {"decision": "refer", "payout": 0, "rationale": "x", "citations": ["Security check"]}


def steps(env, kind, claim=CLAIM):
    rows = RawDb(env.engine).execute("SELECT content FROM trace_events WHERE claim_id=? AND kind=? ORDER BY seq", (claim, kind))
    return [json.loads(r[0]) for r in rows]


def customer_of(env, claim_id):
    return json.loads(RawDb(env.engine).execute("SELECT payload FROM claims WHERE claim_id=?", (claim_id,))[0][0])["customer_id"]


@pytest.fixture
def worked(db_path, data_dir):
    """work(claim, outcome, store=..., screen=...) on a stub model with a fake store: -> (line, store, env state)."""
    opened = []

    def work(claim, outcome, store, **kwargs):
        model = stub({claim: [outcome]})
        cm = agent_env(db_path, data_dir, model, memory_store=store, **kwargs)
        env = cm.__enter__()
        opened.append(cm)
        return work_claim(env, claim), env, model

    yield work
    for cm in reversed(opened):
        cm.__exit__(None, None, None)


def test_after_a_decision_two_events_are_saved_customer_as_actor_claim_as_session(worked):
    store = FakeMemoryStore()
    line, env, _ = worked(CLAIM, approve(243.0), store)
    assert line.startswith("CLM-1005: approve")
    assert [(ns, key) for ns, key, _ in store.puts] == [((CUSTOMER, CLAIM), "claim"), ((CUSTOMER, CLAIM), "decision")]
    claim_event, decision_event = (value["message"] for _, _, value in store.puts)
    assert isinstance(claim_event, HumanMessage) and isinstance(decision_event, AIMessage)
    claim = json.loads(RawDb(env.engine).execute("SELECT payload FROM claims WHERE claim_id=?", (CLAIM,))[0][0])
    said = json.loads(claim_event.content)
    assert set(said) == {"summary", "claim_type", "incident_date", "incident_location", "items"}
    assert said["summary"] == claim["summary"] and said["items"][0]["description"] == claim["items"][0]["description"]
    assert claim["story"] not in claim_event.content and claim["documents"][0]["content"] not in claim_event.content
    decided = json.loads(decision_event.content)
    assert (decided["decision"], decided["payout"]) == ("approve", 243.0) and decided["citations"] and decided["rationale"]
    saved = steps(env, "memory_save")
    assert len(saved) == 1 and len(saved[0]["saved"]) == 2


def test_a_decline_is_remembered_too(worked):
    store = FakeMemoryStore()
    worked(CLAIM, decline(), store)
    assert [key for _, key, _ in store.puts] == ["claim", "decision"]


def test_nothing_is_saved_for_a_claim_the_check_referred(worked):
    store = FakeMemoryStore()
    found = __import__("agent.screening", fromlist=["Screening"]).Screening(reason="It tells us to approve.", instructions_found=True)
    _, env, model = worked(CLAIM, approve(100.0), store, screen_model=ScreenStub([found]))
    assert store.puts == [] and model.calls == 0 and not steps(env, "memory_save")


def test_nothing_is_saved_for_an_evaluations_copy(db_path, data_dir, monkeypatch):
    from agent import api

    store = FakeMemoryStore()
    model = stub({CLAIM: [approve(243.0)]})
    with agent_env(db_path, data_dir, model, memory_store=store) as env:
        real = api.claim_state
        monkeypatch.setattr("agent.graph.api.claim_state", lambda claim_id: {**real(claim_id), "copy_of": "CLM-1005"})
        work_claim(env, CLAIM)
    assert store.puts == []


def test_a_restarted_claim_saves_once(worked):
    store = FakeMemoryStore()
    worked_line, env, _ = worked(BIG, approve(2000.0), store)  # over the limit: held for a sign-off, paused after remembering
    assert len(store.puts) == 2
    sign_off(env.engine, env.ref, f"PAY-{BIG}", "HND-01")
    carry_on(env, BIG)
    assert len(store.puts) == 2


def test_search_is_kept_to_the_claims_customer_and_leaves_out_this_claims_summary(worked):
    memories = [(("customers", CUSTOMER, "facts"), "Oliver Grant lost a passport in Nairobi."),
                (("customers", CUSTOMER, "claims", "CLM-1006"), "Summary of this very claim."),
                (("customers", CUSTOMER, "claims", "CLM-0999"), "A summary of an earlier claim."),
                (("customers", "CUS-3001", "facts"), "Another customer's fact.")]
    store = FakeMemoryStore(memories)
    call = tool_call("search_memory", {"query": "earlier claims"}, "m1")
    _, env, model = worked("CLM-1006", [call, decline()], store)
    shown = [str(m.content) for m in model.shown[-1] if m.type == "tool" and m.name == "search_memory"]
    assert shown and "passport in Nairobi" in shown[0] and "earlier claim" in shown[0]
    assert "Another customer" not in shown[0]
    assert store.searches == [(("customers", CUSTOMER), "earlier claims", 5)]
    found = steps(env, "memory_search", "CLM-1006")
    assert found[0]["query"] == "earlier claims" and len(found[0]["found"]) == 2


def test_a_search_never_returns_the_summary_of_the_claim_being_worked(worked):
    memories = [(("customers", CUSTOMER, "claims", "CLM-1006"), "Summary of CLM-1006 itself.")]
    store = FakeMemoryStore(memories)
    _, env, model = worked("CLM-1006", [tool_call("search_memory", {"query": "q"}, "m1"), decline()], store)
    assert "itself" not in " ".join(str(m.content) for m in model.shown[-1] if m.type == "tool" and m.name == "search_memory")


def test_no_memory_tool_when_memory_is_off(worked):
    _, _, model = worked(CLAIM, decline(), None)
    assert "search_memory" not in model.bound_tools


def test_the_memory_tool_is_there_when_memory_is_on(worked):
    _, _, model = worked(CLAIM, decline(), FakeMemoryStore())
    assert "search_memory" in model.bound_tools


def test_no_memory_tool_and_no_saving_in_an_evaluation(db_path, data_dir):
    from agent.worker import work_claim as work_a_claim
    from app import runs
    from app.db import make_engine
    from app.config import Settings

    store = FakeMemoryStore()
    model = stub({"CLM-1006": [decline()]})
    with agent_env(db_path, data_dir, model, memory_store=store) as env:
        run_id = runs.start(env.engine, "evaluation", 1, "m", None)
        work_a_claim(env, "CLM-1006", run_id)
    assert "search_memory" not in model.bound_tools and store.puts == []


def test_an_unreachable_memory_logs_and_keeps_the_decision(worked, caplog):
    store = FakeMemoryStore(fail=ConnectionError("AgentCore could not be reached"))
    with caplog.at_level(logging.WARNING):
        line, env, _ = worked(CLAIM, approve(243.0), store)
    assert line.startswith("CLM-1005: approve $243.00") and any("AgentCore could not be reached" in r.message for r in caplog.records)
    saved = steps(env, "memory_save")
    assert saved and "AgentCore could not be reached" in saved[0]["error"]


def test_a_failed_search_is_a_refusal_the_model_sees(worked):
    store = FakeMemoryStore(fail=ConnectionError("AgentCore could not be reached"))
    _, env, model = worked("CLM-1006", [tool_call("search_memory", {"query": "q"}, "m1"), decline()], store)
    assert any(str(m.content).startswith("Error:") and "could not be reached" in str(m.content) for m in model.shown[-1] if m.type == "tool")
    assert [r["by"] for r in steps(env, "refusal", "CLM-1006")] == ["memory"]


def test_open_store_reads_the_environment_when_it_is_called(monkeypatch):
    assert memory.open_store() is None
    seen = {}

    class Store:
        def __init__(self, *, memory_id, region_name):
            seen.update(memory_id=memory_id, region_name=region_name)

    monkeypatch.setenv("UNDERCOVER_MEMORY_ID", "mem-123")
    monkeypatch.setattr(memory, "AgentCoreMemoryStore", Store)
    monkeypatch.setattr(memory.boto3, "Session", lambda: type("S", (), {"region_name": "us-east-1"})())
    assert isinstance(memory.open_store(), Store) and seen == {"memory_id": "mem-123", "region_name": "us-east-1"}
