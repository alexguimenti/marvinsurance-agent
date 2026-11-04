import os

import pytest

from app.agent import build_agent
from app.config import Settings
from app.db import init_schema
from tests.fakes import connect
from app.reference import Reference
from app.runner import work_claim
from app.seed import seed_claims

settings = Settings()

# Tests never call a model unless asked: set RUN_LIVE_SMOKE=1 (and the key) to run this one.
pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE_SMOKE") != "1" or not settings.openrouter_api_key,
    reason="live smoke test: set RUN_LIVE_SMOKE=1 and OPENROUTER_API_KEY",
)


def test_real_model_decides_one_routine_claim(tmp_path, data_dir):
    conn = connect(str(tmp_path / "smoke.db"))
    init_schema(conn.engine)
    ref = Reference(data_dir)
    seed_claims(conn.engine, ref)
    agent = build_agent(settings.model_copy(update={"agent_provider": "openrouter"}), conn, ref)

    line = work_claim(conn.engine, ref, agent, "CLM-1001")

    assert line.startswith("CLM-1001: ")
    events = conn.execute("SELECT kind FROM trace_events WHERE claim_id='CLM-1001'").fetchall()
    assert len(events) >= 2
    stored = conn.execute("SELECT model FROM decisions WHERE claim_id='CLM-1001'").fetchone()
    assert stored["model"] == settings.agent_model
