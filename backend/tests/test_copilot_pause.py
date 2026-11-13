"""The sign-off pauses in a node of its own, saved; a freshly built Copilot carries on from the same saved state."""
import httpx
import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage

from agent import connection
from agent.copilot.payloads import Answer, Turn
from agent.copilot.turns import answer, open_env, run_turn
from agent.settings import AgentSettings
from tests.fakes import script, tool_call
from tests.test_copilot_tools import App, DANA

THREAD = "conversation-" + "c" * 36
MARCUS = {"handler_id": "HND-02", "name": "Marcus Oyelaran", "role": "Handler", "can_approve_over_limit": False}
CARD = {"claim_id": "CLM-1003", "payment_id": "PAY-CLM-1003", "customer_name": "Maya Patel", "amount": 3750.0, "reason": "Covered."}
HELD = [{"payment_id": "PAY-CLM-1003", "claim_id": "CLM-1003", "amount": 3750.0, "status": "held"}]


def the_app(handler=DANA, more=None):
    answers = {("GET", "/api/me"): (200, handler), ("GET", "/api/approvals"): (200, HELD),
               ("POST", "/api/copilot/turns/7/pause"): (200, CARD),
               ("POST", "/api/payments/PAY-CLM-1003/approve"): (200, {"amount": 3750.0, "status": "paid"}), **(more or {})}
    return App(answers)


def settings(tmp_path):
    return AgentSettings(_env_file=None, db_path=str(tmp_path / "copilot.db"), agent_provider="openrouter")


def turn(message="Sign off on CLM-1003", token="tok-dana"):
    return Turn(operation="turn", conversation_id=THREAD, turn_id=7, message=message, token=token)


def answer_body(choice, token="tok-dana"):
    return Answer(operation="answer", conversation_id=THREAD, turn_id=7, answer=choice, token=token)


def with_app(app):
    return connection.use(httpx.Client(transport=httpx.MockTransport(app)))


def saved(env):
    with env.graph() as graph:
        return graph.get_state({"configurable": {"thread_id": THREAD}})


def test_a_sign_off_pauses_and_reports_the_card_from_the_records(tmp_path):
    app = the_app()
    model = script(tool_call("sign_off", {"claim_id": "CLM-1003"}, "s1"))
    with open_env(settings(tmp_path), model=model, context_tools=[]) as env, with_app(app):
        run_turn(env, turn())
        assert saved(env).next == ("confirm",)
    assert app.sent("POST", "/api/copilot/turns/7/pause") == [{"claim_id": "CLM-1003"}]  # the claim only: the app builds the card
    assert app.sent("POST", "/api/payments/PAY-CLM-1003/approve") == []  # nothing is paid
    assert app.sent("POST", "/api/copilot/turns/7/end") == [] and app.sent("POST", "/api/copilot/turns/7/fail") == []


def test_a_fresh_copilot_on_the_same_saver_answers_and_pays_once(tmp_path):
    app = the_app()
    first = script(tool_call("list_held_payouts", {}, "h1"), tool_call("sign_off", {"claim_id": "CLM-1003"}, "s1"))
    with open_env(settings(tmp_path), model=first, context_tools=[]) as env, with_app(app):
        run_turn(env, turn())
    held_reads = len(app.sent("GET", "/api/approvals"))
    second = script(AIMessage("CLM-1003 is signed off and paid."))
    with open_env(settings(tmp_path), model=second, context_tools=[]) as fresh, with_app(app):  # a Copilot built after the pause
        answer(fresh, answer_body("sign_off"))
        assert not saved(fresh).next and not saved(fresh).interrupts
    assert len(app.sent("POST", "/api/payments/PAY-CLM-1003/approve")) == 1
    assert len(app.sent("GET", "/api/approvals")) == held_reads  # nothing before the pause ran again
    end = app.sent("POST", "/api/copilot/turns/7/end")[0]
    assert end["answer"] == "CLM-1003 is signed off and paid." and end["model_calls"] == 3  # the turn's, across the pause
    told = second.seen[0][-1]
    assert isinstance(told, HumanMessage) and "was signed off" in told.content and "$3,750.00" in told.content
    card = [b for b in app.sent("POST", "/api/copilot/turns/7/steps") if b["tool"] == "sign_off"]
    assert [b["kind"] for b in card] == ["action", "action"]  # the tool's own card, and the payment's


def test_cancel_pays_nothing_and_the_model_is_told(tmp_path):
    app = the_app()
    with open_env(settings(tmp_path), model=script(tool_call("sign_off", {"claim_id": "CLM-1003"}, "s1")), context_tools=[]) as env, with_app(app):
        run_turn(env, turn())
    second = script(AIMessage("I have not signed it off. What would you like instead?"))
    with open_env(settings(tmp_path), model=second, context_tools=[]) as fresh, with_app(app):
        answer(fresh, answer_body("cancel"))
    assert app.sent("POST", "/api/payments/PAY-CLM-1003/approve") == []
    assert "pressed Cancel" in second.seen[0][-1].content
    assert app.sent("POST", "/api/copilot/turns/7/end")[0]["answer"].startswith("I have not signed it off")


def test_a_sign_off_the_app_refuses_is_told_to_the_model(tmp_path):
    app = the_app(more={("POST", "/api/payments/PAY-CLM-1003/approve"): (409, {"detail": "CLM-1003 is not waiting for a sign-off"})})
    with open_env(settings(tmp_path), model=script(tool_call("sign_off", {"claim_id": "CLM-1003"}, "s1")), context_tools=[]) as env, with_app(app):
        run_turn(env, turn())
    second = script(AIMessage("The app refused."))
    with open_env(settings(tmp_path), model=second, context_tools=[]) as fresh, with_app(app):
        answer(fresh, answer_body("sign_off"))
    assert "not waiting for a sign-off" in second.seen[0][-1].content
    assert [b["status"] for b in app.sent("POST", "/api/copilot/steps/11")][-1] == "refused"


def test_marcus_over_the_limit_is_refused_without_a_pause(tmp_path):
    app = the_app(MARCUS)
    model = script(tool_call("sign_off", {"claim_id": "CLM-1003"}, "s1"), AIMessage("You may not sign off payouts over the limit."))
    with open_env(settings(tmp_path), model=model, context_tools=[]) as env, with_app(app):
        run_turn(env, turn())
        assert not saved(env).next
    assert app.sent("POST", "/api/copilot/turns/7/pause") == []
    assert app.sent("GET", "/api/approvals") == []  # refused in code, before looking at a payout
    assert app.sent("POST", "/api/copilot/steps/11")[0]["status"] == "refused"  # the card says refused, not done
    assert "who can sign off payouts over the limit" in model.seen[1][-1].content
    assert app.sent("POST", "/api/copilot/turns/7/end")[0]["answer"].startswith("You may not")


def test_a_refused_sign_in_ends_the_turn_before_any_model_call(tmp_path):
    app = the_app(more={("GET", "/api/me"): (401, {"detail": "your session is not valid: sign in again"})})
    model = script(AIMessage("never"))
    with open_env(settings(tmp_path), model=model, context_tools=[]) as env, with_app(app):
        run_turn(env, turn(token="tok-expired"))
        answer(env, answer_body("sign_off", token="tok-expired"))
    assert model.seen == []
    assert [r.url.path for r in app.requests] == ["/api/me", "/api/me"]


class Boom(BaseChatModel):
    @property
    def _llm_type(self) -> str:
        return "boom"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        raise RuntimeError("the model is down, and echoed tok-dana in its error")


def test_a_failed_turn_reports_why_without_the_token_and_the_next_message_works(tmp_path):
    app = the_app()
    with open_env(settings(tmp_path), model=Boom(), context_tools=[]) as env, with_app(app):
        run_turn(env, turn("What needs me?"))
    failed = app.sent("POST", "/api/copilot/turns/7/fail")[0]["reason"]
    assert failed.startswith("RuntimeError: the model is down") and "tok-dana" not in failed and len(failed) <= 300
    with open_env(settings(tmp_path), model=script(AIMessage("Two payouts.")), context_tools=[]) as again, with_app(app):
        run_turn(again, turn("What needs me now?"))
    assert app.sent("POST", "/api/copilot/turns/7/end")[0]["answer"] == "Two payouts."
