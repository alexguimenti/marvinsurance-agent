from pathlib import Path

import pytest

DATA_DIR = Path(__file__).resolve().parents[2] / "data"

# Set in the shell, these would point a test at Supabase, Langfuse or a running agent service.
OUTSIDE_WORLD = ("DATABASE_URL", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_BASE_URL", "UNDERCOVER_AGENT")


@pytest.fixture(autouse=True)
def no_outside_world(monkeypatch):
    for name in OUTSIDE_WORLD:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def no_model_calls(request, monkeypatch):
    """Tests never call a model. A request that gets this far is a bug in the test or the code."""
    if request.node.get_closest_marker("live") or "test_openrouter_smoke" in request.node.nodeid:
        return

    def refuse(self, *args, **kwargs):
        raise AssertionError("a test tried to call a real model")

    monkeypatch.setattr("langchain_openai.ChatOpenAI._generate", refuse)
    monkeypatch.setattr("langchain_openai.ChatOpenAI._stream", refuse)


@pytest.fixture
def data_dir() -> str:
    return str(DATA_DIR)


@pytest.fixture
def db_path(tmp_path) -> str:
    return str(tmp_path / "test.db")
