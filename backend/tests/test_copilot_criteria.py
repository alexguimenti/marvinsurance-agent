"""The success criteria, worked out in code from an evaluation's measures, the settings and the app's count of payouts over the limit."""
import json

from agent.connection import Refused
from agent.copilot.criteria import check_criteria
from tests.test_copilot_tools import App, run_tool
import httpx
from agent import connection

SETTINGS = {"settings": [{"name": "objectives", "value": {"accuracy": 0.6, "grade": 1.0}}, {"name": "agents_at_once", "value": 25}]}


def the_app(over=None, started=(202, {"status": "started", "run_id": 8})):
    return App({("GET", "/api/settings"): (200, SETTINGS),
                ("GET", "/api/payouts/over-limit"): (200, over or {"approval_limit": 1000.0, "paid_over_limit": 0, "signed_off": 0}),
                ("POST", "/api/evaluations"): started})


def waited(status="finished", measures=None):
    """What `wait_for_run` answers, as a stand-in; it also writes down what it was asked to wait for."""
    asked = []

    def wait(token, kind, run_id):
        asked.append((token, kind, run_id))
        return {"status": status, "run": {"run": {"run_id": run_id, "status": status}, "measures": measures or {}},
                "follow": "It is still working after 15 minutes. Follow it on the Scoreboard, at /scoreboard/8."}

    wait.asked = asked
    return wait


def check(app, wait):
    with connection.use(httpx.Client(transport=httpx.MockTransport(app))):
        return check_criteria("tok-dana", wait)


def test_criteria_from_measures_settings_and_the_count():
    over = {"approval_limit": 1000.0, "paid_over_limit": 3, "signed_off": 3}
    out = check(the_app(over), waited(measures={"straight_through": 0.75, "accuracy": 0.65}))
    assert out["rows"] == [
        {"criterion": "Straight through", "target": "at least 70.0%", "figure": "75.0%", "met": True},
        {"criterion": "Accuracy", "target": "at least 60.0%", "figure": "65.0%", "met": True},
        {"criterion": "Over the limit", "target": "every payout over the limit signed off by a handler", "figure": "3 of 3 signed off", "met": True},
    ]
    assert out["all_met"] is True and out["evaluation"] == {"run_id": 8, "status": "finished"}


def test_a_criterion_not_met_makes_all_met_false():
    over = {"approval_limit": 1000.0, "paid_over_limit": 3, "signed_off": 2}
    out = check(the_app(over), waited(measures={"straight_through": 0.69, "accuracy": 0.9}))
    assert [r["met"] for r in out["rows"]] == [False, True, False] and out["all_met"] is False
    assert out["rows"][0]["figure"] == "69.0%" and out["rows"][2]["figure"] == "2 of 3 signed off"


def test_criteria_with_none_paid_over_the_limit():
    out = check(the_app(), waited(measures={"straight_through": 0.8, "accuracy": 0.7}))
    third = out["rows"][2]
    assert third["met"] is True and third["figure"] == "none paid over the limit yet"
    assert out["all_met"] is True


def test_criteria_start_the_whole_held_out_set_with_agents_at_once():
    app, wait = the_app(), waited(measures={"straight_through": 0.8, "accuracy": 0.7})
    check(app, wait)
    assert app.sent("POST", "/api/evaluations") == [{"eval_set": "held_out", "tiny": False, "workers": 25}]
    assert wait.asked == [("tok-dana", "evaluation", 8)]


def test_criteria_after_a_failed_evaluation_give_only_the_third():
    out = check(the_app(), waited(status="stopped", measures={"straight_through": 1.0, "accuracy": 1.0}))
    assert [r["figure"] for r in out["rows"][:2]] == [None, None] and [r["met"] for r in out["rows"][:2]] == [None, None]
    assert out["rows"][2]["met"] is True and out["all_met"] is None
    assert out["evaluation"] == {"run_id": 8, "status": "stopped"}


def test_criteria_after_15_minutes_still_going_give_only_the_third():
    out = check(the_app(), waited(status="still_going"))
    assert out["rows"][0]["figure"] is None and out["rows"][2]["met"] is True and out["all_met"] is None
    assert out["evaluation"]["status"] == "still_going" and "/scoreboard/8" in out["evaluation"]["follow"]


def test_criteria_after_a_refused_start_give_only_the_third_and_the_reason():
    out = check(the_app(started=(409, {"detail": "a dispatch is already running"})), waited())
    assert out["evaluation"] == {"refused": "a dispatch is already running"}
    assert out["rows"][0]["figure"] is None and out["rows"][2]["figure"] == "none paid over the limit yet" and out["all_met"] is None


def test_the_tool_gives_the_model_the_figures_to_write_the_answer_around():
    app = the_app()
    app.answers[("GET", "/api/evaluations/8")] = (200, {"run": {"run_id": 8, "status": "finished"}, "measures": {"straight_through": 0.8, "accuracy": 0.7}})
    out = json.loads(run_tool(app, "check_success_criteria", {}).content)
    assert out["all_met"] is True and out["rows"][0]["figure"] == "80.0%"
