import httpx
import pytest

from agent import __main__ as cli
from app.db import init_schema, make_engine
from app.config import Settings
from tests.fakes import RawDb


@pytest.fixture
def cli_env(db_path, data_dir, monkeypatch):
    monkeypatch.setenv("AGENT_PROVIDER", "scripted")
    monkeypatch.setenv("DB_PATH", db_path)
    monkeypatch.setenv("DATA_DIR", data_dir)
    monkeypatch.setattr(cli, "load_dotenv", lambda *a, **k: None)
    return db_path


def down(*args, **kwargs):
    raise httpx.ConnectError("not running")


def decided(db_path):
    engine = make_engine(Settings(_env_file=None, db_path=db_path), max_connections=1)
    return RawDb(engine).execute("SELECT COUNT(*) FROM claims WHERE status != 'waiting'").fetchone()[0]


def test_cli_runs_in_process_when_the_service_is_down(cli_env, monkeypatch, capsys):
    monkeypatch.setattr(cli.httpx, "get", down)
    assert cli.main(["--limit", "2"]) == 0
    assert decided(cli_env) == 2
    assert "Worked 2 claims" in capsys.readouterr().out


def test_cli_works_selected_claims(cli_env, monkeypatch):
    monkeypatch.setattr(cli.httpx, "get", down)
    assert cli.main(["--claims", "CLM-1004,CLM-1006"]) == 0
    assert decided(cli_env) == 2


def test_cli_with_no_limit_works_every_waiting_claim(cli_env, monkeypatch):
    monkeypatch.setattr(cli.httpx, "get", down)
    assert cli.main([]) == 0
    assert decided(cli_env) == 25


def test_cli_refuses_when_a_run_is_alive(cli_env, monkeypatch, capsys):
    monkeypatch.setattr(cli.httpx, "get", down)
    engine = make_engine(Settings(_env_file=None, db_path=cli_env), max_connections=1)
    init_schema(engine)
    RawDb(engine).execute("INSERT INTO runs (status, model, heartbeat_at) VALUES ('running', 'm', CURRENT_TIMESTAMP)")
    assert cli.main(["--limit", "1"]) == 1
    assert "already in progress" in capsys.readouterr().out


def test_cli_hands_over_when_the_service_is_up(cli_env, monkeypatch, capsys):
    engine = make_engine(Settings(_env_file=None, db_path=cli_env), max_connections=1)
    init_schema(engine)
    posted = []

    def post(url, json, timeout):
        posted.append((url, json))
        # the service's run, seen from here
        RawDb(engine).execute("INSERT INTO runs (status, model, summary) VALUES ('finished', 'm', 'Done over there.')")
        return httpx.Response(202, json={"run_id": 1}, request=httpx.Request("POST", url))

    monkeypatch.setattr(cli.httpx, "get", lambda *a, **k: httpx.Response(200, json={}))
    monkeypatch.setattr(cli.httpx, "post", post)
    assert cli.main(["--limit", "3"]) == 0
    assert posted == [("http://127.0.0.1:8002/runs", {"limit": 3})]
    assert "Done over there." in capsys.readouterr().out
