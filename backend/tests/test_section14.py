"""A decision record meets claims manual section 14: the decision, the payout, the calculation, the section relied on, a
reason, who or what made the decision and when, and the manual's version."""
import pytest

from app.api import create_app
from app.config import Settings
from tests.fakes import MINT, TEST_KEY, api_client, record_approval

KEY = {"X-API-Key": TEST_KEY}
CLAIM = "CLM-1001"
CALCULATE = {"items": [{"description": "Payout", "amount": 420.0, "item_limit": 0}], "third_party_paid": 0, "deductible": 0,
             "benefit_max": 0}


@pytest.fixture
def client(db_path, data_dir):
    settings = Settings(_env_file=None, agent_provider="scripted", db_path=db_path, data_dir=data_dir)
    with api_client(create_app(settings, jwks=MINT)) as c:
        yield c


def decision(**changes) -> dict:
    return {"decision": "approve", "payout": 420.0, "rationale": "Covered. Section 10 sets the payment.",
            "citations": ["Policy wording, section 10"], "decided_by": "UnderCover Agent, configuration version 1", "model": "m",
            **changes}


def record(client, claim_id=CLAIM, **changes):
    return client.post(f"/claims/{claim_id}/decision", json=decision(**changes), headers=KEY)


def detail(client, claim_id=CLAIM) -> dict:
    return client.get(f"/claims/{claim_id}", headers=KEY).json()


@pytest.mark.parametrize("claim_id", [CLAIM, "CLM-0000"])  # the same answer on any claim, known or not
@pytest.mark.parametrize("changes,message", [
    ({"rationale": "  "}, "Claims manual section 14: the reason is blank."),
    ({"citations": []}, "Claims manual section 14: the section relied on is blank."),
    ({"citations": ["", "  "]}, "Claims manual section 14: the section relied on is blank."),
    ({"decided_by": " "}, "Claims manual section 14: who made the decision is blank."),
    ({"payout": 0.0}, "Claims manual section 14: an approval pays more than zero."),
    ({"decision": "decline", "payout": 5.0}, "Claims manual section 14: a decline or a referral pays nothing."),
    ({"decision": "refer", "payout": 1.0}, "Claims manual section 14: a decline or a referral pays nothing."),
])
def test_each_request_check_is_a_422_naming_section_14(client, claim_id, changes, message):
    answer = record(client, claim_id, **changes)
    assert answer.status_code == 422 and answer.json()["detail"] == message


def test_an_approval_with_no_calculation_on_file_is_refused(client):
    answer = record(client)
    assert answer.status_code == 422
    assert answer.json()["detail"] == ("Claims manual section 14: an approval needs a payout calculation on file for this "
                                       "attempt. Call calculate_payout first.")
    assert detail(client)["decision"] is None


def test_an_approval_that_differs_from_the_calculation_is_refused(client):
    client.post(f"/claims/{CLAIM}/payout", json=CALCULATE, headers=KEY)
    answer = record(client, payout=500.0)
    assert answer.status_code == 422
    assert answer.json()["detail"] == "Claims manual section 14: the payout $500.00 does not match the calculation on file ($420.00)."


def test_a_calculation_from_an_earlier_attempt_does_not_count(client):
    client.post(f"/agent/claims/{CLAIM}/take", json={"run_id": None}, headers=KEY)
    client.post(f"/claims/{CLAIM}/payout", json=CALCULATE, headers=KEY)
    client.post(f"/agent/claims/{CLAIM}/fail", json={"attempt": 1, "max_attempts": 2, "message": "boom"}, headers=KEY)
    client.post(f"/agent/claims/{CLAIM}/take", json={"run_id": None}, headers=KEY)  # the second attempt
    answer = record(client)
    assert answer.status_code == 422 and "needs a payout calculation on file for this attempt" in answer.json()["detail"]
    client.post(f"/claims/{CLAIM}/payout", json=CALCULATE, headers=KEY)  # calculated again in this attempt
    assert record(client).status_code == 200


def test_cents_noise_matches(client):
    body = {**CALCULATE, "items": [{"description": "Payout", "amount": 419.999999, "item_limit": 0}]}
    client.post(f"/claims/{CLAIM}/payout", json=body, headers=KEY)
    assert record(client, payout=420.0).status_code == 200


def test_the_latest_calculation_counts(client):
    client.post(f"/claims/{CLAIM}/payout", json={**CALCULATE, "items": [{"description": "x", "amount": 100.0, "item_limit": 0}]}, headers=KEY)
    client.post(f"/claims/{CLAIM}/payout", json=CALCULATE, headers=KEY)
    assert record(client).status_code == 200


def test_calculating_for_an_unknown_claim_is_a_404(client):
    assert client.post("/claims/CLM-0000/payout", json=CALCULATE, headers=KEY).status_code == 404


def test_a_complete_approval_holds_its_calculation_maker_time_and_manual_version(client):
    assert record_approval(client, CLAIM, 420.0, decided_by="UnderCover Agent, configuration version 1").status_code == 200
    record_ = detail(client)["decision"]
    assert record_["calculation"] == {"lines": [{"step": "Payout", "amount": 420.0}], "payout": 420.0}
    assert record_["decided_by"] == "UnderCover Agent, configuration version 1"
    assert record_["manual_version"] == "2026.2" and record_["wording_version"] == "2026.1"
    assert record_["decided_at"] and "T" in record_["decided_at"]
    assert (record_["decision"], record_["payout"], record_["citations"]) == ("approve", 420.0, ["Policy wording, section 10"])


def test_the_calculation_on_a_record_is_the_one_calculate_payout_gave_not_one_the_caller_sends(client):
    body = {**CALCULATE, "items": [{"description": "Hospital bill", "amount": 3800.0, "item_limit": 0}], "deductible": 50}
    on_file = client.post(f"/claims/{CLAIM}/payout", json=body, headers=KEY).json()
    sent = decision(payout=on_file["payout"], calculation=[{"step": "A made up line", "amount": on_file["payout"]}])
    assert client.post(f"/claims/{CLAIM}/decision", json=sent, headers=KEY).status_code == 200
    lines = detail(client)["decision"]["calculation"]["lines"]
    assert lines == on_file["steps"] and "made up" not in str(lines)


def test_a_decline_has_no_calculation_and_a_referral_by_the_security_check_names_its_maker(client):
    assert record(client, decision="decline", payout=0.0, decided_by="UnderCover Agent, configuration version 1").status_code == 200
    assert detail(client)["decision"]["calculation"] is None
    assert record(client, "CLM-1002", decision="refer", payout=0.0, decided_by="Security check", citations=["Security check"]).status_code == 200
    referral = detail(client, "CLM-1002")["decision"]
    assert referral["calculation"] is None and referral["decided_by"] == "Security check" and referral["manual_version"] == "2026.2"


def test_a_sign_off_adds_who_signed_off_and_when(client):
    client.post("/agent/claims/CLM-1003/take", json={"run_id": None}, headers=KEY)
    record_approval(client, "CLM-1003", 2000.0)
    client.post("/agent/claims/CLM-1003/hold", headers=KEY)
    assert detail(client, "CLM-1003")["decision"]["signed_off_by"] is None
    done = client.post("/payments/PAY-CLM-1003/approve", headers=MINT.header("HND-01"))
    assert done.status_code == 200
    signed = detail(client, "CLM-1003")["decision"]
    assert signed["signed_off_by"] == "Dana Whitfield" and signed["signed_off_at"]


def test_a_repeat_after_a_restart_counts_as_done(client):
    assert record_approval(client, CLAIM, 420.0).status_code == 200
    assert record(client).status_code == 200  # the same record again: the calculation on file still matches


def test_an_operation_never_holds_a_connection_while_it_asks_for_another(db_path, data_dir):
    """Up to 25 claim agents share the backend's 4 connections. A request that holds one while it waits for a second would
    stall them all once enough did: so the whole recording runs on a pool of one, which refuses a second connection."""
    from sqlalchemy import create_engine
    from sqlalchemy.pool import QueuePool

    from app import claims as claim_records
    from app.db import init_schema
    from app.reference import Reference
    from app.seed import seed_claims
    from tests.fakes import record_decision, write_calculation

    ref = Reference(data_dir)
    first = create_engine(f"sqlite:///{db_path}")
    init_schema(first)
    seed_claims(first, ref)
    first.dispose()
    one = create_engine(f"sqlite:///{db_path}", poolclass=QueuePool, pool_size=1, max_overflow=0, pool_timeout=1)
    write_calculation(one, CLAIM, 420.0)
    approve = {"decision": "approve", "payout": 420.0, "rationale": "Covered. Section 10.", "citations": ["x"], "decided_by": "Test"}
    claim_records.record_decision(one, ref, CLAIM, approve, "m")  # raises a pool timeout if it asks for a second connection
    claim_records.hold_for_sign_off(one, ref, CLAIM)
    one.dispose()
