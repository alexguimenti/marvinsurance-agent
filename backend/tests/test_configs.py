"""The claim agent's configuration, and the improver's recipe, in versions."""
import pytest
from fastapi.testclient import TestClient

from app.configs import load_instructions
from agent.service import create_service
from agent.worker import work_claim
from app import configs
from app.api import create_app
from app.config import Settings
from app.db import init_schema, make_engine
from app.reference import Reference
from app.seed import seed_claims
from tests.fakes import service_client, api_client, MINT, RawDb, agent_env, approve, stub


@pytest.fixture
def engine(db_path, data_dir):
    engine = make_engine(Settings(_env_file=None, db_path=db_path), max_connections=2)
    init_schema(engine)
    seed_claims(engine, Reference(data_dir))
    yield engine
    engine.dispose()


def test_version_1_is_the_claim_agent_as_it_is(engine):
    v1 = configs.current(engine, "config")
    assert v1["version"] == 1
    assert v1["instructions"] == load_instructions("claim")
    assert (v1["model"], v1["reasoning_effort"]) == ("deepseek/deepseek-v4.1-flash", "low")
    assert (v1["proposed_by"], v1["approved_by"]) == ("seed", "seed")


def test_version_1_of_the_recipe_is_the_labs_first_recipe(engine):
    recipe = configs.current(engine, "recipe")
    assert recipe["version"] == 1 and recipe["proposed_by"] == "seed"
    for tool in ("read_results", "propose_change", "run_evaluation", "propose_recipe"):
        assert tool in recipe["recipe"]  # the recipe names the improver's four tools
    assert recipe["recipe"].rstrip().splitlines()[-1].startswith("5.")  # improving the recipe is the last step


def test_with_model_name_version_1_is_that_model_at_default_effort(db_path, data_dir, monkeypatch):
    monkeypatch.setenv("MODEL_NAME", "some/free-model")
    engine = make_engine(Settings(_env_file=None, db_path=db_path), max_connections=1)
    init_schema(engine)
    seed_claims(engine, Reference(data_dir))
    v1 = configs.current(engine, "config")
    assert (v1["model"], v1["reasoning_effort"]) == ("some/free-model", "default")


def test_seeding_again_keeps_the_versions_and_the_current_one(engine, data_dir):
    version = configs.add_version(engine, "config", {"instructions": "new", "model": "m", "reasoning_effort": "low",
                                                      "reason": "r", "proposed_by": "improver"}, approved_by="HND-01", round_id=None)
    configs.make_current(engine, "config", version)
    seed_claims(engine, Reference(data_dir))
    assert configs.current(engine, "config")["version"] == version == 2


def test_a_new_version_records_what_changed_the_reason_who_proposed_and_approved_it(engine):
    old = configs.current(engine, "config")
    new = old["instructions"].rstrip("\n") + "\n- Never approve a claim with no receipts."
    version = configs.add_version(engine, "config", {"instructions": new, "model": old["model"], "reasoning_effort": "low",
                                                      "reason": "Misses approved claims with no receipts", "proposed_by": "improver"},
                                  approved_by="HND-01", round_id=7)
    row = configs.get_version(engine, "config", version)
    assert version == 2 and row["reason"] == "Misses approved claims with no receipts"
    assert (row["proposed_by"], row["approved_by"], row["round_id"]) == ("improver", "HND-01", 7) and row["approved_at"]
    assert row["summary"] == "1 line added, 0 removed"
    assert configs.current(engine, "config")["version"] == 1  # a new version is not current until a round keeps it


def test_a_change_of_model_is_in_the_summary(engine):
    old = configs.current(engine, "config")
    version = configs.add_version(engine, "config", {"instructions": old["instructions"], "model": "openai/gpt-6-luna",
                                                      "reasoning_effort": "low", "reason": "r", "proposed_by": "improver"}, "HND-01", None)
    assert configs.get_version(engine, "config", version)["summary"] == "model deepseek/deepseek-v4.1-flash -> openai/gpt-6-luna"


def test_diff_marks_added_and_removed_lines():
    lines = configs.diff("a\nb\nc", "a\nB\nc\nd")
    assert [(line["kind"], line["text"]) for line in lines] == [
        ("same", "a"), ("remove", "b"), ("add", "B"), ("same", "c"), ("add", "d")]


def test_the_claim_agents_view_of_its_version_holds_no_scores(engine):
    run_id = RawDb(engine).execute(
        "INSERT INTO runs (kind, status, workers, model, reasoning_effort, config_version) VALUES ('dispatch', 'running', 3, 'm', 'low', 1) RETURNING run_id"
    ).fetchone()[0]
    view = configs.config_for_run(engine, run_id)
    assert set(view) == {"version", "instructions", "model", "reasoning_effort", "round_id"}  # no scores, no decisions


def test_a_run_without_a_version_uses_the_current_one(engine):
    assert configs.config_for_run(engine, None)["version"] == 1


def test_a_dispatch_and_an_evaluation_record_their_version(db_path, data_dir):
    settings = Settings(_env_file=None, agent_provider="scripted", db_path=db_path, data_dir=data_dir)
    with service_client(create_service(settings, inline=True)) as service:
        with api_client(create_app(settings, http=service, jwks=MINT), headers=MINT.header()) as backend:
            backend.post("/dispatches", json={"workers": 3})
            backend.post("/evaluations", json={"eval_set": "evaluation", "tiny": True, "workers": 3})
            runs = RawDb(make_engine(settings, max_connections=1)).execute(
                "SELECT kind, config_version, reasoning_effort FROM runs ORDER BY run_id").fetchall()
    assert [tuple(r) for r in runs] == [("dispatch", 1, "default"), ("evaluation", 1, "default")]


def test_a_run_uses_its_versions_instructions(db_path, data_dir):
    model = stub({"CLM-1001": [approve(100.0)]})
    with agent_env(db_path, data_dir, model) as env:
        version = configs.add_version(env.engine, "config", {"instructions": "ONLY APPROVE WHAT IS PROVEN.", "model": "m",
                                                              "reasoning_effort": "low", "reason": "r", "proposed_by": "improver"}, "HND-01", None)
        configs.make_current(env.engine, "config", version)
        work_claim(env, "CLM-1001", run_id=None)
    system = next(m for m in model.shown[0] if m.type == "system").content
    assert system.startswith("ONLY APPROVE WHAT IS PROVEN.") and "Sections you can read" in system


@pytest.fixture
def backend(db_path, data_dir):
    settings = Settings(_env_file=None, agent_provider="scripted", db_path=db_path, data_dir=data_dir)
    with service_client(create_service(settings, inline=True)) as service:
        with api_client(create_app(settings, http=service, jwks=MINT), headers=MINT.header()) as client:
            yield client


def test_the_configuration_page_shows_the_current_version_every_version_and_the_recipes(backend):
    body = backend.get("/configuration").json()
    assert body["current"]["version"] == 1 and body["current_recipe"] == 1
    assert [v["version"] for v in body["versions"]] == [1] and [r["version"] for r in body["recipes"]] == [1]
    assert "scores" in body["versions"][0] and body["versions"][0]["approved_by_name"] is None  # approved by the seed


def test_a_versions_diff_is_against_the_one_before(engine, backend, db_path):
    old = configs.current(engine, "config")
    configs.add_version(engine, "config", {"instructions": old["instructions"].rstrip("\n") + "\nExtra.", "model": "openai/gpt-6-luna",
                                           "reasoning_effort": "low", "reason": "r", "proposed_by": "improver"}, "HND-01", None)
    diff = backend.get("/configuration/config/2/diff").json()
    assert diff["against"] == 1 and [l for l in diff["lines"] if l["kind"] == "add"][0]["text"] == "Extra."
    assert diff["model"] == {"before": "deepseek/deepseek-v4.1-flash", "after": "openai/gpt-6-luna"}
    first = backend.get("/configuration/config/1/diff").json()
    assert first["against"] is None and all(l["kind"] == "add" for l in first["lines"])


def test_an_unknown_version_is_404(backend):
    assert backend.get("/configuration/config/9/diff").status_code == 404
    assert backend.get("/configuration/nonsense/1/diff").status_code == 422
