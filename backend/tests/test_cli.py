import pytest

from agent import __main__ as cli
from app.config import Settings
from app.db import make_engine
from tests.fakes import RawDb


@pytest.fixture
def cli_env(db_path, data_dir, monkeypatch):
    monkeypatch.setenv("AGENT_PROVIDER", "scripted")
    monkeypatch.setenv("DB_PATH", db_path)
    monkeypatch.setenv("DATA_DIR", data_dir)
    monkeypatch.setattr(cli, "load_dotenv", lambda *a, **k: None)
    from agent import connection
    from app.api import create_app
    from tests.fakes import MINT, api_client

    settings = Settings(_env_file=None, agent_provider="scripted", db_path=db_path, data_dir=data_dir)
    with api_client(create_app(settings, jwks=MINT)) as backend, connection.use(backend):  # the backend the CLI works through
        yield db_path


def status(db_path, claim_id):
    engine = make_engine(Settings(_env_file=None, db_path=db_path), max_connections=1)
    return RawDb(engine).execute("SELECT status FROM claims WHERE claim_id = ?", (claim_id,)).fetchone()[0]


def test_cli_works_one_claim(cli_env, capsys):
    assert cli.main(["CLM-1004"]) == 0
    assert status(cli_env, "CLM-1004") == "decided"
    assert status(cli_env, "CLM-1006") == "waiting"
    assert capsys.readouterr().out.startswith("CLM-1004: approve")


def test_cli_says_when_a_claim_is_not_worked(cli_env, capsys):
    cli.main(["CLM-1004"])
    assert cli.main(["CLM-1004"]) == 1
    assert "not worked, the claim is decided" in capsys.readouterr().out


def test_cli_without_a_claim_id_is_refused(cli_env):
    with pytest.raises(SystemExit):
        cli.main([])
