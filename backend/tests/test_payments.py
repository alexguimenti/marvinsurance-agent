import pytest

from app.api import create_app
from app.claims import hold_for_sign_off
from app.config import Settings
from app.db import init_schema
from app.payments import get_payment, pay, sign_off
from app.reference import Reference
from app.seed import seed_claims
from tests.fakes import record_decision, MINT, TEST_KEY, api_client, approve, connect


@pytest.fixture
def ref(data_dir):
    return Reference(data_dir)


@pytest.fixture
def engine(db_path, ref):
    c = connect(db_path)
    init_schema(c.engine)
    seed_claims(c.engine, ref)
    return c.engine


def test_exactly_the_limit_is_paid_without_a_sign_off(engine, ref):
    record_decision(engine, ref, "CLM-1001", approve(1000.00), "m")
    pay(engine, ref, "CLM-1001")
    assert get_payment(engine, "CLM-1001").status == "paid"


def test_a_cent_over_the_limit_waits(engine, ref):
    record_decision(engine, ref, "CLM-1001", approve(1000.01), "m")
    hold_for_sign_off(engine, ref, "CLM-1001")
    assert get_payment(engine, "CLM-1001").status == "held"
    sign_off(engine, ref, "PAY-CLM-1001", "HND-01")
    assert get_payment(engine, "CLM-1001").approved_by == "HND-01"


# --- the payment operation and the sign-off that pays ----------------------------------------------------

@pytest.fixture
def pay_client(db_path, data_dir):
    settings = Settings(_env_file=None, agent_provider="scripted", db_path=db_path, data_dir=data_dir)
    with api_client(create_app(settings, jwks=MINT), headers={"X-API-Key": TEST_KEY}) as c:
        yield c


def test_a_payment_over_the_limit_on_a_claim_never_approved_is_a_403_naming_the_limit(pay_client):
    for claim_id in ("CLM-1001", "CLM-9999"):  # an unknown claim gets the same answer: the limit comes first
        answer = pay_client.post(f"/claims/{claim_id}/pay", json={"amount": 1000.01})
        assert answer.status_code == 403 and "$1,000.00" in answer.json()["detail"]


def test_a_payment_with_no_approval_to_pay_is_refused_after_the_limit_check(pay_client):
    assert pay_client.post("/claims/CLM-1001/pay", json={"amount": 100}).status_code == 409


def test_the_limit_comes_from_the_setting(pay_client):
    senior = MINT.header("HND-01")
    pay_client.put("/settings/approval_limit", json={"value": 50}, headers=senior)
    answer = pay_client.post("/claims/CLM-1001/pay", json={"amount": 51})
    assert answer.status_code == 403 and "$50.00" in answer.json()["detail"]


def test_a_payment_takes_the_key_and_refuses_a_sign_in(db_path, data_dir):
    settings = Settings(_env_file=None, agent_provider="scripted", db_path=db_path, data_dir=data_dir)
    with api_client(create_app(settings, jwks=MINT)) as c:
        assert c.post("/claims/CLM-1001/pay", json={"amount": 1}).status_code == 401
        assert c.post("/claims/CLM-1001/pay", json={"amount": 1}, headers=MINT.header()).status_code == 401  # the key is the agent's


def test_a_sign_off_pays_the_claim_once(db_path, data_dir):
    conn = connect(db_path)
    init_schema(conn.engine)
    ref = Reference(data_dir)
    seed_claims(conn.engine, ref)
    conn.execute("UPDATE claims SET status = 'in_progress' WHERE claim_id = 'CLM-1003'")
    record_decision(conn.engine, ref, "CLM-1003", approve(2000.0), "m")
    hold_for_sign_off(conn.engine, ref, "CLM-1003")
    first = sign_off(conn.engine, ref, "PAY-CLM-1003", "HND-01")
    second = sign_off(conn.engine, ref, "PAY-CLM-1003", "HND-01")  # a repeat changes nothing
    assert (first.status, first.approved_at, first.paid_at) == (second.status, second.approved_at, second.paid_at) and first.status == "paid"
    assert conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0] == 1
    paid_steps = [r for r in conn.execute("SELECT content FROM trace_events WHERE kind = 'status'") if '"paid"' in r["content"]]
    assert len(paid_steps) == 1
    assert conn.execute("SELECT status FROM claims WHERE claim_id = 'CLM-1003'").fetchone()[0] == "decided"
