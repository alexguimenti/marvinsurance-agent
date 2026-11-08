"""The scoreboard's measures, as in the team's labs, against numbers worked out by hand."""
import pytest
from fastapi.testclient import TestClient

from agent.service import create_service
from app.api import create_app
from app.config import Settings
from tests.fakes import MINT
from app.scoring import measures, score_claim

HANDLERS = [
    {"claim_id": "A", "decision": "approve", "payout": 500.0},
    {"claim_id": "B", "decision": "decline", "payout": 0.0},
    {"claim_id": "C", "decision": "refer", "payout": 0.0},
    {"claim_id": "D", "decision": "approve", "payout": 1200.0},
    {"claim_id": "E", "decision": "approve", "payout": 300.0},
]
AGENT = {
    "A": {"decision": "approve", "payout": 500.60},  # right, within a dollar
    "B": {"decision": "approve", "payout": 800.0},  # wrongful approval: its payout, $800
    "C": {"decision": "decline", "payout": 0.0},  # wrongful decline: $1,500
    "D": {"decision": "approve", "payout": 1000.0},  # right decision, $200 off
    "E": None,  # no decision: a needless referral, $50
}


def test_measures_match_ones_worked_out_by_hand():
    rows = [{**score_claim(AGENT[h["claim_id"]], h), "cost_usd": 0.01, "seconds": 10.0} for h in HANDLERS]
    m = measures(rows, total_seconds=25.0)
    assert m["claims"] == 5
    assert m["decision_accuracy"] == 2 / 5  # A and D
    assert m["accuracy"] == 1 / 5  # A only
    assert m["mistake_cost"] == pytest.approx(800 + 1500 + 200 + 50)
    assert m["straight_through"] == 4 / 5  # E counts as referred
    assert m["cost_per_claim"] == pytest.approx(0.01)
    assert (m["seconds_per_claim"], m["total_seconds"]) == (10.0, 25.0)


@pytest.mark.parametrize("agent,handler,cost", [
    ({"decision": "refer", "payout": 0}, {"decision": "decline", "payout": 0}, 50),  # a needless referral
    ({"decision": "decline", "payout": 0}, {"decision": "refer", "payout": 0}, 1500),  # a wrongful decline
    ({"decision": "approve", "payout": 4000}, {"decision": "refer", "payout": 0}, 4000),  # wrongful, paid or not
    ({"decision": "approve", "payout": 300.99}, {"decision": "approve", "payout": 300}, 0),  # within a dollar
    ({"decision": "refer", "payout": 0}, {"decision": "refer", "payout": 0}, 0),
])
def test_the_cost_of_each_kind_of_mistake(agent, handler, cost):
    assert score_claim(agent, handler)["mistake_cost"] == pytest.approx(cost)


def test_a_claim_with_no_decision_counts_as_a_referral():
    scored = score_claim(None, {"decision": "refer", "payout": 0})
    assert scored["decision_ok"] and scored["accurate"] and scored["referred"]


def test_no_cost_reported_gives_no_cost_per_claim():
    rows = [{**score_claim(None, {"decision": "refer", "payout": 0}), "cost_usd": None, "seconds": 1.0}]
    assert measures(rows, 1.0)["cost_per_claim"] is None


def test_an_empty_evaluation_has_no_measures_to_divide():
    assert measures([], 0.0)["decision_accuracy"] is None


MEASURES = {"claims", "decision_accuracy", "accuracy", "mistake_cost", "straight_through", "cost_per_claim",
            "seconds_per_claim", "total_seconds"}


@pytest.fixture
def backend(db_path, data_dir):
    settings = Settings(_env_file=None, agent_provider="scripted", db_path=db_path, data_dir=data_dir)
    with TestClient(create_service(settings, inline=True)) as service:
        with TestClient(create_app(settings, http=service, jwks=MINT), headers=MINT.header()) as client:
            yield client


def test_evaluations_are_listed_newest_first_with_every_measure(backend):
    backend.post("/evaluations", json={"eval_set": "evaluation", "tiny": True, "workers": 5})
    backend.post("/evaluations", json={"eval_set": "held_out", "tiny": True, "workers": 3})
    listed = backend.get("/evaluations").json()
    assert [(e["run"]["eval_set"], e["run"]["workers"]) for e in listed] == [("held_out", 3), ("evaluation", 5)]
    for evaluation in listed:
        assert set(evaluation["measures"]) == MEASURES and evaluation["measures"]["claims"] == 5
        assert evaluation["run"]["status"] == "finished" and evaluation["run"]["model"] == "scripted"


def test_an_evaluation_opens_to_its_claims_beside_the_handlers(backend):
    backend.post("/evaluations", json={"eval_set": "evaluation", "tiny": True, "workers": 5})
    report = backend.get("/evaluations/1").json()
    rows = {r["copy_of"]: r for r in report["claims"]}
    assert set(rows) == {"CLM-1007", "CLM-1015", "CLM-1017", "CLM-1020", "CLM-1021"}
    row = rows["CLM-1015"]
    assert row["claim_id"] == "E1-CLM-1015"
    assert row["agent"]["decision"] == "decline" and row["agent"]["rationale"]  # the scripted agent declines 1015
    assert row["handler"]["decision"] in ("approve", "decline", "refer") and "payout" in row["handler"]
    assert {"decision_ok", "accurate", "mistake_cost", "cost_usd", "seconds"} <= set(row)
    assert report["measures"]["decision_accuracy"] == sum(r["decision_ok"] for r in report["claims"]) / 5


def test_a_dispatch_is_not_an_evaluation(backend):
    backend.post("/dispatches", json={"workers": 3})
    assert backend.get("/evaluations/1").status_code == 404
    assert backend.get("/evaluations/99").status_code == 404
    assert backend.get("/evaluations").json() == []
