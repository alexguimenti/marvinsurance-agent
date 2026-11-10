"""The claim graph on each route: the same work, the same refusals, and a payment the Gateway refuses held for a handler."""
import json

import pytest

from agent import connection
from agent.direct import DirectRoute
from agent.gateway import GatewayRoute
from agent.worker import carry_on, work_claim
from app.auth import Handler
from app.claims import get_claim
from app.payments import get_payment, sign_off
from app.settings_store import change_setting
from tests.fakes import RawDb, StandInGateway, agent_env, approve, direct_route, stub, tool_call

CLAIM = "CLM-1001"
BIG = "CLM-1003"  # a claim big enough for an approval over $1,000
DANA = Handler("HND-01", "Dana Whitfield", "Senior Claims Handler", "dana.whitfield@marvinsurance.example", True)


@pytest.fixture(params=["direct", "gateway"])
def on_route(request, db_path, data_dir):
    """Work claims on a route: on_route(outcomes, deny=()) -> (env, model, stand_in), closed after the test."""
    opened = []

    def make(outcomes=None, deny=(), **settings):
        stand_in = StandInGateway(deny=deny)
        route = direct_route() if request.param == "direct" else GatewayRoute("https://gateway.example/mcp", None, call=stand_in)
        model = stub(outcomes)
        cm = agent_env(db_path, data_dir, model, route=route, **settings)
        env = cm.__enter__()
        opened.append(cm)
        return env, model, stand_in

    make.kind = request.param
    yield make
    for cm in reversed(opened):
        cm.__exit__(None, None, None)


def steps(env, kind, claim=CLAIM):
    rows = RawDb(env.engine).execute("SELECT content FROM trace_events WHERE claim_id=? AND kind=? ORDER BY seq", (claim, kind))
    return [json.loads(r[0]) for r in rows]


def test_a_claim_is_decided_on_either_route(on_route):
    env, _, stand_in = on_route({CLAIM: [approve(420.0)]})
    assert work_claim(env, CLAIM) == "CLM-1001: approve $420.00, paid"
    if on_route.kind == "gateway":
        assert "undercover-app___pay_claim" in stand_in.names and "undercover-app___get_claim" in stand_in.names


CALCULATE = {"items": [{"description": "x", "amount": 420.0, "item_limit": None}], "third_party_paid": 0, "deductible": 0,
             "benefit_max": None}


def test_an_app_refusal_reaches_the_model_and_is_a_refusal_step(on_route, monkeypatch):
    env, model, _ = on_route({CLAIM: [[tool_call("calculate_payout", CALCULATE, "c1"), tool_call("calculate_payout", CALCULATE, "c2"),
                                       approve(420.0)]]})
    real = connection.call
    refused = []

    def refusing(method, path, json=None, params=None):
        if path.endswith("/payout") and not refused:  # the first calculation only
            refused.append(path)
            raise connection.Refused(422, "the calculator is closed")
        return real(method, path, json=json, params=params)

    monkeypatch.setattr(connection, "call", refusing)
    assert work_claim(env, CLAIM) == "CLM-1001: approve $420.00, paid"
    refusals = steps(env, "refusal")
    assert [r["by"] for r in refusals] == ["app"] and "the calculator is closed" in refusals[0]["reason"]
    shown = [str(m.content) for m in model.shown[-1] if m.type == "tool"]
    assert any(text.startswith("Error:") and "the calculator is closed" in text for text in shown)
    results = [r for r in steps(env, "tool_result") if r["name"].endswith("calculate_payout")]
    assert len(results) == 1  # the second calculation only: a refusal is not a result


def test_a_gateway_refusal_of_a_tool_is_recorded_as_the_gateways_and_work_goes_on(db_path, data_dir):
    stand_in = StandInGateway(deny=["undercover-app___calculate_payout"])
    route = GatewayRoute("https://gateway.example/mcp", None, call=stand_in)
    calculate = tool_call("calculate_payout", CALCULATE, "c1")
    model = stub({CLAIM: [[calculate, tool_call("calculate_payout", CALCULATE, "c2"), approve(420.0)]]})
    with agent_env(db_path, data_dir, model, route=route) as env:
        assert work_claim(env, CLAIM) == "CLM-1001: approve $420.00, paid"
        refusals = steps(env, "refusal")
        shown = [str(m.content) for m in model.shown[-1] if m.type == "tool"]
    assert [(r["by"], r["tool"]) for r in refusals] == [("gateway", "undercover-app___calculate_payout")]
    assert any("refused by the Gateway" in text for text in shown)


def test_a_payment_the_gateway_refused_waits_for_sign_off_and_a_sign_off_pays_it(db_path, data_dir):
    stand_in = StandInGateway()
    route = GatewayRoute("https://gateway.example/mcp", None, call=stand_in)
    with agent_env(db_path, data_dir, stub({BIG: [approve(2000.0)]}), route=route) as env:
        change_setting(env.engine, "approval_limit", 5000, DANA)  # raised above the Gateway's Policy
        line = work_claim(env, BIG)
        payment = get_payment(env.engine, BIG)
        status = get_claim(env.engine, BIG)["status"]
        refusals = steps(env, "refusal", BIG)
        assert (status, payment.status) == ("awaiting_approval", "held")
        assert RawDb(env.engine).execute("SELECT hold_reason FROM payments WHERE claim_id=?", (BIG,))[0][0] == "gateway_refused"
        assert [r["by"] for r in refusals] == ["gateway"] and "pay_claim" in refusals[0]["tool"]
        sign_off(env.engine, env.ref, f"PAY-{BIG}", "HND-01")
        assert get_payment(env.engine, BIG).status == "paid"
        assert line.startswith("CLM-1003")
        assert carry_on(env, BIG).startswith("CLM-1003: not worked")  # nothing is left to carry on


def test_get_claim_gives_the_same_text_on_both_routes(tmp_path, data_dir):
    texts = {}
    for kind in ("direct", "gateway"):
        route = direct_route() if kind == "direct" else GatewayRoute("https://gateway.example/mcp", None, call=StandInGateway())
        model = stub({CLAIM: [approve(420.0)]})
        (tmp_path / kind).mkdir()
        with agent_env(str(tmp_path / kind / "test.db"), data_dir, model, route=route) as env:
            work_claim(env, CLAIM)
            texts[kind] = next(str(m.content) for m in model.shown[0] if m.type == "tool" and m.name == "get_claim")
    assert texts["direct"] == texts["gateway"] and texts["direct"].startswith("<claim_data>")
