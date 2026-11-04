import json

import pytest

from agent.worker import work_claim
from app import runs
from app.db import init_schema
from app.reference import Reference
from app.seed import seed_claims
from tests.fakes import agent_env, connect, stub

FIVE = ["CLM-1001", "CLM-1002", "CLM-1004", "CLM-1006", "CLM-1007"]
USAGE = {"calls": 3, "input_tokens": 100, "output_tokens": 10, "reasoning_tokens": 5, "cost_usd": 0.002}


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
def env_for(db_path, data_dir):
    opened = []

    def make(outcomes=None):
        cm = agent_env(db_path, data_dir, stub(outcomes))
        env = cm.__enter__()
        opened.append(cm)
        return env

    yield make
    for cm in reversed(opened):
        cm.__exit__(None, None, None)


def items(conn, run_id):
    return runs.todo_list(conn.engine, run_id)


def test_second_run_is_refused_while_one_runs(conn):
    runs.start_run(conn.engine, "m")
    with pytest.raises(runs.RunActive):
        runs.start_run(conn.engine, "m")


def test_a_new_run_can_start_after_the_last_finished(conn):
    first = runs.start_run(conn.engine, "m")
    runs.finish(conn.engine, first, "finished")
    assert runs.start_run(conn.engine, "m") == first + 1


def test_set_and_update_todo(conn):
    run_id = runs.start_run(conn.engine, "m")
    listed = runs.set_todo(conn.engine, run_id, [{"title": "Work CLM-1001", "claim_id": "CLM-1001"},
                                          {"title": "Write the summary"}])
    assert [(i["position"], i["title"], i["status"]) for i in listed] == [
        (1, "Work CLM-1001", "todo"), (2, "Write the summary", "todo")]
    summary_item = listed[1]["item_id"]
    updated = runs.update_todo(conn.engine, run_id, summary_item, "in_progress", "drafting")
    assert (updated["status"], updated["note"]) == ("in_progress", "drafting")


def test_set_todo_replaces_the_plan(conn):
    run_id = runs.start_run(conn.engine, "m")
    runs.set_todo(conn.engine, run_id, [{"title": "a"}, {"title": "b"}])
    runs.set_todo(conn.engine, run_id, [{"title": "c"}])
    assert [i["title"] for i in items(conn, run_id)] == ["c"]


@pytest.mark.parametrize("item_id,new_status", [(999, "done"), (None, "finished")])
def test_update_todo_rejects_unknown_status_and_item(conn, item_id, new_status):
    run_id = runs.start_run(conn.engine, "m")
    real = runs.set_todo(conn.engine, run_id, [{"title": "a"}])[0]["item_id"]
    with pytest.raises(ValueError):
        runs.update_todo(conn.engine, run_id, item_id or real, new_status)


def test_item_cannot_be_done_for_undecided_claim(conn):
    run_id = runs.start_run(conn.engine, "m")
    item = runs.set_todo(conn.engine, run_id, [{"title": "Work CLM-1001", "claim_id": "CLM-1001"}])[0]
    with pytest.raises(ValueError):
        runs.update_todo(conn.engine, run_id, item["item_id"], "done")


def test_set_todo_keeps_what_is_done(conn):
    run_id = runs.start_run(conn.engine, "m")
    planned = runs.set_todo(conn.engine, run_id, [{"title": "a", "claim_id": "CLM-1001"}, {"title": "b"}])
    runs.todo_for_claim(conn.engine, run_id, "CLM-1001", "done")
    runs.set_todo(conn.engine, run_id, [{"title": "again", "claim_id": "CLM-1001"}, {"title": "c"}])
    assert [(i["title"], i["status"]) for i in items(conn, run_id)] == [("a", "done"), ("c", "todo")]
    assert planned[0]["position"] == 1


def test_work_claim_ticks_its_todo_item(conn, env_for):
    env = env_for({"CLM-1002": [ValueError("one"), ValueError("two")]})
    run_id = runs.start_run(conn.engine, "m")
    runs.set_todo(conn.engine, run_id, [{"title": f"Work {c}", "claim_id": c} for c in ("CLM-1001", "CLM-1002")])
    work_claim(env, "CLM-1001", run_id)
    work_claim(env, "CLM-1002", run_id)
    assert [i["status"] for i in items(conn, run_id)] == ["done", "todo"]
    work_claim(env, "CLM-1002", run_id)
    last = items(conn, run_id)[1]
    assert last["status"] == "left_for_handler" and "two" in last["note"]


def test_work_claim_adds_an_item_the_plan_did_not_have(conn, env_for):
    env = env_for()
    run_id = runs.start_run(conn.engine, "m")
    work_claim(env, "CLM-1001", run_id)
    assert [(i["claim_id"], i["status"]) for i in items(conn, run_id)] == [("CLM-1001", "done")]


def test_cap_counts_claims_not_attempts(conn, env_for):
    env = env_for({"CLM-1001": [ValueError("one")]})
    run_id = runs.start_run(conn.engine, "m", cap=2)
    work_claim(env, "CLM-1001", run_id)
    work_claim(env, "CLM-1001", run_id)
    assert not runs.cap_reached(conn.engine, run_id)
    work_claim(env, "CLM-1002", run_id)
    assert runs.cap_reached(conn.engine, run_id)


def test_in_scope_waiting_follows_the_scope(conn):
    run_id = runs.start_run(conn.engine, "m", scope=["CLM-1004", "CLM-1002"])
    assert runs.in_scope_waiting(conn.engine, run_id) == ["CLM-1002", "CLM-1004"]
    runs.finish(conn.engine, run_id, "finished")
    whole = runs.start_run(conn.engine, "m")
    assert len(runs.in_scope_waiting(conn.engine, whole)) == 25


def test_missed_claims_lists_untried_and_retries_within_cap(conn, env_for):
    env = env_for({"CLM-1002": [ValueError("one")]})
    run_id = runs.start_run(conn.engine, "m", cap=3, scope=FIVE)
    assert runs.missed_claims(conn.engine, run_id) == FIVE[:3]
    for cid in FIVE[:3]:
        work_claim(env, cid, run_id)
    assert runs.missed_claims(conn.engine, run_id) == ["CLM-1002"]  # back in the queue, a retry is due
    work_claim(env, "CLM-1002", run_id)
    assert runs.missed_claims(conn.engine, run_id) == []


def test_a_claim_left_for_a_handler_is_not_missed(conn, env_for):
    env = env_for({"CLM-1001": [ValueError("one"), ValueError("two")]})
    run_id = runs.start_run(conn.engine, "m", scope=["CLM-1001"])
    work_claim(env, "CLM-1001", run_id)
    work_claim(env, "CLM-1001", run_id)
    assert runs.missed_claims(conn.engine, run_id) == []


def test_finish_leaves_claims_alone_and_sums_usage(conn, env_for):
    env = env_for()
    run_id = runs.start_run(conn.engine, "m", scope=["CLM-1001", "CLM-1002"])
    work_claim(env, "CLM-1001", run_id)
    conn.execute("UPDATE claims SET status='in_progress', run_id=? WHERE claim_id='CLM-1002'", (run_id,))
    runs.run_event(conn.engine, run_id, "usage", USAGE)

    runs.finish(conn.engine, run_id, "failed", error="stopped")

    assert conn.execute("SELECT status FROM claims WHERE claim_id='CLM-1002'").fetchone()[0] == "in_progress"
    row = conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
    assert (row["status"], row["error"]) == ("failed", "stopped")
    assert row["finished_at"] is not None
    usage = json.loads(row["usage"])
    assert usage["calls"] == 4 and usage["cost_usd"] == pytest.approx(0.0025)


def test_run_events_are_numbered(conn):
    run_id = runs.start_run(conn.engine, "m")
    runs.run_event(conn.engine, run_id, "message", {"text": "a"})
    runs.run_event(conn.engine, run_id, "message", {"text": "b"})
    assert [e["seq"] for e in runs.run_events(conn.engine, run_id)] == [1, 2]


def test_todo_changes_made_by_work_claim_are_run_events(conn, env_for):
    env = env_for()
    run_id = runs.start_run(conn.engine, "m")
    work_claim(env, "CLM-1001", run_id)
    todo = [e["content"] for e in runs.run_events(conn.engine, run_id) if e["kind"] == "todo"]
    assert [(t["claim_id"], t["status"]) for t in todo] == [("CLM-1001", "in_progress"), ("CLM-1001", "done")]
