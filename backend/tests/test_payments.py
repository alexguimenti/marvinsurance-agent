import pytest

from app.claims import hold_for_sign_off, record_decision
from app.db import init_schema
from app.payments import get_payment, pay, sign_off
from app.reference import Reference
from app.seed import seed_claims
from tests.fakes import approve, connect


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
