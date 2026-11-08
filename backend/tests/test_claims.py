import pytest

from app.claims import DecisionConflict, get_claim, hold_for_sign_off, needs_sign_off, record_decision
from app.db import init_schema
from app.payments import SignOffRequired, get_payment, pay, sign_off
from app.reference import Reference
from app.seed import seed_claims
from tests.fakes import connect

CLAIM = "CLM-1001"


def approve(payout):
    return {"decision": "approve", "payout": payout, "calculation": [{"step": "x", "amount": payout}],
            "rationale": "Covered. Section 10 sets the payment.", "citations": ["Policy wording, section 10"]}


DECLINE = {"decision": "decline", "payout": 0.0, "calculation": [],
           "rationale": "Excluded. Section 9 does not pay it.", "citations": []}


@pytest.fixture
def ref(data_dir):
    return Reference(data_dir)


@pytest.fixture
def engine(db_path, ref):
    c = connect(db_path)
    init_schema(c.engine)
    seed_claims(c.engine, ref)
    c.execute("UPDATE claims SET status='in_progress' WHERE claim_id=?", (CLAIM,))
    return c.engine


def status(engine):
    return get_claim(engine, CLAIM)["status"]


def count(engine, table):
    return connect_count(engine, table)


def connect_count(engine, table):
    from tests.fakes import RawDb

    return RawDb(engine).execute(f"SELECT COUNT(*) FROM {table} WHERE claim_id=?", (CLAIM,)).fetchone()[0]


def test_recording_a_decision_returns_the_claim(engine, ref):
    claim = record_decision(engine, ref, CLAIM, DECLINE, "m")
    assert claim["claim_id"] == CLAIM and claim["status"] == "decided"


def test_decision_records_the_document_versions(engine, ref):
    record_decision(engine, ref, CLAIM, DECLINE, "m")
    assert get_claim(engine, CLAIM)["decision"]["manual_version"] == "2026.2"


def test_repeating_a_decision_counts_as_done(engine, ref):
    record_decision(engine, ref, CLAIM, approve(500.0), "m")
    claim = record_decision(engine, ref, CLAIM, approve(500.0), "m")
    assert claim["claim_id"] == CLAIM
    assert count(engine, "decisions") == 1


@pytest.mark.parametrize("other", [approve(600.0), DECLINE])
def test_a_different_decision_is_refused(engine, ref, other):
    record_decision(engine, ref, CLAIM, approve(500.0), "m")
    with pytest.raises(DecisionConflict):
        record_decision(engine, ref, CLAIM, other, "m")
    assert get_claim(engine, CLAIM)["decision"]["payout"] == 500.0


def test_an_approval_is_not_decided_until_it_is_paid(engine, ref):
    assert record_decision(engine, ref, CLAIM, approve(500.0), "m")["status"] == "in_progress"


@pytest.mark.parametrize("decision,expected", [(approve(1000.01), True), (approve(1000.0), False), (DECLINE, False)])
def test_needs_sign_off_is_the_backends_call(engine, ref, decision, expected):
    record_decision(engine, ref, CLAIM, decision, "m")
    assert needs_sign_off(engine, ref, CLAIM) is expected


def test_no_decision_needs_no_sign_off(engine, ref):
    assert needs_sign_off(engine, ref, CLAIM) is False


def test_holding_makes_one_held_payment_and_waits(engine, ref):
    record_decision(engine, ref, CLAIM, approve(2000.0), "m")
    hold_for_sign_off(engine, ref, CLAIM)
    claim = hold_for_sign_off(engine, ref, CLAIM)
    assert claim["status"] == "awaiting_approval"
    assert get_payment(engine, CLAIM).status == "held"
    assert count(engine, "payments") == 1


def test_holding_after_a_sign_off_changes_nothing(engine, ref):
    record_decision(engine, ref, CLAIM, approve(2000.0), "m")
    payment = get_payment(engine, hold_for_sign_off(engine, ref, CLAIM)["claim_id"])
    sign_off(engine, ref, payment.payment_id, "HND-01")
    assert hold_for_sign_off(engine, ref, CLAIM)["status"] == "in_progress"
    assert get_payment(engine, CLAIM).status == "signed_off"


def test_pay_within_limit(engine, ref):
    record_decision(engine, ref, CLAIM, approve(500.0), "m")
    claim = pay(engine, ref, CLAIM)
    assert claim["status"] == "decided"
    payment = get_payment(engine, CLAIM)
    assert (payment.status, payment.amount) == ("paid", 500.0) and payment.paid_at


def test_pay_over_limit_without_sign_off_is_refused(engine, ref):
    record_decision(engine, ref, CLAIM, approve(2000.0), "m")
    with pytest.raises(SignOffRequired):
        pay(engine, ref, CLAIM)
    assert get_payment(engine, CLAIM) is None
    hold_for_sign_off(engine, ref, CLAIM)
    with pytest.raises(SignOffRequired):
        pay(engine, ref, CLAIM)
    assert get_payment(engine, CLAIM).status == "held"


def test_paying_needs_an_approval(engine, ref):
    record_decision(engine, ref, CLAIM, DECLINE, "m")
    with pytest.raises(ValueError):
        pay(engine, ref, CLAIM)
    assert get_payment(engine, CLAIM) is None


def test_paying_twice_pays_once(engine, ref):
    record_decision(engine, ref, CLAIM, approve(500.0), "m")
    pay(engine, ref, CLAIM)
    paid_at = get_payment(engine, CLAIM).paid_at
    assert pay(engine, ref, CLAIM)["claim_id"] == CLAIM
    assert count(engine, "payments") == 1
    assert get_payment(engine, CLAIM).paid_at == paid_at


def test_pay_amount_comes_from_the_decision(engine, ref):
    record_decision(engine, ref, CLAIM, approve(2000.0), "m")
    hold_for_sign_off(engine, ref, CLAIM)
    sign_off(engine, ref, f"PAY-{CLAIM}", "HND-01")
    pay(engine, ref, CLAIM)
    assert get_payment(engine, CLAIM).amount == 2000.0


def test_sign_off_does_not_pay(engine, ref):
    record_decision(engine, ref, CLAIM, approve(2000.0), "m")
    hold_for_sign_off(engine, ref, CLAIM)
    payment = sign_off(engine, ref, f"PAY-{CLAIM}", "HND-01")
    assert payment.status == "signed_off" and payment.paid_at is None
    assert payment.approved_by == "HND-01" and payment.approved_at
    assert status(engine) == "in_progress"
    assert needs_sign_off(engine, ref, CLAIM) is False


def test_sign_off_twice_keeps_the_first_handler(engine, ref):
    record_decision(engine, ref, CLAIM, approve(2000.0), "m")
    hold_for_sign_off(engine, ref, CLAIM)
    first = sign_off(engine, ref, f"PAY-{CLAIM}", "HND-01")
    second = sign_off(engine, ref, f"PAY-{CLAIM}", "HND-01")
    assert second.approved_by == "HND-01" and second.approved_at == first.approved_at


def test_sign_off_after_paying_records_nothing(engine, ref):
    record_decision(engine, ref, CLAIM, approve(2000.0), "m")
    hold_for_sign_off(engine, ref, CLAIM)
    sign_off(engine, ref, f"PAY-{CLAIM}", "HND-01")
    pay(engine, ref, CLAIM)
    assert sign_off(engine, ref, f"PAY-{CLAIM}", "HND-01").approved_by == "HND-01"
    assert status(engine) == "decided"


def test_handler_without_the_right_is_refused(engine, ref):
    record_decision(engine, ref, CLAIM, approve(2000.0), "m")
    hold_for_sign_off(engine, ref, CLAIM)
    with pytest.raises(PermissionError):
        sign_off(engine, ref, f"PAY-{CLAIM}", "HND-99")
    assert get_payment(engine, CLAIM).status == "held"


def test_sign_off_of_an_unknown_payment(engine, ref):
    with pytest.raises(LookupError):
        sign_off(engine, ref, "PAY-NOPE", "HND-01")


def test_a_handler_who_cannot_sign_off_over_the_limit_is_refused(engine, ref):
    record_decision(engine, ref, CLAIM, approve(2000.0), "m")
    hold_for_sign_off(engine, ref, CLAIM)
    for handler_id in ("HND-02", "HND-03"):  # Marcus and Priya
        with pytest.raises(PermissionError):
            sign_off(engine, ref, f"PAY-{CLAIM}", handler_id)
    assert get_payment(engine, CLAIM).status == "held"
