from pathlib import Path

import pytest

DATA_DIR = Path(__file__).resolve().parents[2] / "data"

# Set in the shell, these would point a test at Supabase, Langfuse or a running agent service.
OUTSIDE_WORLD = ("DATABASE_URL", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_BASE_URL", "UNDERCOVER_AGENT")


@pytest.fixture(autouse=True)
def no_outside_world(monkeypatch):
    for name in OUTSIDE_WORLD:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def data_dir() -> str:
    return str(DATA_DIR)


@pytest.fixture
def db_path(tmp_path) -> str:
    return str(tmp_path / "test.db")
