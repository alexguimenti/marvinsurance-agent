import pytest

from app.db import connect, init_schema
from app.payments import approve_payment, request_payment
from app.reference import Reference


@pytest.fixture
def conn(db_path):
    c = connect(db_path)
    init_schema(c)
    return c


@pytest.fixture
def ref(data_dir):
    return Reference(data_dir)


def test_exactly_limit_is_paid(conn):
    assert request_payment(conn, "CLM-1", 1000.00, 1000.0).status == "paid"


def test_just_over_limit_is_held(conn):
    assert request_payment(conn, "CLM-2", 1000.01, 1000.0).status == "held"


def test_request_payment_twice_is_one_record(conn):
    first = request_payment(conn, "CLM-3", 500.0, 1000.0)
    second = request_payment(conn, "CLM-3", 9999.0, 1000.0)
    assert second.payment_id == first.payment_id
    assert second.amount == 500.0
    assert conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0] == 1


def test_approve_unknown_handler_rejected(conn, ref):
    p = request_payment(conn, "CLM-4", 2000.0, 1000.0)
    with pytest.raises(PermissionError):
        approve_payment(conn, p.payment_id, "HND-99", ref)
    assert conn.execute("SELECT status FROM payments").fetchone()[0] == "held"


def test_approve_held_becomes_paid_with_approver(conn, ref):
    p = request_payment(conn, "CLM-5", 2000.0, 1000.0)
    approved = approve_payment(conn, p.payment_id, "HND-01", ref)
    assert approved.status == "paid"
    assert approved.approved_by == "HND-01"


def test_double_approve_is_idempotent(conn, ref):
    p = request_payment(conn, "CLM-6", 2000.0, 1000.0)
    approve_payment(conn, p.payment_id, "HND-01", ref)
    again = approve_payment(conn, p.payment_id, "HND-02", ref)
    assert again.status == "paid"
    assert again.approved_by == "HND-01"
