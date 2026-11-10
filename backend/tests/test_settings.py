"""The platform's settings: kept in the database, changed only by a handler who can sign off over the limit."""
import pytest
from fastapi.testclient import TestClient

from agent.service import create_service
from app.api import create_app
from app.auth import Handler
from app.claims import needs_sign_off
from app.config import Settings
from app.db import init_schema, make_engine
from app.reference import Reference
from app.seed import seed_claims
from app.settings_store import change_setting, get_settings, models_allowed, setting_history
from tests.fakes import record_decision, service_client, api_client, MINT, approve

DANA = Handler("HND-01", "Dana Whitfield", "Senior Claims Handler", "dana.whitfield@marvinsurance.example", True)


@pytest.fixture
def ref(data_dir):
    return Reference(data_dir)


@pytest.fixture
def engine(db_path, ref):
    engine = make_engine(Settings(_env_file=None, db_path=db_path), max_connections=2)
    init_schema(engine)
    seed_claims(engine, ref)
    yield engine
    engine.dispose()


def test_settings_start_with_the_labs_values(engine):
    s = get_settings(engine)
    assert s["approval_limit"] == 1000
    assert s["objectives"] == {"accuracy": 0.6, "grade": 1.0}
    assert s["noise"] == {"accuracy": 0.1, "grade": 0.15, "cost_per_claim": 0.15, "seconds_per_claim": 0.15}
    assert s["tolerance"] == {"accuracy": 0.1, "grade": 0.15, "cost_per_claim": 1.0, "seconds_per_claim": 0.25}
    assert (s["round_spending_limit"], s["agents_at_once"]) == (0.5, 25)


def test_seeding_twice_keeps_a_changed_setting(engine, ref):
    change_setting(engine, "round_spending_limit", 0.75, DANA)
    seed_claims(engine, ref)
    assert get_settings(engine)["round_spending_limit"] == 0.75


def test_a_change_records_who_and_when(engine):
    change_setting(engine, "round_spending_limit", 0.75, DANA)
    row = next(r for r in setting_history(engine) if r["name"] == "round_spending_limit")
    assert (row["value"], row["changed_by"]) == (0.75, "HND-01") and row["changed_at"]
    current = next(r for r in setting_history(engine, current_only=True) if r["name"] == "round_spending_limit")
    assert current["changed_by"] == "HND-01"


@pytest.mark.parametrize("name,value", [
    ("approval_limit", 0), ("approval_limit", -5), ("approval_limit", "lots"),
    ("round_spending_limit", 0), ("agents_at_once", 0), ("agents_at_once", 26), ("agents_at_once", 2.5),
    ("objectives", {"accuracy": 1.5, "grade": 1.0}), ("objectives", {"accuracy": 0.6}),
    ("noise", {"accuracy": -0.1, "grade": 0.1, "cost_per_claim": 0.1, "seconds_per_claim": 0.1}),
    ("tolerance", {"accuracy": 0.1}),
])
def test_a_value_that_makes_no_sense_is_refused_and_nothing_changes(engine, name, value):
    before = get_settings(engine)[name]
    with pytest.raises(ValueError):
        change_setting(engine, name, value, DANA)
    assert get_settings(engine)[name] == before


def test_an_unknown_setting_is_refused(engine):
    with pytest.raises(LookupError):
        change_setting(engine, "colour_of_the_sky", 1, DANA)


def test_a_new_approval_limit_applies_to_the_next_decision(engine, ref):
    record_decision(engine, ref, "CLM-1003", approve(3750.0), "m")  # over $1,000: held
    change_setting(engine, "approval_limit", 5000, DANA)
    record_decision(engine, ref, "CLM-1008", approve(2240.0), "m")  # under the new limit
    assert needs_sign_off(engine, ref, "CLM-1003") is True  # a decision already recorded keeps the limit it was made under
    assert needs_sign_off(engine, ref, "CLM-1008") is False


def test_without_a_settings_row_the_company_file_still_gives_the_limit(db_path, ref):
    from app.settings_store import approval_limit

    engine = make_engine(Settings(_env_file=None, db_path=db_path), max_connections=1)
    init_schema(engine)  # tables, but not seeded
    assert approval_limit(engine, ref) == 1000


def test_models_allowed_come_from_the_file_with_numeric_prices(ref):
    allowed = models_allowed(ref, Settings(_env_file=None))
    assert set(allowed) == {"openai/gpt-6-luna", "google/gemini-3.5-flash-lite", "deepseek/deepseek-v4.1-flash"}
    assert allowed["deepseek/deepseek-v4.1-flash"] == {"reasoning_efforts": ["low"], "price_in": 0.30, "price_out": 1.20}
    assert allowed["openai/gpt-6-luna"]["reasoning_efforts"] == ["none", "low", "medium"]


def test_models_allowed_with_model_name_is_that_model_at_default_and_free(ref, monkeypatch):
    monkeypatch.setenv("MODEL_NAME", "some/free-model")
    assert models_allowed(ref, Settings(_env_file=None)) == {
        "some/free-model": {"reasoning_efforts": ["default"], "price_in": 0.0, "price_out": 0.0}}


@pytest.fixture
def backend(db_path, data_dir):
    settings = Settings(_env_file=None, agent_provider="scripted", db_path=db_path, data_dir=data_dir)
    with service_client(create_service(settings, inline=True)) as service:
        with api_client(create_app(settings, http=service, jwks=MINT), headers=MINT.header()) as client:
            yield client


def test_every_handler_can_see_the_settings_and_the_models_allowed(backend):
    body = backend.get("/settings", headers=MINT.header("HND-02")).json()  # Marcus
    by_name = {s["name"]: s for s in body["settings"]}
    assert by_name["approval_limit"]["value"] == 1000 and by_name["approval_limit"]["changed_by"] == "seed"
    assert by_name["approval_limit"]["changed_by_name"] is None
    assert "openai/gpt-6-luna" in body["models_allowed"]


def test_dana_changes_a_setting_and_the_page_says_who(backend):
    answer = backend.put("/settings/round_spending_limit", json={"value": 0.8}, headers=MINT.header("HND-01"))
    assert answer.status_code == 200
    by_name = {s["name"]: s for s in backend.get("/settings").json()["settings"]}
    assert (by_name["round_spending_limit"]["value"], by_name["round_spending_limit"]["changed_by"]) == (0.8, "HND-01")
    assert by_name["round_spending_limit"]["changed_by_name"] == "Dana Whitfield"


def test_marcus_cannot_change_a_setting(backend):
    answer = backend.put("/settings/round_spending_limit", json={"value": 9}, headers=MINT.header("HND-02"))
    assert answer.status_code == 403
    by_name = {s["name"]: s for s in backend.get("/settings").json()["settings"]}
    assert by_name["round_spending_limit"]["value"] == 0.5 and by_name["round_spending_limit"]["changed_by"] == "seed"


def test_a_change_needs_a_sign_in(backend):
    assert backend.put("/settings/round_spending_limit", json={"value": 1}, headers={"Authorization": ""}).status_code == 401


def test_a_value_that_makes_no_sense_is_422_and_an_unknown_name_404(backend):
    assert backend.put("/settings/agents_at_once", json={"value": 99}, headers=MINT.header("HND-01")).status_code == 422
    assert backend.put("/settings/nothing", json={"value": 1}, headers=MINT.header("HND-01")).status_code == 404
