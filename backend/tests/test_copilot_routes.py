"""The app keeps the Copilot's conversations, turns and steps, starts each turn on the Copilot and refuses what is not a handler's."""
import functools
import threading
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.api import create_app
from app.config import Settings
from app.db import make_engine
from app.reference import Reference
from app.seed import reseed
from tests.fakes import MINT, TEST_KEY, RawDb, api_client, record_approval

BIG = "CLM-1003"
STEP = {"kind": "claims", "tool": "get_claim", "main_input": "CLM-1012", "input": {"claim_id": "CLM-1012"}}


def settings_for(db_path, data_dir):
    return Settings(_env_file=None, agent_provider="scripted", db_path=db_path, data_dir=data_dir)


class Copilot:
    """The httpx the app calls the Copilot with: writes each post down, or fails as a Copilot that is down does."""

    def __init__(self):
        self.posts, self.fail, self.answer = [], None, {"accepted": True}

    def post(self, url, json=None, timeout=None, headers=None):
        self.posts.append((url, json, headers))
        if self.fail:
            raise self.fail
        return httpx.Response(200, json=self.answer, request=httpx.Request("POST", url))


@functools.cache
def _minted(handler_id):  # one token for each handler, so a test can compare it with what the app passed on
    header = MINT.header(handler_id)
    return header, header["Authorization"].removeprefix("Bearer ")


def as_handler(handler_id="HND-01"):
    return _minted(handler_id)


@pytest.fixture
def copilot():
    return Copilot()


@pytest.fixture
def app(db_path, data_dir, copilot):
    with api_client(create_app(settings_for(db_path, data_dir), http=copilot, jwks=MINT)) as client:
        yield client


@pytest.fixture
def engine(db_path, data_dir):
    engine = make_engine(settings_for(db_path, data_dir), max_connections=2)
    yield engine
    engine.dispose()


def new_conversation(app, handler_id="HND-01"):
    return app.post("/copilot/conversations", headers=as_handler(handler_id)[0]).json()["conversation_id"]


def say(app, conversation_id, text="Work the queue", handler_id="HND-01"):
    return app.post(f"/copilot/conversations/{conversation_id}/messages", json={"text": text}, headers=as_handler(handler_id)[0])


def view(app, conversation_id, handler_id="HND-01"):
    return app.get(f"/copilot/conversations/{conversation_id}", headers=as_handler(handler_id)[0])


def move_back(engine, minutes, column="started_at"):
    at = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")
    RawDb(engine).execute(f"UPDATE copilot_turns SET {column} = ?", (at,))


def hold_big_payout(app):
    """The agent has decided CLM-1003 over the limit and its payout is held for a sign-off."""
    key = {"X-API-Key": TEST_KEY}
    app.post(f"/agent/claims/{BIG}/take", json={"run_id": None}, headers=key)
    record_approval(app, BIG, 2000.0)
    app.post(f"/agent/claims/{BIG}/hold", headers=key)


def test_a_message_starts_a_turn_and_sends_the_token_and_the_session(app, copilot):
    header, token = as_handler()
    conversation_id = new_conversation(app)
    assert 33 <= len(conversation_id) <= 100 and conversation_id.replace("-", "").isalnum()
    answer = say(app, conversation_id, "Work the queue with 10 agents")
    assert answer.status_code == 202
    url, payload, headers = copilot.posts[0]
    assert url.endswith("/invocations") and "Authorization" not in (headers or {}) and "X-API-Key" not in (headers or {})
    assert payload == {"operation": "turn", "conversation_id": conversation_id, "turn_id": answer.json()["turn_id"],
                       "message": "Work the queue with 10 agents", "token": token}
    body = view(app, conversation_id).json()
    assert body["title"] == "Work the queue with 10 agents"
    assert [(t["message"], t["status"]) for t in body["turns"]] == [("Work the queue with 10 agents", "working")]
    assert [c["conversation_id"] for c in app.get("/copilot/conversations", headers=header).json()] == [conversation_id]


def test_a_second_message_while_working_is_refused(app):
    conversation_id = new_conversation(app)
    assert say(app, conversation_id).status_code == 202
    refused = say(app, conversation_id, "again")
    assert refused.status_code == 409 and refused.json()["detail"] == "the Copilot is still working on your last message"
    assert len(view(app, conversation_id).json()["turns"]) == 1


def test_two_messages_at_once_start_one_turn(app):
    conversation_id = new_conversation(app)
    barrier, codes = threading.Barrier(2), []

    def go():
        barrier.wait()
        codes.append(say(app, conversation_id).status_code)

    threads = [threading.Thread(target=go) for _ in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(codes) == [202, 409]
    assert len(view(app, conversation_id).json()["turns"]) == 1


def test_a_waiting_turn_refuses_a_message_and_is_never_stopped(app, engine):
    conversation_id = new_conversation(app)
    turn_id = say(app, conversation_id).json()["turn_id"]
    hold_big_payout(app)
    header = as_handler()[0]
    assert app.post(f"/copilot/turns/{turn_id}/pause", json={"claim_id": BIG}, headers=header).status_code == 200
    move_back(engine, 600)  # ten hours
    body = view(app, conversation_id).json()
    assert body["turns"][0]["status"] == "waiting" and body["turns"][0]["confirmation"]["claim_id"] == BIG
    refused = say(app, conversation_id)
    assert refused.status_code == 409 and refused.json()["detail"] == "answer the confirmation first"


def test_a_turn_working_20_minutes_shows_stopped_and_a_new_message_goes_through(app, engine):
    conversation_id = new_conversation(app)
    say(app, conversation_id)
    move_back(engine, 19)
    assert view(app, conversation_id).json()["turns"][0]["status"] == "working"
    move_back(engine, 21)
    assert view(app, conversation_id).json()["turns"][0]["status"] == "stopped"
    assert say(app, conversation_id, "carry on").status_code == 202
    statuses = [t["status"] for t in view(app, conversation_id).json()["turns"]]
    assert statuses == ["stopped", "working"]


def test_a_turn_working_20_minutes_after_the_answer_shows_stopped(app, engine):
    conversation_id = new_conversation(app)
    turn_id = say(app, conversation_id).json()["turn_id"]
    hold_big_payout(app)
    header = as_handler()[0]
    app.post(f"/copilot/turns/{turn_id}/pause", json={"claim_id": BIG}, headers=header)
    assert app.post(f"/copilot/conversations/{conversation_id}/confirmation", json={"answer": "sign_off"}, headers=header).status_code == 202
    move_back(engine, 600)  # started long ago, but answered just now
    RawDb(engine).execute("UPDATE copilot_turns SET resumed_at = ?", (datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),))
    assert view(app, conversation_id).json()["turns"][0]["status"] == "working"
    move_back(engine, 21, "resumed_at")
    assert view(app, conversation_id).json()["turns"][0]["status"] == "stopped"


def test_a_confirmation_answered_twice_gets_409_and_sends_the_answer_once(app, copilot):
    conversation_id = new_conversation(app)
    turn_id = say(app, conversation_id).json()["turn_id"]
    hold_big_payout(app)
    header, token = as_handler()
    app.post(f"/copilot/turns/{turn_id}/pause", json={"claim_id": BIG}, headers=header)
    first = app.post(f"/copilot/conversations/{conversation_id}/confirmation", json={"answer": "sign_off"}, headers=header)
    second = app.post(f"/copilot/conversations/{conversation_id}/confirmation", json={"answer": "sign_off"}, headers=header)
    assert (first.status_code, second.status_code) == (202, 409)
    answers = [p for _, p, _ in copilot.posts if p["operation"] == "answer"]
    assert answers == [{"operation": "answer", "conversation_id": conversation_id, "turn_id": turn_id, "answer": "sign_off", "token": token}]


def test_another_handler_gets_404_on_read_message_confirm_and_reports(app):
    conversation_id = new_conversation(app, "HND-01")
    turn_id = say(app, conversation_id).json()["turn_id"]
    marcus = as_handler("HND-02")[0]
    assert view(app, conversation_id, "HND-02").status_code == 404
    assert say(app, conversation_id, handler_id="HND-02").status_code == 404
    assert app.post(f"/copilot/conversations/{conversation_id}/confirmation", json={"answer": "cancel"}, headers=marcus).status_code == 404
    for path, body in ((f"/copilot/turns/{turn_id}/steps", STEP), (f"/copilot/turns/{turn_id}/pause", {"claim_id": BIG}),
                       (f"/copilot/turns/{turn_id}/end", {"answer": "x", "model_calls": 1, "cost_usd": 0.1}),
                       (f"/copilot/turns/{turn_id}/fail", {"reason": "x"})):
        assert app.post(path, json=body, headers=marcus).status_code == 404, path
    assert app.get("/copilot/conversations", headers=marcus).json() == []


def test_a_request_with_the_agent_key_is_refused(app):
    key = {"X-API-Key": TEST_KEY}
    assert app.post("/copilot/conversations", headers=key).status_code in (401, 403)
    both = {**as_handler()[0], **key}  # the key makes a request the agent's, whatever else it carries
    assert app.post("/copilot/conversations", headers=both).status_code == 403
    assert app.get("/copilot/conversations", headers=both).status_code == 403
    assert app.post("/copilot/conversations", headers=as_handler()[0]).status_code == 201


def test_the_card_is_read_from_the_records(app):
    conversation_id = new_conversation(app)
    turn_id = say(app, conversation_id).json()["turn_id"]
    hold_big_payout(app)
    header = as_handler()[0]
    card = app.post(f"/copilot/turns/{turn_id}/pause", json={"claim_id": BIG}, headers=header).json()
    ref = Reference(settings_for("x", app_data_dir()).data_dir)
    assert card == {"claim_id": BIG, "payment_id": "PAY-CLM-1003", "customer_name": ref.customer(
        next(c for c in ref.claims if c["claim_id"] == BIG)["customer_id"])["name"], "amount": 2000.0,
        "reason": "Covered. Section 10 sets the payment."}
    assert view(app, conversation_id).json()["turns"][0]["confirmation"] == card
    nothing_held = app.post(f"/copilot/turns/{turn_id}/pause", json={"claim_id": "CLM-1001"}, headers=header)
    assert nothing_held.status_code == 409  # nothing held on that claim: no card


def app_data_dir():
    from tests.conftest import DATA_DIR

    return str(DATA_DIR)


def test_a_copilot_that_was_not_reached_fails_the_turn_and_frees_the_conversation(app, copilot):
    conversation_id = new_conversation(app)
    copilot.fail = httpx.ConnectError("the Copilot is down")
    refused = say(app, conversation_id)
    assert refused.status_code == 503
    body = view(app, conversation_id).json()
    assert body["turns"][0]["status"] == "failed" and body["turns"][0]["error"]
    copilot.fail = None
    assert say(app, conversation_id, "try again").status_code == 202


def test_a_copilot_that_refuses_a_turn_passes_the_reason_on(app, copilot):
    copilot.answer = {"refused": "that conversation is busy"}
    conversation_id = new_conversation(app)
    refused = say(app, conversation_id)
    assert refused.status_code == 409 and refused.json()["detail"] == "that conversation is busy"
    assert view(app, conversation_id).json()["turns"][0]["status"] == "failed"


def test_an_answer_the_copilot_was_not_reached_for_leaves_the_card_waiting(app, copilot):
    conversation_id = new_conversation(app)
    turn_id = say(app, conversation_id).json()["turn_id"]
    hold_big_payout(app)
    header = as_handler()[0]
    app.post(f"/copilot/turns/{turn_id}/pause", json={"claim_id": BIG}, headers=header)
    copilot.fail = httpx.ConnectError("down")
    assert app.post(f"/copilot/conversations/{conversation_id}/confirmation", json={"answer": "sign_off"}, headers=header).status_code == 503
    copilot.fail = None
    assert view(app, conversation_id).json()["turns"][0]["status"] == "waiting"
    assert app.post(f"/copilot/conversations/{conversation_id}/confirmation", json={"answer": "sign_off"}, headers=header).status_code == 202


def test_steps_and_the_end_of_a_turn_are_kept_with_the_conversation(app):
    conversation_id = new_conversation(app)
    turn_id = say(app, conversation_id).json()["turn_id"]
    header = as_handler()[0]
    step_id = app.post(f"/copilot/turns/{turn_id}/steps", json=STEP, headers=header).json()["step_id"]
    working = view(app, conversation_id).json()["turns"][0]["steps"][0]
    assert (working["status"], working["tool"], working["main_input"], working["kind"]) == ("working", "get_claim", "CLM-1012", "claims")
    assert app.post(f"/copilot/steps/{step_id}", json={"status": "done", "output": "the claim", "sources": [{"file": "a.md", "owner": "Ops"}], "reason": None},
                    headers=header).status_code == 200
    done = view(app, conversation_id).json()["turns"][0]["steps"][0]
    assert done["status"] == "done" and done["output"] == "the claim" and done["sources"] == [{"file": "a.md", "owner": "Ops"}]
    assert done["input"] == {"claim_id": "CLM-1012"} and done["seconds"] is not None
    assert app.post(f"/copilot/turns/{turn_id}/end", json={"answer": "All done.", "model_calls": 3, "cost_usd": 0.0123}, headers=header).status_code == 200
    turn = view(app, conversation_id).json()["turns"][0]
    assert (turn["status"], turn["answer"], turn["model_calls"], turn["cost_usd"]) == ("done", "All done.", 3, 0.0123)
    assert say(app, conversation_id, "next").status_code == 202  # the conversation is free again


def test_a_failed_turn_keeps_its_reason_and_frees_the_conversation(app):
    conversation_id = new_conversation(app)
    turn_id = say(app, conversation_id).json()["turn_id"]
    header = as_handler()[0]
    app.post(f"/copilot/turns/{turn_id}/fail", json={"reason": "TooManyModelCalls: 26"}, headers=header)
    turn = view(app, conversation_id).json()["turns"][0]
    assert (turn["status"], turn["error"]) == ("failed", "TooManyModelCalls: 26")
    assert say(app, conversation_id, "again").status_code == 202


def test_a_report_after_the_turn_has_ended_is_refused(app):
    conversation_id = new_conversation(app)
    turn_id = say(app, conversation_id).json()["turn_id"]
    header = as_handler()[0]
    app.post(f"/copilot/turns/{turn_id}/end", json={"answer": "x", "model_calls": 1, "cost_usd": None}, headers=header)
    assert app.post(f"/copilot/turns/{turn_id}/end", json={"answer": "y", "model_calls": 1, "cost_usd": None}, headers=header).status_code == 409
    assert view(app, conversation_id).json()["turns"][0]["answer"] == "x"


def test_a_message_must_be_text_of_a_reasonable_length(app):
    conversation_id = new_conversation(app)
    assert say(app, conversation_id, "").status_code == 422
    assert say(app, conversation_id, "x" * 4001).status_code == 422


def test_reseed_drops_the_conversations(app, engine, data_dir):
    conversation_id = new_conversation(app)
    say(app, conversation_id)
    reseed(engine, Reference(data_dir))
    db = RawDb(engine)
    assert db.execute("SELECT COUNT(*) FROM copilot_conversations").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM copilot_turns").fetchone()[0] == 0


def test_the_token_is_never_stored(app, engine):
    header, token = as_handler()
    conversation_id = new_conversation(app)
    turn_id = say(app, conversation_id).json()["turn_id"]
    hold_big_payout(app)
    app.post(f"/copilot/turns/{turn_id}/steps", json=STEP, headers=header)
    app.post(f"/copilot/turns/{turn_id}/pause", json={"claim_id": BIG}, headers=header)
    app.post(f"/copilot/conversations/{conversation_id}/confirmation", json={"answer": "cancel"}, headers=header)
    db = RawDb(engine)
    for table in ("copilot_conversations", "copilot_turns", "copilot_steps"):
        for row in db.execute(f"SELECT * FROM {table}"):
            assert token not in repr(dict(row)), table
