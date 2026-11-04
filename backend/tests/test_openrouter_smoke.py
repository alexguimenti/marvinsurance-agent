import os

import pytest

from agent.worker import open_env, work_claim
from app.config import Settings
from app.db import init_schema, make_engine
from app.reference import Reference
from app.seed import seed_claims
from tests.fakes import RawDb

settings = Settings()

# Tests never call a model unless asked: set RUN_LIVE_SMOKE=1 (and the key) to run this one.
pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE_SMOKE") != "1" or not settings.openrouter_api_key,
    reason="live smoke test: set RUN_LIVE_SMOKE=1 and OPENROUTER_API_KEY",
)


def test_real_model_decides_one_routine_claim(tmp_path, data_dir):
    config = settings.model_copy(update={"agent_provider": "openrouter", "db_path": str(tmp_path / "smoke.db")})
    engine = make_engine(config, max_connections=1)
    init_schema(engine)
    seed_claims(engine, Reference(data_dir))
    with open_env(config) as env:
        line = work_claim(env, "CLM-1001")
    assert line.startswith("CLM-1001: ")
    db = RawDb(engine)
    assert len(db.execute("SELECT kind FROM trace_events WHERE claim_id='CLM-1001'")) >= 2
