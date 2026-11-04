import json

import pytest

from app.agent import Decision
from app.db import init_schema
from tests.fakes import connect
from app.payments import request_payment
from app.reference import Reference
from app.runner import work_claim
from app.seed import seed_claims
from tests.fakes import StubAgent

GOOD = "The loss is covered. Section 10 of the wording sets the payment."


def approve(payout):
    return Decision(decision="approve", payout=payout, rationale=GOOD)


@pytest.fixture
def ref(data_dir):
    return Reference(data_dir)


@pytest.fixture
def conn(db_path, ref):
    c = connect(db_path)
    init_schema(c.engine)
    seed_claims(c.engine, ref)
    return c


def claim(conn, cid="CLM-1001"):
    return conn.execute("SELECT * FROM claims WHERE claim_id=?", (cid,)).fetchone()


def payments(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM payments")]


def kinds(conn):
    return [r["kind"] for r in conn.execute("SELECT kind FROM trace_events ORDER BY seq")]


def decisions(conn):
    return conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]


def test_approve_under_limit_is_paid_and_decided(conn, ref):
    line = work_claim(conn.engine, ref, StubAgent({"CLM-1001": [approve(420.0)]}), "CLM-1001")
    assert line == "CLM-1001: approve $420.00, paid"
    assert claim(conn)["status"] == "decided"
    row = conn.execute("SELECT * FROM decisions").fetchone()
    assert (row["manual_version"], row["wording_version"]) == ("2026.2", "2026.1")
    assert [p["status"] for p in payments(conn)] == ["paid"]


def test_over_limit_is_held(conn, ref):
    line = work_claim(conn.engine, ref, StubAgent({"CLM-1001": [approve(3750.0)]}), "CLM-1001")
    assert line == "CLM-1001: approve $3,750.00, held for sign-off"
    assert claim(conn)["status"] == "awaiting_approval"
    assert [p["status"] for p in payments(conn)] == ["held"]


def test_exactly_1000_is_paid(conn, ref):
    work_claim(conn.engine, ref, StubAgent({"CLM-1001": [approve(1000.0)]}), "CLM-1001")
    assert [p["status"] for p in payments(conn)] == ["paid"]


@pytest.mark.parametrize("kind", ["decline", "refer"])
def test_decline_and_refer_are_decided_without_payment(conn, ref, kind):
    line = work_claim(conn.engine, ref, StubAgent({"CLM-1001": [Decision(decision=kind, rationale=GOOD)]}), "CLM-1001")
    assert line == f"CLM-1001: {kind}"
    assert claim(conn)["status"] == "decided"
    assert payments(conn) == []


def test_first_failure_goes_back_to_waiting(conn, ref):
    line = work_claim(conn.engine, ref, StubAgent({"CLM-1001": [ValueError("no decision")]}), "CLM-1001")
    row = claim(conn)
    assert (row["status"], row["attempts"]) == ("waiting", 1)
    assert "no decision" in row["last_error"]
    assert line.startswith("CLM-1001: attempt 1 of 2 failed (ValueError: no decision)")
    assert "back in the queue" in line
    assert decisions(conn) == 0


def test_second_failure_leaves_it_for_a_handler(conn, ref):
    agent = StubAgent({"CLM-1001": [ValueError("one"), ValueError("two")]})
    work_claim(conn.engine, ref, agent, "CLM-1001")
    line = work_claim(conn.engine, ref, agent, "CLM-1001")
    row = claim(conn)
    assert (row["status"], row["attempts"]) == ("needs_handler", 2)
    assert "Left for a handler" in line
    assert decisions(conn) == 0 and payments(conn) == []


def test_a_retry_after_one_failure_can_succeed(conn, ref):
    agent = StubAgent({"CLM-1001": [ValueError("one"), approve(420.0)]})
    work_claim(conn.engine, ref, agent, "CLM-1001")
    assert work_claim(conn.engine, ref, agent, "CLM-1001") == "CLM-1001: approve $420.00, paid"
    assert claim(conn)["status"] == "decided"


def test_failed_attempt_drops_its_held_payment(conn, ref):
    def pay_then_fail(c, cid):
        request_payment(c.engine, cid, 2000.0, 1000.0)
        raise ValueError("the agent finished without a decision")

    work_claim(conn.engine, ref, StubAgent({"CLM-1001": [pay_then_fail]}, conn=conn), "CLM-1001")
    assert payments(conn) == []
    assert claim(conn)["status"] == "waiting"


def test_payment_mismatch_is_a_failed_attempt(conn, ref):
    def pay_other_amount(c, cid):
        request_payment(c.engine, cid, 300.0, 1000.0)
        return approve(420.0)

    line = work_claim(conn.engine, ref, StubAgent({"CLM-1001": [pay_other_amount]}, conn=conn), "CLM-1001")
    assert "attempt 1 of 2 failed" in line and "does not match" in line
    assert payments(conn) == [] and decisions(conn) == 0


def test_decided_claim_is_not_worked_again(conn, ref):
    agent = StubAgent({"CLM-1001": [approve(420.0)]})
    work_claim(conn.engine, ref, agent, "CLM-1001")
    line = work_claim(conn.engine, ref, agent, "CLM-1001")
    assert line == "CLM-1001: not worked, the claim is decided."
    assert agent.asked == ["CLM-1001"]
    assert claim(conn)["attempts"] == 0
    assert len(payments(conn)) == 1


def test_unknown_claim_is_not_worked(conn, ref):
    agent = StubAgent()
    assert work_claim(conn.engine, ref, agent, "CLM-9999") == "CLM-9999: not worked, there is no such claim."
    assert agent.asked == []


def test_trace_events_carry_run_id(conn, ref):
    conn.execute("INSERT INTO runs (status, model) VALUES ('running', 'm')")
    work_claim(conn.engine, ref, StubAgent(), "CLM-1001", run_id=1)
    run_ids = {r[0] for r in conn.execute("SELECT run_id FROM trace_events")}
    assert run_ids == {1}
    assert claim(conn)["run_id"] == 1


def test_trace_seq_keeps_counting_across_attempts(conn, ref):
    agent = StubAgent({"CLM-1001": [ValueError("one"), approve(420.0)]})
    work_claim(conn.engine, ref, agent, "CLM-1001")
    work_claim(conn.engine, ref, agent, "CLM-1001")
    seqs = [r[0] for r in conn.execute("SELECT seq FROM trace_events ORDER BY id")]
    assert seqs == list(range(1, len(seqs) + 1))


def test_each_attempt_records_its_usage(conn, ref):
    agent = StubAgent({"CLM-1001": [ValueError("one"), approve(420.0)]})
    work_claim(conn.engine, ref, agent, "CLM-1001")
    work_claim(conn.engine, ref, agent, "CLM-1001")
    usages = [json.loads(r[0]) for r in conn.execute("SELECT content FROM trace_events WHERE kind='usage'")]
    assert len(usages) == 2 and all(u["calls"] == 2 for u in usages)
    assert kinds(conn)[-1] == "usage"


def test_the_error_event_says_which_attempt_failed(conn, ref):
    work_claim(conn.engine, ref, StubAgent({"CLM-1001": [ValueError("no decision")]}), "CLM-1001")
    error = json.loads(conn.execute("SELECT content FROM trace_events WHERE kind='error'").fetchone()[0])
    assert error["message"].startswith("Attempt 1 of 2 failed (ValueError: no decision)")


def test_decision_appears_once_in_trace(conn, ref):
    work_claim(conn.engine, ref, StubAgent({"CLM-1001": [approve(420.0)]}), "CLM-1001")
    assert kinds(conn).count("decision") == 1


def test_rationale_warning_goes_to_trace(conn, ref):
    short = Decision(decision="decline", rationale="No.")
    work_claim(conn.engine, ref, StubAgent({"CLM-1001": [short]}), "CLM-1001")
    assert "warning" in kinds(conn)


def test_max_attempts_one_leaves_it_for_a_handler_at_once(conn, ref):
    work_claim(conn.engine, ref, StubAgent({"CLM-1001": [ValueError("x")]}), "CLM-1001", max_attempts=1)
    assert claim(conn)["status"] == "needs_handler"
