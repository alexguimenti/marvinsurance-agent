"""Each claim run saves its progress through a saver of its own, and claim agents wait for a connection."""
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import text

from agent.worker import work_claim
from app.claims import get_claim
from app.config import Settings
from app.db import make_engine
from tests.fakes import StubModel, agent_env, approve, stub


@pytest.fixture
def env(db_path, data_dir):
    with agent_env(db_path, data_dir, stub()) as opened:
        yield opened


def test_each_claim_run_gets_a_saver_of_its_own(env):
    with env.claim_graph() as a, env.claim_graph() as b:
        assert a.checkpointer is not b.checkpointer


class SlowStub(StubModel):
    """Takes its time over every reply, and counts how many replies are being written at once."""

    running: int = 0
    peak: int = 0
    lock: object = None

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        with self.lock:
            self.running += 1
            self.peak = max(self.peak, self.running)
        try:
            time.sleep(0.2)
            return super()._generate(messages, stop, run_manager, **kwargs)
        finally:
            with self.lock:
                self.running -= 1


def test_two_claims_worked_at_once_both_finish(db_path, data_dir):
    model = SlowStub(outcomes={}, default=approve(100.0), asked=[], shown=[], current={}, calls=0, lock=threading.Lock())
    with agent_env(db_path, data_dir, model) as env:
        with ThreadPoolExecutor(2) as pool:
            lines = list(pool.map(lambda c: work_claim(env, c), ["CLM-1001", "CLM-1002"]))
        assert lines == ["CLM-1001: approve $100.00, paid", "CLM-1002: approve $100.00, paid"]
        assert model.peak == 2  # they really did overlap
        assert get_claim(env.engine, "CLM-1002")["status"] == "decided"


def test_a_claim_agent_waits_for_a_connection_rather_than_fail(db_path):
    engine = make_engine(Settings(_env_file=None, db_path=db_path), max_connections=1)

    def busy(_):
        with engine.connect() as conn:  # holds the only connection for a while
            value = conn.execute(text("SELECT 1")).scalar_one()
            time.sleep(0.1)
            return value

    with ThreadPoolExecutor(5) as pool:  # five at once on one connection
        assert list(pool.map(busy, range(5))) == [1] * 5


def test_make_engine_waits_for_a_connection(db_path):
    assert make_engine(Settings(_env_file=None, db_path=db_path), max_connections=4).pool._timeout == 600
