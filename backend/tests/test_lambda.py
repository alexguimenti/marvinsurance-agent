"""Ready for Lambda: the handler, a connection for each request, and a backend that imports only its own package."""
import builtins
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.dialects import postgresql
from sqlalchemy.pool import NullPool

from app.api import create_app
from app.config import Settings
from app.db import SCHEMA, claims, make_engine
from tests.fakes import MINT, TEST_KEY

REPO = Path(__file__).resolve().parents[2]


def function_url_event(path: str) -> dict:
    """What a Lambda Function URL sends (payload format 2.0)."""
    return {
        "version": "2.0", "routeKey": "$default", "rawPath": path, "rawQueryString": "",
        "headers": {"host": "abc.lambda-url.us-east-1.on.aws"}, "isBase64Encoded": False,
        "requestContext": {"http": {"method": "GET", "path": path, "sourceIp": "10.0.0.1", "protocol": "HTTP/1.1"}, "stage": "$default"},
    }


def load_main(monkeypatch, db_path, data_dir, dashboard_dir):
    """The deployment's module, imported fresh on a throwaway database."""
    from app.config import get_settings

    for name, value in (("DB_PATH", db_path), ("DATA_DIR", data_dir), ("DASHBOARD_DIR", str(dashboard_dir))):
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    for name in ("backend.main", "app.main"):
        sys.modules.pop(name, None)
    import backend.main

    return backend.main


@pytest.fixture
def dist(tmp_path):
    build = tmp_path / "dist"
    build.mkdir()
    (build / "index.html").write_text("<html>the dashboard</html>", encoding="utf-8")
    return build


def test_the_handler_answers_a_function_url_event_for_health(monkeypatch, db_path, data_dir, tmp_path):
    main = load_main(monkeypatch, db_path, data_dir, tmp_path / "no-build")
    answer = main.handler(function_url_event("/api/health"), None)
    assert answer["statusCode"] == 200 and json.loads(answer["body"]) == {"status": "ok"}  # with no startup hook run


def test_the_handler_serves_index_html_for_root_and_a_page(monkeypatch, db_path, data_dir, dist):
    main = load_main(monkeypatch, db_path, data_dir, dist)
    for path in ("/", "/claims/CLM-1001"):
        answer = main.handler(function_url_event(path), None)
        assert answer["statusCode"] == 200 and answer["body"] == "<html>the dashboard</html>", path


def test_the_deployments_names_exist(monkeypatch, db_path, data_dir, tmp_path):
    main = load_main(monkeypatch, db_path, data_dir, tmp_path / "no-build")
    assert main.app is not None and callable(main.handler)


def test_on_lambda_a_connection_is_opened_per_request_with_prepared_statements_off(monkeypatch):
    import app.db as db

    seen = {}
    real = db.create_engine

    def spy(url, **kwargs):
        seen.update(kwargs)
        return real(url, **kwargs)

    monkeypatch.setattr(db, "create_engine", spy)
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "undercover")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@pooler.example:6543/postgres")
    settings = Settings(_env_file=None)
    assert settings.on_lambda is True
    engine = make_engine(settings, max_connections=4)
    assert isinstance(engine.pool, NullPool) and seen["connect_args"] == {"prepare_threshold": None}
    assert "pool_size" not in seen  # nothing is kept between requests


def test_off_lambda_connections_work_as_today(monkeypatch):
    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_NAME", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db.example:5432/postgres")
    settings = Settings(_env_file=None)
    assert settings.on_lambda is False
    engine = make_engine(settings, max_connections=4)
    assert engine.pool.size() == 4 and engine.get_execution_options().get("schema_translate_map") is None


def test_on_lambda_every_statement_names_the_schema(monkeypatch):
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "undercover")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@pooler.example:6543/postgres")
    engine = make_engine(Settings(_env_file=None), max_connections=4)
    translate = engine.get_execution_options()["schema_translate_map"]
    assert translate == {None: SCHEMA}
    sql = str(select(claims).compile(dialect=postgresql.dialect(), schema_translate_map=translate))
    assert "__[SCHEMA__none].claims" in sql  # the placeholder SQLAlchemy fills with the schema as the statement runs


def run_python(code: str, env: dict, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", code], env=env, cwd=cwd, capture_output=True, text=True, timeout=120)


def base_env() -> dict:
    return {k: v for k, v in os.environ.items() if k in ("PATH", "SYSTEMROOT", "TEMP", "TMP", "USERPROFILE", "HOME")}


def test_the_app_imports_nothing_from_the_agent():
    code = ("import sys, app.main\n"
            "bad = sorted(m for m in sys.modules if m.split('.')[0] in ('agent', 'context', 'langchain', 'langgraph', 'langchain_core', 'langfuse', 'mcp', 'boto3'))\n"
            "assert not bad, bad")
    done = run_python(code, base_env(), REPO / "backend")
    assert done.returncode == 0, done.stderr


def test_the_app_starts_with_only_the_three_variables():
    env = {**base_env(), "DATABASE_URL": "postgresql://u:p@pooler.example:6543/postgres",
           "VITE_SUPABASE_URL": "https://project.supabase.example", "UNDERCOVER_API_KEY": TEST_KEY}
    code = ("from fastapi.testclient import TestClient\n"
            "import backend.main as m\n"
            "assert TestClient(m.app).get('/api/health').json() == {'status': 'ok'}\n"
            "assert TestClient(m.app).get('/api/claims').status_code == 401  # needs a sign-in, not a variable")
    done = run_python(code, env, REPO)
    assert done.returncode == 0, done.stderr


def test_the_app_writes_no_files(db_path, data_dir, monkeypatch, tmp_path):
    (tmp_path / "index.html").write_text("<html>the dashboard</html>", encoding="utf-8")
    settings = Settings(_env_file=None, agent_provider="scripted", db_path=db_path, data_dir=data_dir, dashboard_dir=str(tmp_path))
    with TestClient(create_app(settings, jwks=MINT)) as client:  # startup may write (the database); requests may not
        real = builtins.open

        def read_only(file, mode="r", *args, **kwargs):
            if any(flag in str(mode) for flag in "wax+"):
                raise PermissionError(f"read-only file system: {file}")
            return real(file, mode, *args, **kwargs)

        monkeypatch.setattr(builtins, "open", read_only)
        monkeypatch.setattr(io, "open", read_only)
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/claims", headers=MINT.header()).status_code == 200
        assert client.get("/api/claims/CLM-1001", headers={"X-API-Key": TEST_KEY}).status_code == 200
        assert client.get("/api/agent/openapi.json", headers={"X-API-Key": TEST_KEY}).status_code == 200
        assert client.get("/a-dashboard-page").status_code == 200  # served from the build, read only
