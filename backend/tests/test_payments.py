import pytest

from app.db import init_schema
from tests.fakes import connect
from app.payments import request_payment
from app.reference import Reference


@pytest.fixture
def conn(db_path):
    c = connect(db_path)
    init_schema(c.engine)
    return c


@pytest.fixture
def ref(data_dir):
    return Reference(data_dir)


def test_exactly_limit_is_paid(conn):
    assert request_payment(conn.engine, "CLM-1", 1000.00, 1000.0).status == "paid"


def test_just_over_limit_is_held(conn):
    assert request_payment(conn.engine, "CLM-2", 1000.01, 1000.0).status == "held"


def test_request_payment_twice_is_one_record(conn):
    first = request_payment(conn.engine, "CLM-3", 500.0, 1000.0)
    second = request_payment(conn.engine, "CLM-3", 9999.0, 1000.0)
    assert second.payment_id == first.payment_id
    assert second.amount == 500.0
    assert conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0] == 1
