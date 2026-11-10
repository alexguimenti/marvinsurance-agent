"""Each statement is a trip to Supabase, about 220 ms from here, and a sign-off must show as paid within five
seconds. These tests keep the path from the sign-off to the payment short."""
from contextlib import contextmanager

import pytest
from sqlalchemy import event

from agent.worker import invoke, work_claim
from app.claims import needs_sign_off
from app.config import Settings
from app.db import make_engine, warm_up
from app.payments import sign_off
from tests.fakes import agent_env, approve, stub

CLAIM = "CLM-1003"


@contextmanager
def statements(engine):
    seen = []

    def count(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    event.listen(engine, "before_cursor_execute", count)
    try:
        yield seen
    finally:
        event.remove(engine, "before_cursor_execute", count)


@pytest.fixture
def held(db_path, data_dir):
    with agent_env(db_path, data_dir, stub({CLAIM: [approve(3750.0)]})) as env:
        work_claim(env, CLAIM)
        yield env


def test_an_engine_opens_no_transaction_per_operation(db_path):
    engine = make_engine(Settings(_env_file=None, db_path=db_path), max_connections=1)
    assert engine.get_execution_options()["isolation_level"] == "AUTOCOMMIT"
    assert engine.pool._pre_ping is False


def test_asking_whether_a_sign_off_is_needed_is_one_statement(held):
    with statements(held.engine) as seen:
        assert needs_sign_off(held.engine, held.ref, CLAIM) is True
    assert len(seen) == 1


def test_a_sign_off_pays_in_three_statements(held):
    """The payment, the claim, the trace: all of it by the sign-off, so a handler sees it paid at once."""
    with statements(held.engine) as seen:
        payment = sign_off(held.engine, held.ref, f"PAY-{CLAIM}", "HND-01")
    assert payment.status == "paid" and len(seen) == 3
    assert "UPDATE payments" in seen[0]


def test_a_paused_claim_saves_no_checkpoint_before_it_is_paid(held):
    """A saved step is several more trips: waiting and paying are one step, so nothing is saved in between."""
    config = {"configurable": {"thread_id": f"{CLAIM}-1"}}
    assert held.graph.get_state(config).next == ("pay",)
    before = len(list(held.graph.checkpointer.list(config)))
    sign_off(held.engine, held.ref, f"PAY-{CLAIM}", "HND-01")
    invoke(held, CLAIM, f"{CLAIM}-1", None, None)  # the agent resumed: it finds the claim paid
    assert held.graph.get_state(config).next == ()
    assert len(list(held.graph.checkpointer.list(config))) == before + 1  # only the one after the node


def test_warm_up_opens_every_connection(db_path):
    engine = make_engine(Settings(_env_file=None, db_path=db_path), max_connections=3)
    warm_up(engine, 3)
    assert engine.pool.checkedin() == 3
