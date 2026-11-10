from pathlib import Path

import pytest

from tests.fakes import TEST_KEY

DATA_DIR = Path(__file__).resolve().parents[2] / "data"

# Set in the shell, these would point a test at Supabase, Langfuse, a running agent service or another model.
OUTSIDE_WORLD = ("DATABASE_URL", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_BASE_URL", "UNDERCOVER_AGENT",
                 "MODEL_NAME", "AGENT_MODEL", "AGENT_REASONING_EFFORT", "VITE_SUPABASE_URL", "OPENROUTER_DECISIONS_URL", "UNDERCOVER_API_KEY", "UNDERCOVER_BACKEND", "AWS_LAMBDA_FUNCTION_NAME", "DASHBOARD_DIR")


@pytest.fixture(autouse=True)
def no_outside_world(request, monkeypatch):
    for name in OUTSIDE_WORLD:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DASHBOARD_DIR", "no-such-dashboard-build")  # a build on this computer is no business of a test
    monkeypatch.setenv("UNDERCOVER_API_KEY", TEST_KEY)  # a test that wants no key deletes it
    if "test_openrouter_smoke" not in request.node.nodeid:  # the live smoke test needs the key; nothing else does
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)


@pytest.fixture(autouse=True)
def no_model_calls(request, monkeypatch):
    """Tests never call a model. A request that gets this far is a bug in the test or the code."""
    if "test_openrouter_smoke" in request.node.nodeid:
        return

    def refuse(self, *args, **kwargs):
        raise AssertionError("a test tried to call a real model")

    monkeypatch.setattr("langchain_openai.ChatOpenAI._generate", refuse)

    def refuse_judge(settings):
        raise AssertionError("a test tried to call the judge")

    monkeypatch.setattr("app.judge.make_client", refuse_judge)
    monkeypatch.setattr("langchain_openai.ChatOpenAI._stream", refuse)


@pytest.fixture
def data_dir() -> str:
    return str(DATA_DIR)


@pytest.fixture
def db_path(tmp_path) -> str:
    return str(tmp_path / "test.db")
