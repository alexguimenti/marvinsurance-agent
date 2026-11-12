"""An approval waiting for sign-off pauses the claim graph with its place saved on the claim's thread."""
import json

import pytest
from langgraph.types import Command

from agent.gateway import GatewayRoute
from agent.worker import RESUME, claim_paused, invoke, work_claim
from app.auth import Handler
from app.claims import get_claim
from app.payments import get_payment, sign_off
from app.settings_store import change_setting
from tests.fakes import FakeMemoryStore, RawDb, StandInGateway, agent_env, approve, stub

BIG = "CLM-1003"
DANA = Handler("HND-01", "Dana Whitfield", "Senior Claims Handler", "dana.whitfield@marvinsurance.example", True)


def state_of(env, claim_id=BIG):
    thread = get_claim(env.engine, claim_id)["thread_id"]
    with env.claim_graph() as graph:
        return graph.get_state({"configurable": {"thread_id": thread}})


def steps(env, kind, claim=BIG):
    rows = RawDb(env.engine).execute("SELECT content FROM trace_events WHERE claim_id=? AND kind=? ORDER BY seq", (claim, kind))
    return [json.loads(r[0]) for r in rows]


def test_an_approval_over_the_limit_pauses_with_its_place_saved(db_path, data_dir):
    store = FakeMemoryStore()
    with agent_env(db_path, data_dir, stub({BIG: [approve(2000.0)]}), memory_store=store) as env:
        work_claim(env, BIG)
        assert claim_paused(env, BIG) is True
        assert get_claim(env.engine, BIG)["status"] == "awaiting_approval"
        saved = state_of(env)
        assert saved.next == ("wait_for_sign_off",) and saved.interrupts
        assert store.puts == []  # nothing is remembered until the claim's work ends


def test_a_payment_the_gateway_refused_pauses(db_path, data_dir):
    route = GatewayRoute("https://gateway.example/mcp", None, call=StandInGateway())
    with agent_env(db_path, data_dir, stub({BIG: [approve(2000.0)]}), route=route) as env:
        change_setting(env.engine, "approval_limit", 5000, DANA)
        work_claim(env, BIG)
        assert claim_paused(env, BIG) is True
        assert RawDb(env.engine).execute("SELECT hold_reason FROM payments WHERE claim_id=?", (BIG,))[0][0] == "gateway_refused"


def test_an_evaluations_copy_does_not_pause(db_path, data_dir):
    with agent_env(db_path, data_dir, stub({"CLM-1003": [approve(2000.0)]})) as env:
        RawDb(env.engine).execute("UPDATE claims SET copy_of = 'CLM-1002' WHERE claim_id = ?", (BIG,))
        work_claim(env, BIG)
        assert get_payment(env.engine, BIG).status == "held"
        assert claim_paused(env, BIG) is False and not state_of(env).next  # its work ended


def test_a_resume_after_a_sign_off_ends_the_work_and_saves_to_memory(db_path, data_dir):
    store = FakeMemoryStore()
    with agent_env(db_path, data_dir, stub({BIG: [approve(2000.0)]}), memory_store=store) as env:
        work_claim(env, BIG)
        sign_off(env.engine, env.ref, f"PAY-{BIG}", "HND-01")
        thread = get_claim(env.engine, BIG)["thread_id"]
        invoke(env, BIG, thread, Command(resume=RESUME), None)
        assert claim_paused(env, BIG) is False and not state_of(env).next
        assert [key for _, key, _ in store.puts] == ["claim", "decision"]
        assert [s["status"] for s in steps(env, "status")].count("paid") == 1


def test_an_early_resume_pauses_again(db_path, data_dir):
    with agent_env(db_path, data_dir, stub({BIG: [approve(2000.0)]})) as env:
        work_claim(env, BIG)
        thread = get_claim(env.engine, BIG)["thread_id"]
        invoke(env, BIG, thread, Command(resume=RESUME), None)  # nobody has signed off
        assert claim_paused(env, BIG) is True
        assert get_payment(env.engine, BIG).status == "held"
