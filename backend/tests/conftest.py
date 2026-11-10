import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn

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


@pytest.fixture(scope="session")
def context_server_url():
    """The context layer, served on this computer for the whole run: what the agent reaches at UNDERCOVER_CONTEXT."""
    from context.server import build_server

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(build_server().streamable_http_app(), host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.02)
    yield f"http://127.0.0.1:{port}/mcp"
    server.should_exit = True
    thread.join(5)


@pytest.fixture(autouse=True)
def context_layer(monkeypatch, context_server_url, no_outside_world):
    """Every test that reaches the context layer reaches this one, never a server of the developer's."""
    monkeypatch.setenv("UNDERCOVER_CONTEXT", context_server_url)


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
