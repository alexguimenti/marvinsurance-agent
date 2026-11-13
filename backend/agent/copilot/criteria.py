"""The three success criteria we agreed when the pilot started, and the figures for them, worked out here in code from our records and never
by the model. The model writes its answer around what this returns.

1. Straight through: at least 70% of claims handled straight through on the whole held-out set (an evaluation's own measure).
2. Accuracy: at least our accuracy objective in the settings, on the same evaluation (its own measure).
3. Over the limit: every payout over the approval limit approved by a person (the app counts them, in one read).

The first two come from an evaluation of the whole held-out set that this starts and waits for. If it is refused, fails, stops or is still
going after 15 minutes, there are no figures for them. The third is always given."""
from typing import Callable

from agent.connection import Refused
from agent.copilot import app_api
from agent.copilot.waiting import wait_for_run

STRAIGHT_THROUGH_TARGET = 0.70


def _percent(value: float) -> str:
    return f"{value * 100:.1f}%"


def over_the_limit(counts: dict) -> dict:
    paid, signed = counts["paid_over_limit"], counts["signed_off"]
    if paid == 0:
        return {"criterion": "Over the limit", "target": "every payout over the limit signed off by a handler",
                "figure": "none paid over the limit yet", "met": True}
    return {"criterion": "Over the limit", "target": "every payout over the limit signed off by a handler",
            "figure": f"{signed} of {paid} signed off", "met": signed == paid}


def check_criteria(token: str, wait: Callable = wait_for_run) -> dict:
    """Start the whole held-out set with the claim agents in our settings, wait for it, and answer
    `{"rows": [{criterion, target, figure, met}], "all_met": bool | None, "evaluation": {...}}`."""
    rows = {r["name"]: r["value"] for r in app_api.get(token, "/api/settings")["settings"]}
    objective = rows["objectives"]["accuracy"]
    third = over_the_limit(app_api.get(token, "/api/payouts/over-limit"))
    first = {"criterion": "Straight through", "target": f"at least {_percent(STRAIGHT_THROUGH_TARGET)}", "figure": None, "met": None}
    second = {"criterion": "Accuracy", "target": f"at least {_percent(objective)}", "figure": None, "met": None}
    try:
        started = app_api.post(token, "/api/evaluations", {"eval_set": "held_out", "tiny": False, "workers": int(rows["agents_at_once"])})
    except (Refused, LookupError) as exc:
        evaluation = {"refused": str(exc)}
    else:
        result = wait(token, "evaluation", started["run_id"])
        evaluation = {"run_id": started["run_id"], "status": result["status"]}
        if result["status"] == "still_going":
            evaluation["follow"] = result["follow"]
        measures = result["run"].get("measures") or {}
        if result["status"] == "finished" and measures.get("straight_through") is not None and measures.get("accuracy") is not None:
            first.update(figure=_percent(measures["straight_through"]), met=measures["straight_through"] >= STRAIGHT_THROUGH_TARGET)
            second.update(figure=_percent(measures["accuracy"]), met=measures["accuracy"] >= objective)
        elif result["status"] == "finished":
            evaluation["status"] = "finished without measures"
    checked = [first, second, third]
    return {"rows": checked, "all_met": None if first["met"] is None else all(r["met"] for r in checked), "evaluation": evaluation}
