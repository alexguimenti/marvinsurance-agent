import pytest

from app.agent import Decision, build_agent
from app.config import Settings
from app.db import connect, init_schema
from app.reference import Reference
from app.runner import run_claim
from app.seed import seed_claims

settings = Settings()

pytestmark = pytest.mark.skipif(
    not settings.openrouter_api_key, reason="OPENROUTER_API_KEY is not set"
)


def test_real_model_decides_one_routine_claim(tmp_path, data_dir):
    conn = connect(str(tmp_path / "smoke.db"))
    init_schema(conn)
    ref = Reference(data_dir)
    seed_claims(conn, ref)
    agent = build_agent(settings.model_copy(update={"agent_provider": "openrouter"}), conn, ref)

    decision = run_claim(conn, ref, agent, "CLM-1001")

    assert isinstance(decision, Decision)
    events = conn.execute("SELECT kind FROM trace_events WHERE claim_id='CLM-1001'").fetchall()
    assert len(events) >= 2
    stored = conn.execute("SELECT model FROM decisions WHERE claim_id='CLM-1001'").fetchone()
    assert stored["model"] == settings.agent_model
