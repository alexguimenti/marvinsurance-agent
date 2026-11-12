"""The agent imports nothing from the app, so `runtime/` with `agent/` beside it is a package on its own."""
import ast
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

AGENT = Path(__file__).resolve().parents[1] / "agent"


def imports_of(path: Path) -> set[str]:
    found = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module.split(".")[0])
    return found


@pytest.mark.parametrize("module", sorted(AGENT.glob("*.py")), ids=lambda p: p.name)
def test_no_agent_module_imports_the_app_or_the_context_layer(module):
    assert not imports_of(module) & {"app", "context"}, module.name


def test_the_agent_package_imports_on_its_own(tmp_path):
    shutil.copytree(AGENT, tmp_path / "agent", ignore=shutil.ignore_patterns("tests", "__pycache__"))
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "SYSTEMROOT", "TEMP", "TMP", "USERPROFILE", "HOME")}
    code = "import agent.entrypoint, agent.worker, agent.dispatcher, agent.improver, agent.models, agent.__main__"
    done = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr


def test_the_agents_settings_match_the_apps_where_they_share_a_name():
    import app.config as app_config
    from agent.settings import AgentSettings, DEFAULT_EFFORT, DEFAULT_MODEL, SCREENING_MODEL

    assert (DEFAULT_MODEL, DEFAULT_EFFORT, SCREENING_MODEL) == (app_config.DEFAULT_MODEL, app_config.DEFAULT_EFFORT, app_config.SCREENING_MODEL)
    shared = set(AgentSettings.model_fields) & set(app_config.Settings.model_fields)
    assert shared == set(AgentSettings.model_fields)  # every field the agent reads is one the app has, under the same name
    for name in shared:
        assert AgentSettings.model_fields[name].default == app_config.Settings.model_fields[name].default, name


def test_the_checkpoint_pool_prepares_nothing(monkeypatch):
    import contextlib

    from agent import checkpoints
    from agent.settings import AgentSettings

    seen = {}

    class FakePool:
        def __init__(self, url, **kwargs):
            seen.update(kwargs)

        def __enter__(self):
            raise StopIteration  # the pool's arguments are all this test wants

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(checkpoints, "ConnectionPool", FakePool)
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@pooler.example:6543/postgres")
    with contextlib.suppress(StopIteration, RuntimeError):
        with checkpoints.make_checkpoints(AgentSettings(_env_file=None)):
            pass
    assert seen["kwargs"]["prepare_threshold"] is None


def test_the_backend_address_reads_UNDERCOVER_API_first(monkeypatch):
    from agent import connection

    monkeypatch.delenv("UNDERCOVER_API", raising=False)
    monkeypatch.setenv("UNDERCOVER_BACKEND", "http://laptop.example:8000/")
    assert connection.backend_url() == "http://laptop.example:8000"
    monkeypatch.setenv("UNDERCOVER_API", "https://app.lambda-url.example/")
    assert connection.backend_url() == "https://app.lambda-url.example"
