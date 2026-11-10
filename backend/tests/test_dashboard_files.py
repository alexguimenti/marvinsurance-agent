"""The backend serves the dashboard's build when there is one: its files, and its pages on a refresh."""
import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.config import Settings
from tests.fakes import MINT


@pytest.fixture
def dist(tmp_path):
    build = tmp_path / "site" / "dist"
    (build / "assets").mkdir(parents=True)
    (build / "index.html").write_text("<html>the dashboard</html>", encoding="utf-8")
    (build / "assets" / "app.js").write_text("console.log('app')", encoding="utf-8")
    (tmp_path / "site" / ".env").write_text("SECRET=never-served", encoding="utf-8")
    (tmp_path / "site" / "data").mkdir()
    (tmp_path / "site" / "data" / "claims.json").write_text("[]", encoding="utf-8")
    return build


def client_for(db_path, data_dir, dashboard_dir):
    settings = Settings(_env_file=None, agent_provider="scripted", db_path=db_path, data_dir=data_dir, dashboard_dir=str(dashboard_dir))
    return TestClient(create_app(settings, jwks=MINT))


def test_root_and_a_page_get_index_html(db_path, data_dir, dist):
    with client_for(db_path, data_dir, dist) as client:
        for path in ("/", "/claims/CLM-1001", "/approvals"):
            answer = client.get(path)
            assert answer.status_code == 200 and answer.text == "<html>the dashboard</html>", path


def test_a_file_is_served_at_its_own_path(db_path, data_dir, dist):
    with client_for(db_path, data_dir, dist) as client:
        answer = client.get("/assets/app.js")
        assert answer.status_code == 200 and answer.text == "console.log('app')"
        assert "javascript" in answer.headers["content-type"]


def test_an_unknown_api_path_is_a_json_404(db_path, data_dir, dist):
    with client_for(db_path, data_dir, dist) as client:
        for path in ("/api/nothing", "/api/claims/CLM-1001/nothing", "/api"):
            answer = client.get(path)
            assert answer.status_code == 404 and "detail" in answer.json(), path
        assert client.get("/api/health").json() == {"status": "ok"}  # every /api path works as before


def test_with_no_build_the_backend_runs_as_today(db_path, data_dir, tmp_path):
    with client_for(db_path, data_dir, tmp_path / "no-such-build") as client:
        assert client.get("/").status_code == 404
        assert client.get("/api/health").status_code == 200


@pytest.mark.parametrize("path", ["/..%2F..%2F.env", "/assets/../../data/claims.json", "/%2e%2e/.env", "/assets/..%2f..%2fdata%2fclaims.json"])
def test_a_path_outside_the_build_gets_index_html_never_the_file(db_path, data_dir, dist, path):
    with client_for(db_path, data_dir, dist) as client:
        answer = client.get(path)
        assert answer.status_code == 200 and answer.text == "<html>the dashboard</html>"
        assert "never-served" not in answer.text
