"""The run record: one at a time, its claims and where each stands, and how it ends."""
import pytest

from app import runs
from app.db import init_schema, make_engine
from app.config import Settings
from app.reference import Reference
from app.seed import seed_claims
from tests.fakes import RawDb


@pytest.fixture
def engine(db_path, data_dir):
    engine = make_engine(Settings(_env_file=None, db_path=db_path), max_connections=2)
    init_schema(engine)
    seed_claims(engine, Reference(data_dir))
    yield engine
    engine.dispose()


def test_a_run_records_its_kind_n_model_and_effort(engine):
    run_id = runs.start_dispatch(engine, 5, "some/model", "low")
    row = runs.get_run(engine, run_id)
    assert (row["kind"], row["status"], row["workers"], row["model"], row["reasoning_effort"]) == (
        "dispatch", "running", 5, "some/model", "low")


def test_no_effort_is_recorded_as_none(engine):
    assert runs.get_run(engine, runs.start_dispatch(engine, 3, "other/model", None))["reasoning_effort"] == "none"


def test_a_new_run_can_start_after_the_last_finished(engine):
    first = runs.start_dispatch(engine, 3, "m", "low")
    runs.finish_run(engine, first)
    assert runs.get_run(engine, first)["status"] == "finished"
    assert runs.start_dispatch(engine, 3, "m", "low") == first + 1


def test_a_dispatcher_that_breaks_ends_its_run_stopped_with_the_reason(engine):
    run_id = runs.start_dispatch(engine, 3, "m", "low")
    runs.finish_run(engine, run_id, error="ValueError: no")
    row = runs.get_run(engine, run_id)
    assert (row["status"], row["error"]) == ("stopped", "ValueError: no")


def test_latest_run_is_by_kind(engine):
    first = runs.start_dispatch(engine, 3, "m", "low")
    runs.finish_run(engine, first)
    assert runs.latest_run(engine, "dispatch")["run_id"] == first
    assert runs.latest_run(engine, "evaluation") is None


def test_a_claims_start_is_its_first(engine):
    run_id = runs.start_dispatch(engine, 3, "m", "low")
    runs.add_run_claims(engine, run_id, ["CLM-1001", "CLM-1002"])
    runs.claim_started(engine, run_id, "CLM-1001")
    first = runs.run_claims_view(engine, run_id)[0]["started_at"]
    runs.claim_started(engine, run_id, "CLM-1001")  # a retry
    view = runs.run_claims_view(engine, run_id)
    assert view[0]["started_at"] == first and view[1]["started_at"] is None
    assert [r["claim_id"] for r in view] == ["CLM-1001", "CLM-1002"]


def test_the_summary_counts_what_the_records_say(engine):
    raw = RawDb(engine)
    run_id = runs.start_dispatch(engine, 3, "m", "low")
    runs.add_run_claims(engine, run_id, ["CLM-1001", "CLM-1002", "CLM-1003", "CLM-1004", "CLM-1005"])
    for claim_id, decision, payout in (("CLM-1001", "approve", 100.0), ("CLM-1002", "decline", 0.0),
                                       ("CLM-1003", "approve", 3750.0)):
        raw.execute("INSERT INTO decisions (claim_id, decision, payout, calculation, rationale, citations, model) "
                    "VALUES (?, ?, ?, '[]', 'r', '[]', 'm')", (claim_id, decision, payout))
    raw.execute("INSERT INTO payments (payment_id, claim_id, amount, status) VALUES ('PAY-CLM-1003', 'CLM-1003', 3750, 'held')")
    raw.execute("UPDATE claims SET status='decided' WHERE claim_id IN ('CLM-1001', 'CLM-1002')")
    raw.execute("UPDATE claims SET status='awaiting_approval' WHERE claim_id='CLM-1003'")
    raw.execute("UPDATE claims SET status='needs_handler' WHERE claim_id='CLM-1004'")
    raw.execute("UPDATE claims SET status='in_progress' WHERE claim_id='CLM-1005'")
    assert runs.run_summary(engine, run_id) == {
        "approved": 2, "declined": 1, "referred": 0,
        "awaiting_sign_off": [{"claim_id": "CLM-1003", "payout": 3750.0}],
        "left_for_handler": ["CLM-1004"], "in_progress": ["CLM-1005"],
    }
