"""What the independent review of the Copilot found: a long turn that is still alive is not stopped, a card cannot pay an amount it did not show,
a failing sign-in check is told apart from a refused one, and a long first argument does not fail a card."""
import httpx
import pytest
from langchain_core.messages import AIMessage

from agent.connection import Refused  # noqa: F401
from agent.copilot.cards import main_input
from agent.copilot.payloads import Turn
from agent.copilot.turns import open_env, run_turn
from agent.settings import AgentSettings
from agent import connection
from app.db import make_engine
from tests.fakes import RawDb, script
from tests.test_copilot_routes import (BIG, STEP, as_handler, app, copilot, engine, hold_big_payout, move_back, new_conversation, say, settings_for,  # noqa: F401
                                       view)
from tests.test_copilot_tools import App, DANA


def test_a_turn_that_keeps_reporting_is_not_stopped_however_long_it_runs(app, engine):
    conversation_id = new_conversation(app)
    turn_id = say(app, conversation_id).json()["turn_id"]
    header = as_handler()[0]
    move_back(engine, 25)  # it began 25 minutes ago ...
    assert view(app, conversation_id).json()["turns"][0]["status"] == "stopped"  # ... and had said nothing since
    # a step is the Copilot's word that it is working: it counts from then
    again = new_conversation(app)
    working = say(app, again).json()["turn_id"]
    move_back(engine, 25)
    step_id = app.post(f"/copilot/turns/{working}/steps", json=STEP, headers=header).json()["step_id"]
    assert view(app, again).json()["turns"][0]["status"] == "working"
    move_back(engine, 25, "resumed_at")  # then 25 quiet minutes
    assert view(app, again).json()["turns"][0]["status"] == "stopped"
    # finishing a step is a word too
    move_back(engine, 25)
    app.post(f"/copilot/steps/{step_id}", json={"status": "done", "output": "x", "sources": None, "reason": None}, headers=header)
    assert view(app, again).json()["turns"][0]["status"] == "working"
    assert turn_id != working


def test_a_card_cannot_pay_an_amount_it_did_not_show(app, engine):
    conversation_id = new_conversation(app)
    turn_id = say(app, conversation_id).json()["turn_id"]
    hold_big_payout(app)
    header = as_handler()[0]
    app.post(f"/copilot/turns/{turn_id}/pause", json={"claim_id": BIG}, headers=header)  # the card shows $2,000
    RawDb(engine).execute("UPDATE payments SET amount = 9000 WHERE claim_id = ?", (BIG,))  # sent back and decided again, held at $9,000
    refused = app.post(f"/copilot/conversations/{conversation_id}/confirmation", json={"answer": "sign_off"}, headers=header)
    assert refused.status_code == 409 and "payout" in refused.json()["detail"]
    assert view(app, conversation_id).json()["turns"][0]["status"] == "waiting"  # the card is still there, to be cancelled
    cancelled = app.post(f"/copilot/conversations/{conversation_id}/confirmation", json={"answer": "cancel"}, headers=header)
    assert cancelled.status_code == 202


def test_a_card_whose_payout_is_gone_cannot_be_signed_off(app, engine):
    conversation_id = new_conversation(app)
    turn_id = say(app, conversation_id).json()["turn_id"]
    hold_big_payout(app)
    header = as_handler()[0]
    app.post(f"/copilot/turns/{turn_id}/pause", json={"claim_id": BIG}, headers=header)
    RawDb(engine).execute("DELETE FROM payments WHERE claim_id = ?", (BIG,))
    assert app.post(f"/copilot/conversations/{conversation_id}/confirmation", json={"answer": "sign_off"}, headers=header).status_code == 409


def turn(token="tok-dana"):
    return Turn(operation="turn", conversation_id="conversation-" + "f" * 36, turn_id=7, message="hi", token=token)


def run_with(tmp_path, me):
    app_stub = App({("GET", "/api/me"): me})
    settings = AgentSettings(_env_file=None, db_path=str(tmp_path / "copilot.db"))
    model = script(AIMessage("hello"))
    with open_env(settings, model=model, context_tools=[]) as env, connection.use(httpx.Client(transport=httpx.MockTransport(app_stub))):
        run_turn(env, turn())
    return app_stub, model


def test_a_backend_that_is_down_fails_the_turn_instead_of_leaving_it_working(tmp_path):
    app_stub, model = run_with(tmp_path, (503, {"detail": "the pooler closed the connection"}))
    assert model.seen == []  # still before any model call
    failed = app_stub.sent("POST", "/api/copilot/turns/7/fail")
    assert len(failed) == 1 and "tok-dana" not in failed[0]["reason"]


def test_a_refused_sign_in_still_ends_quietly(tmp_path):
    app_stub, model = run_with(tmp_path, (401, {"detail": "sign in again"}))
    assert model.seen == [] and app_stub.sent("POST", "/api/copilot/turns/7/fail") == []


def test_a_long_first_argument_makes_a_short_card_input():
    shown = main_input("send_back", {"note": "n" * 400, "claim_id": "CLM-1003"})
    assert len(shown) <= 300 and shown.startswith("nnn")
    assert main_input("search_documents", {"query": "q" * 1000}) == "q" * 297 + "..."
