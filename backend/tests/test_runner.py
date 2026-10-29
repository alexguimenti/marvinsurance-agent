import json

import pytest

from app.agent import Decision
from app.db import connect, init_schema
from app.payments import request_payment
from app.reference import Reference
from app.runner import run_claim, run_queue
from app.seed import seed_claims

GOOD = "The loss is covered. Section 10 of the wording sets the payment."


class FakeAgent:
    model_id = "fake/model"

    def __init__(self, decision=None, error=None, pay=None, conn=None):
        self.decision, self.error, self.pay, self.conn = decision, error, pay, conn
        self.calls = 0

    def decide(self, claim_id, on_event):
        self.calls += 1
        on_event("tool_call", {"name": "get_claim", "args": {}})
        on_event("tool_result", {"name": "get_claim", "content": "x"})
        if self.pay is not None:
            request_payment(self.conn, claim_id, self.pay, 1000.0)
        if self.error:
            raise self.error
        return self.decision


def approve(payout):
    return Decision(decision="approve", payout=payout, rationale=GOOD)


@pytest.fixture
def ref(data_dir):
    return Reference(data_dir)


@pytest.fixture
def conn(db_path, ref):
    c = connect(db_path)
    init_schema(c)
    seed_claims(c, ref)
    return c


def status(conn, cid="CLM-1001"):
    return conn.execute("SELECT status FROM claims WHERE claim_id=?", (cid,)).fetchone()[0]


def payments(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM payments")]


def test_approve_under_limit_is_decided_and_paid(conn, ref):
    run_claim(conn, ref, FakeAgent(approve(420.0)), "CLM-1001")
    assert status(conn) == "decided"
    assert [(p["status"], p["amount"]) for p in payments(conn)] == [("paid", 420.0)]
    row = conn.execute("SELECT model, payout FROM decisions").fetchone()
    assert (row["model"], row["payout"]) == ("fake/model", 420.0)


def test_approve_over_limit_awaits_approval_and_payment_held(conn, ref):
    run_claim(conn, ref, FakeAgent(approve(2000.0)), "CLM-1001")
    assert status(conn) == "awaiting_approval"
    assert payments(conn)[0]["status"] == "held"


def test_approve_exactly_1000_is_paid(conn, ref):
    run_claim(conn, ref, FakeAgent(approve(1000.0)), "CLM-1001")
    assert status(conn) == "decided"
    assert payments(conn)[0]["status"] == "paid"


@pytest.mark.parametrize("kind", ["decline", "refer"])
def test_decline_and_refer_are_decided_without_payment(conn, ref, kind):
    run_claim(conn, ref, FakeAgent(Decision(decision=kind, rationale=GOOD)), "CLM-1001")
    assert status(conn) == "decided"
    assert payments(conn) == []


def test_agent_exception_becomes_refer_not_stuck(conn, ref):
    d = run_claim(conn, ref, FakeAgent(error=RuntimeError("model down")), "CLM-1001")
    assert d.decision == "refer"
    assert status(conn) == "decided"
    kinds = [r["kind"] for r in conn.execute("SELECT kind FROM trace_events")]
    assert "error" in kinds
    assert payments(conn) == []


def test_payment_amount_mismatch_becomes_refer(conn, ref):
    agent = FakeAgent(approve(500.0), pay=300.0, conn=conn)
    d = run_claim(conn, ref, agent, "CLM-1001")
    assert d.decision == "refer"
    assert status(conn) == "decided"
    assert [p["status"] for p in payments(conn)] == ["void"]


def test_running_claim_twice_makes_one_payment(conn, ref):
    agent = FakeAgent(approve(420.0))
    run_claim(conn, ref, agent, "CLM-1001")
    assert run_claim(conn, ref, agent, "CLM-1001") is None
    assert agent.calls == 1
    assert len(payments(conn)) == 1


def test_trace_events_are_ordered_by_seq(conn, ref):
    run_claim(conn, ref, FakeAgent(approve(420.0)), "CLM-1001")
    rows = conn.execute("SELECT seq, kind FROM trace_events ORDER BY id").fetchall()
    assert [r["seq"] for r in rows] == list(range(1, len(rows) + 1))
    assert rows[0]["kind"] == "status" and rows[-1]["kind"] == "decision"


def test_rationale_warning_goes_to_trace(conn, ref):
    d = Decision(decision="refer", rationale="Nope.")
    run_claim(conn, ref, FakeAgent(d), "CLM-1001")
    kinds = [r["kind"] for r in conn.execute("SELECT kind FROM trace_events")]
    assert "warning" in kinds


def test_run_queue_processes_only_waiting_claims_in_order(conn, ref):
    conn.execute("UPDATE claims SET status='decided' WHERE claim_id='CLM-1001'")
    conn.commit()
    done = run_queue(conn, ref, FakeAgent(approve(10.0)))
    assert done[0] == "CLM-1002" and len(done) == 24


def test_decision_appears_once_in_trace(conn, ref):
    class Emitting(FakeAgent):
        def decide(self, claim_id, on_event):
            d = super().decide(claim_id, on_event)
            on_event("decision", d.model_dump())
            return d

    run_claim(conn, ref, Emitting(approve(420.0)), "CLM-1001")
    kinds = [r["kind"] for r in conn.execute("SELECT kind FROM trace_events")]
    assert kinds.count("decision") == 1


def test_run_queue_with_ids_runs_only_those_in_claim_order(conn, ref):
    done = run_queue(conn, ref, FakeAgent(approve(10.0)), ["CLM-1003", "CLM-1001"])
    assert done == ["CLM-1001", "CLM-1003"]
    assert status(conn, "CLM-1001") == "decided" and status(conn, "CLM-1003") == "decided"
    assert status(conn, "CLM-1002") == "waiting"


def test_usage_is_the_last_trace_event_after_the_decision(conn, ref):
    agent = FakeAgent(approve(420.0))
    agent.usage = {"calls": 3, "input_tokens": 900, "output_tokens": 100, "reasoning_tokens": 0, "cost_usd": 0.0031}
    run_claim(conn, ref, agent, "CLM-1001")
    rows = conn.execute("SELECT kind, content FROM trace_events ORDER BY seq").fetchall()
    assert [r["kind"] for r in rows][-2:] == ["decision", "usage"]
    assert json.loads(rows[-1]["content"])["cost_usd"] == 0.0031


def test_usage_is_recorded_even_when_the_agent_fails(conn, ref):
    agent = FakeAgent(error=RuntimeError("model down"))
    agent.usage = {"calls": 1, "input_tokens": 10, "output_tokens": 0, "reasoning_tokens": 0, "cost_usd": 0.0001}
    run_claim(conn, ref, agent, "CLM-1001")
    kinds = [r["kind"] for r in conn.execute("SELECT kind FROM trace_events ORDER BY seq")]
    assert kinds[-1] == "usage"
