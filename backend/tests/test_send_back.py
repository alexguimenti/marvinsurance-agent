"""A senior handler sends a claim waiting for sign-off back to the agent with a note, and the agent decides again."""
import json
import threading

import pytest

from agent.dispatcher import dispatch
from agent.worker import claim_paused, resume, work_claim
from app import runs
from app.api import create_app
from app.claims import claim_state, get_claim
from app.config import Settings
from app.db import make_engine
from app.payments import get_payment
from app.reference import Reference
from app.send_back import MAX_NOTE, held_by_a_resume, heard_from_resume, send_back, take_sent_back
from tests.fakes import MINT, TEST_KEY, RawDb, agent_env, api_client, approve, record_approval, stub

BIG = "CLM-1003"
KEY = {"X-API-Key": TEST_KEY}
NOTE = "Check the per-item limit again."


def settings_for(db_path, data_dir):
    return Settings(_env_file=None, agent_provider="scripted", db_path=db_path, data_dir=data_dir)


@pytest.fixture
def client(db_path, data_dir):
    with api_client(create_app(settings_for(db_path, data_dir), jwks=MINT)) as c:
        yield c


def held_claim(client, claim_id=BIG, payout=2000.0):
    client.post(f"/agent/claims/{claim_id}/take", json={"run_id": None}, headers=KEY)
    record_approval(client, claim_id, payout)
    client.post(f"/agent/claims/{claim_id}/hold", headers=KEY)


def send(client, claim_id=BIG, note=NOTE, who="HND-01"):
    return client.post(f"/claims/{claim_id}/send-back", json={"note": note}, headers=MINT.header(who))


def steps(env, kind, claim=BIG):
    rows = RawDb(env.engine).execute("SELECT content FROM trace_events WHERE claim_id=? AND kind=? ORDER BY seq", (claim, kind))
    return [json.loads(r[0]) for r in rows]


@pytest.mark.parametrize("note,who,code", [
    ("", "HND-01", 422), ("   ", "HND-01", 422), ("x" * (MAX_NOTE + 1), "HND-01", 422), (NOTE, "HND-02", 403),
])
def test_send_back_refusals(client, note, who, code):
    held_claim(client)
    assert send(client, note=note, who=who).status_code == code
    assert client.get(f"/claims/{BIG}", headers=KEY).json()["status"] == "awaiting_approval"  # nothing changed


def test_a_claim_not_waiting_and_an_evaluations_copy_are_a_409(client, db_path, data_dir):
    assert send(client, "CLM-1001").status_code == 409  # waiting in the queue
    held_claim(client)
    RawDb(make_engine(settings_for(db_path, data_dir), 1)).execute("UPDATE claims SET copy_of = 'CLM-1002' WHERE claim_id = 'CLM-1003'")
    answer = send(client)
    assert answer.status_code == 409 and "evaluation's copy" in answer.json()["detail"]
    assert send(client, "CLM-0000").status_code == 404


def test_the_send_back_keeps_the_decision_with_its_note_and_clears_the_rest(client, db_path, data_dir):
    held_claim(client)
    answer = send(client)
    assert answer.status_code == 200 and answer.json()["status"] == "sent_back"
    raw = RawDb(make_engine(settings_for(db_path, data_dir), 1))
    kept = raw.execute("SELECT decision, payout, note, sent_back_by FROM sent_back_decisions WHERE claim_id = 'CLM-1003'")[0]
    assert (kept[0], kept[1], kept[2], kept[3]) == ("approve", 2000.0, NOTE, "HND-01")
    assert raw.execute("SELECT COUNT(*) FROM decisions WHERE claim_id = 'CLM-1003'")[0][0] == 0
    assert raw.execute("SELECT COUNT(*) FROM payments WHERE claim_id = 'CLM-1003'")[0][0] == 0
    step = json.loads(raw.execute("SELECT content FROM trace_events WHERE claim_id = 'CLM-1003' AND kind = 'sent_back'")[0][0])
    assert step["note"] == NOTE and step["sent_back_by_name"] == "Dana Whitfield"


def test_a_sign_off_and_a_send_back_at_the_same_moment_only_one_wins(db_path, data_dir):
    settings = settings_for(db_path, data_dir)
    with api_client(create_app(settings, jwks=MINT)) as client:
        held_claim(client)
        codes, barrier = {}, threading.Barrier(2)

        def sign():
            barrier.wait()
            codes["sign"] = client.post("/payments/PAY-CLM-1003/approve", headers=MINT.header("HND-01")).status_code

        def back():
            barrier.wait()
            codes["back"] = send(client).status_code

        threads = [threading.Thread(target=sign), threading.Thread(target=back)]
        [t.start() for t in threads]
        [t.join(60) for t in threads]
        assert sorted(codes.values()) == [200, 409], codes
        engine = make_engine(settings, 1)
        status = get_claim(engine, BIG)["status"]
        payment = get_payment(engine, BIG)
        if codes["sign"] == 200:
            assert status == "decided" and payment.status == "paid"
        else:
            assert status == "sent_back" and payment is None


def test_a_sent_back_claim_is_taken_once(client, db_path, data_dir):
    held_claim(client)
    send(client)
    engine = make_engine(settings_for(db_path, data_dir), 1)
    assert [take_sent_back(engine, BIG), take_sent_back(engine, BIG)] == [True, False]
    assert get_claim(engine, BIG)["status"] == "in_progress"


def test_a_dispatch_leaves_alone_a_claim_whose_resume_has_been_heard_from(client, db_path, data_dir):
    held_claim(client)
    send(client)
    engine = make_engine(settings_for(db_path, data_dir), 1)
    assert BIG in runs.claims_to_dispatch(engine)  # a send back the agent never received is a dispatch's to work
    take_sent_back(engine, BIG)
    heard_from_resume(engine, BIG)
    assert BIG not in runs.claims_to_dispatch(engine)
    RawDb(engine).execute("UPDATE claims SET resume_heard_at = datetime('now', '-6 minutes') WHERE claim_id = 'CLM-1003'")
    assert BIG in runs.claims_to_dispatch(engine)  # silent for five minutes: it counts as stopped
    assert held_by_a_resume(None) is False


def paused_and_sent_back(db_path, data_dir, outcomes, **kwargs):
    """An agent env with CLM-1003 approved over the limit and paused, then sent back by Dana with a note."""
    cm = agent_env(db_path, data_dir, stub({BIG: outcomes}), **kwargs)
    env = cm.__enter__()
    work_claim(env, BIG)
    assert claim_paused(env, BIG)
    send_back(env.engine, Reference(data_dir), BIG, NOTE, "HND-01")
    return cm, env


def test_the_agent_decides_again_and_a_new_approval_within_the_limit_is_paid(db_path, data_dir):
    model = stub({BIG: [[approve(2000.0), approve(900.0)]]})
    with agent_env(db_path, data_dir, model) as env:
        work_claim(env, BIG)
        send_back(env.engine, Reference(data_dir), BIG, NOTE, "HND-01")
        assert env.engine is not None
        from app.send_back import take_sent_back as take

        assert take(env.engine, BIG)
        resume(env, BIG)
        assert get_claim(env.engine, BIG)["status"] == "decided" and get_payment(env.engine, BIG).amount == 900.0
        shown = [str(m.content) for m in model.shown[-1] if m.type == "human"]
        assert any("Dana Whitfield" in t and NOTE in t and "senior claims handler" in t for t in shown)
        assert get_claim(env.engine, BIG)["decision"]["payout"] == 900.0


def test_a_new_approval_over_the_limit_waits_for_sign_off_again(db_path, data_dir):
    model = stub({BIG: [[approve(2000.0), approve(1500.0)]]})
    with agent_env(db_path, data_dir, model) as env:
        work_claim(env, BIG)
        send_back(env.engine, Reference(data_dir), BIG, NOTE, "HND-01")
        take_sent_back(env.engine, BIG)
        resume(env, BIG)
        assert get_claim(env.engine, BIG)["status"] == "awaiting_approval" and claim_paused(env, BIG)
        assert get_payment(env.engine, BIG).amount == 1500.0 and get_payment(env.engine, BIG).status == "held"


def test_a_claim_can_be_sent_back_twice(db_path, data_dir):
    model = stub({BIG: [[approve(2000.0), approve(1800.0), approve(1200.0)]]})
    ref = Reference(data_dir)
    with agent_env(db_path, data_dir, model) as env:
        work_claim(env, BIG)
        for note in ("first note", "second note"):
            send_back(env.engine, ref, BIG, note, "HND-01")
            take_sent_back(env.engine, BIG)
            resume(env, BIG)
        rows = RawDb(env.engine).execute("SELECT note FROM sent_back_decisions WHERE claim_id = 'CLM-1003' ORDER BY id")
        assert [r[0] for r in rows] == ["first note", "second note"]
        assert get_payment(env.engine, BIG).amount == 1200.0


def test_a_send_back_the_agent_never_received_is_worked_by_the_next_dispatch(db_path, data_dir):
    model = stub({BIG: [[approve(2000.0), approve(900.0)]]})
    with agent_env(db_path, data_dir, model) as env:
        work_claim(env, BIG)
        send_back(env.engine, Reference(data_dir), BIG, NOTE, "HND-01")  # and the call to the agent failed
        run_id = runs.start_dispatch(env.engine, 1, "stub/model", None)
        dispatch(env, run_id, 1)
        assert get_claim(env.engine, BIG)["status"] == "decided" and get_payment(env.engine, BIG).amount == 900.0
        # the dispatch worked the whole queue: the note is in what the model was shown for this claim, not the last claim's
        assert any(NOTE in str(m.content) for shown in model.shown for m in shown if m.type == "human")


def test_the_note_is_in_code_not_in_the_versioned_instructions():
    from agent.prompts import send_back_message
    from app.configs import load_instructions

    assert "senior claims handler" in send_back_message("a note", "Dana Whitfield")
    assert "sent your decision back" not in load_instructions("claim")


def test_claim_detail_lists_sent_back_decisions_newest_first_and_says_it_waits_for_the_next_dispatch(client):
    held_claim(client)
    send(client, note="first note")
    detail = client.get(f"/claims/{BIG}", headers=KEY).json()
    assert detail["status"] == "sent_back" and detail["waiting_for_next_dispatch"] is True and detail["decision"] is None
    assert [b["note"] for b in detail["sent_back"]] == ["first note"]
    first = detail["sent_back"][0]
    assert (first["decision"], first["payout"], first["sent_back_by_name"]) == ("approve", 2000.0, "Dana Whitfield")
    assert first["calculation"]["lines"] and first["decided_by"] == "Test" and first["sent_back_at"]
    client.post(f"/agent/claims/{BIG}/take-sent-back", headers=KEY)
    record_approval(client, BIG, 1800.0)
    client.post(f"/agent/claims/{BIG}/hold", headers=KEY)
    client.post(f"/claims/{BIG}/send-back", json={"note": "second note"}, headers=MINT.header("HND-01"))
    notes = [b["note"] for b in client.get(f"/claims/{BIG}", headers=KEY).json()["sent_back"]]
    assert notes == ["second note", "first note"]
