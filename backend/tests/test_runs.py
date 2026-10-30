import json

import pytest

from app import runs
from app.agent import Decision
from app.db import connect, init_schema
from app.reference import Reference
from app.runner import work_claim
from app.seed import seed_claims
from tests.fakes import STUB_USAGE, StubAgent

GOOD = "The loss is covered. Section 10 of the wording sets the payment."
FIVE = ["CLM-1001", "CLM-1002", "CLM-1004", "CLM-1006", "CLM-1007"]


@pytest.fixture
def ref(data_dir):
    return Reference(data_dir)


@pytest.fixture
def conn(db_path, ref):
    c = connect(db_path)
    init_schema(c)
    seed_claims(c, ref)
    return c


def status(conn, cid):
    return conn.execute("SELECT status FROM claims WHERE claim_id=?", (cid,)).fetchone()[0]


def items(conn, run_id):
    return runs.todo_list(conn, run_id)


def test_second_run_is_refused_while_one_runs(conn):
    runs.start_run(conn, "m")
    with pytest.raises(runs.RunActive):
        runs.start_run(conn, "m")


def test_a_new_run_can_start_after_the_last_finished(conn):
    first = runs.start_run(conn, "m")
    runs.finish(conn, first, "finished")
    assert runs.start_run(conn, "m") == first + 1


def test_set_and_update_todo(conn):
    run_id = runs.start_run(conn, "m")
    listed = runs.set_todo(conn, run_id, [{"title": "Work CLM-1001", "claim_id": "CLM-1001"},
                                          {"title": "Write the summary"}])
    assert [(i["position"], i["title"], i["status"]) for i in listed] == [
        (1, "Work CLM-1001", "todo"), (2, "Write the summary", "todo")]
    summary_item = listed[1]["item_id"]
    updated = runs.update_todo(conn, run_id, summary_item, "in_progress", "drafting")
    assert (updated["status"], updated["note"]) == ("in_progress", "drafting")


def test_set_todo_replaces_the_plan(conn):
    run_id = runs.start_run(conn, "m")
    runs.set_todo(conn, run_id, [{"title": "a"}, {"title": "b"}])
    runs.set_todo(conn, run_id, [{"title": "c"}])
    assert [i["title"] for i in items(conn, run_id)] == ["c"]


@pytest.mark.parametrize("item_id,new_status", [(999, "done"), (None, "finished")])
def test_update_todo_rejects_unknown_status_and_item(conn, item_id, new_status):
    run_id = runs.start_run(conn, "m")
    real = runs.set_todo(conn, run_id, [{"title": "a"}])[0]["item_id"]
    with pytest.raises(ValueError):
        runs.update_todo(conn, run_id, item_id or real, new_status)


def test_item_cannot_be_done_for_undecided_claim(conn):
    run_id = runs.start_run(conn, "m")
    item = runs.set_todo(conn, run_id, [{"title": "Work CLM-1001", "claim_id": "CLM-1001"}])[0]
    with pytest.raises(ValueError):
        runs.update_todo(conn, run_id, item["item_id"], "done")


def test_work_claim_ticks_its_todo_item(conn, ref):
    run_id = runs.start_run(conn, "m")
    runs.set_todo(conn, run_id, [{"title": f"Work {c}", "claim_id": c} for c in ("CLM-1001", "CLM-1002")])
    agent = StubAgent({"CLM-1002": [ValueError("one"), ValueError("two")]})
    work_claim(conn, ref, agent, "CLM-1001", run_id)
    work_claim(conn, ref, agent, "CLM-1002", run_id)
    assert [i["status"] for i in items(conn, run_id)] == ["done", "todo"]
    work_claim(conn, ref, agent, "CLM-1002", run_id)
    last = items(conn, run_id)[1]
    assert last["status"] == "left_for_handler" and "two" in last["note"]


def test_work_claim_adds_an_item_the_plan_did_not_have(conn, ref):
    run_id = runs.start_run(conn, "m")
    work_claim(conn, ref, StubAgent(), "CLM-1001", run_id)
    assert [(i["claim_id"], i["status"]) for i in items(conn, run_id)] == [("CLM-1001", "done")]


def test_cap_counts_claims_not_attempts(conn, ref):
    run_id = runs.start_run(conn, "m", cap=2)
    agent = StubAgent({"CLM-1001": [ValueError("one")]})
    work_claim(conn, ref, agent, "CLM-1001", run_id)
    work_claim(conn, ref, agent, "CLM-1001", run_id)
    assert not runs.cap_reached(conn, run_id)
    work_claim(conn, ref, agent, "CLM-1002", run_id)
    assert runs.cap_reached(conn, run_id)


def test_in_scope_waiting_follows_the_scope(conn):
    run_id = runs.start_run(conn, "m", scope=["CLM-1004", "CLM-1002"])
    assert runs.in_scope_waiting(conn, run_id) == ["CLM-1002", "CLM-1004"]
    runs.finish(conn, run_id, "finished")
    whole = runs.start_run(conn, "m")
    assert len(runs.in_scope_waiting(conn, whole)) == 25


def test_missed_claims_lists_untried_and_retries_within_cap(conn, ref):
    run_id = runs.start_run(conn, "m", cap=3, scope=FIVE)
    assert runs.missed_claims(conn, run_id) == FIVE[:3]
    agent = StubAgent({"CLM-1002": [ValueError("one")]})
    for cid in FIVE[:3]:
        work_claim(conn, ref, agent, cid, run_id)
    assert runs.missed_claims(conn, run_id) == ["CLM-1002"]  # back in the queue, a retry is due
    work_claim(conn, ref, agent, "CLM-1002", run_id)
    assert runs.missed_claims(conn, run_id) == []


def test_a_claim_left_for_a_handler_is_not_missed(conn, ref):
    run_id = runs.start_run(conn, "m", scope=["CLM-1001"])
    agent = StubAgent({"CLM-1001": [ValueError("one"), ValueError("two")]})
    work_claim(conn, ref, agent, "CLM-1001", run_id)
    work_claim(conn, ref, agent, "CLM-1001", run_id)
    assert runs.missed_claims(conn, run_id) == []


def test_finish_puts_in_progress_back_and_sums_usage(conn, ref):
    run_id = runs.start_run(conn, "m", scope=["CLM-1001", "CLM-1002"])
    work_claim(conn, ref, StubAgent(), "CLM-1001", run_id)
    conn.execute("UPDATE claims SET status='in_progress', run_id=? WHERE claim_id='CLM-1002'", (run_id,))
    runs.run_event(conn, run_id, "usage", {**STUB_USAGE, "calls": 3, "cost_usd": 0.002})

    runs.finish(conn, run_id, "failed", error="stopped")

    assert status(conn, "CLM-1002") == "waiting"
    row = conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
    assert (row["status"], row["error"]) == ("failed", "stopped")
    assert row["finished_at"] is not None
    usage = json.loads(row["usage"])
    assert usage["calls"] == 5 and usage["cost_usd"] == pytest.approx(0.003)


def test_run_events_are_numbered(conn):
    run_id = runs.start_run(conn, "m")
    runs.run_event(conn, run_id, "message", {"text": "a"})
    runs.run_event(conn, run_id, "message", {"text": "b"})
    assert [e["seq"] for e in runs.run_events(conn, run_id)] == [1, 2]


def test_recover_after_restart(conn):
    run_id = runs.start_run(conn, "m")
    conn.execute("INSERT INTO trace_events (claim_id, seq, kind, content) VALUES ('CLM-1001', 1, 'status', '{}')")
    conn.execute("UPDATE claims SET status='in_progress', run_id=? WHERE claim_id='CLM-1001'", (run_id,))
    conn.commit()

    runs.recover_after_restart(conn)

    row = conn.execute("SELECT status, error FROM runs WHERE run_id=?", (run_id,)).fetchone()
    assert tuple(row) == ("failed", "the server stopped during the run")
    assert status(conn, "CLM-1001") == "waiting"
    trace = [json.loads(r[0]) for r in conn.execute("SELECT content FROM trace_events WHERE claim_id='CLM-1001' ORDER BY seq")]
    assert len(trace) == 2 and trace[-1]["note"] == "recovered after a restart"
    assert runs.start_run(conn, "m")  # nothing is running any more


def test_decided_claims_keep_their_decision_after_recovery(conn, ref):
    run_id = runs.start_run(conn, "m")
    work_claim(conn, ref, StubAgent({"CLM-1001": [Decision(decision="decline", rationale=GOOD)]}), "CLM-1001", run_id)
    runs.recover_after_restart(conn)
    assert status(conn, "CLM-1001") == "decided"
