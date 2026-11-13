"""The reads the Copilot needs and a handler may use: customers, a policy by id, a dispatch by id, and the count of payouts
paid over the approval limit."""
import pytest

from app import runs
from app.api import create_app
from app.config import Settings
from app.db import make_engine
from app.reference import Reference
from tests.fakes import MINT, TEST_KEY, RawDb, api_client

DANA = MINT.header("HND-01")


@pytest.fixture
def parts(db_path, data_dir):
    settings = Settings(_env_file=None, agent_provider="scripted", db_path=db_path, data_dir=data_dir)
    with api_client(create_app(settings, jwks=MINT)) as client:
        yield client, make_engine(settings, max_connections=1), Reference(data_dir)


def claim_of(ref, customer_id):
    return next(c for c in ref.claims if c["customer_id"] == customer_id)


def test_find_customers_by_part_of_a_name_or_an_id(parts):
    client, _, ref = parts
    maya = ref.read("customers.json")[0]
    by_name = client.get("/customers", params={"name": maya["name"][:4].upper()}, headers=DANA).json()
    assert maya["customer_id"] in [c["customer_id"] for c in by_name]
    assert {"customer_id", "name", "customer_since"} == set(by_name[0])
    by_id = client.get("/customers", params={"name": maya["customer_id"]}, headers=DANA).json()
    assert [c["customer_id"] for c in by_id] == [maya["customer_id"]]
    assert client.get("/customers", params={"name": "zzzz-nobody"}, headers=DANA).json() == []
    assert client.get("/customers", params={"name": ""}, headers=DANA).status_code == 422


def test_a_customer_with_policies_earlier_claims_and_queue_claims(parts):
    client, _, ref = parts
    claim = ref.claims[0]
    customer = ref.customer(claim["customer_id"])
    body = client.get(f"/customers/{customer['customer_id']}", headers=DANA).json()
    assert body["customer"]["name"] == customer["name"]
    assert all(p["customer_id"] == customer["customer_id"] for p in body["policies"]) and body["policies"]
    assert body["previous_claims"] == customer["previous_claims"]
    queued = {c["claim_id"]: c for c in body["queue_claims"]}
    assert claim["claim_id"] in queued and queued[claim["claim_id"]]["status"] == "waiting"
    assert client.get("/customers/CUS-0000", headers=DANA).status_code == 404


def test_a_policy_by_id(parts):
    client, _, ref = parts
    policy = ref.read("policies.json")[0]
    assert client.get(f"/policies/{policy['policy_id']}", headers=DANA).json() == policy
    assert client.get("/policies/POL-0000", headers=DANA).status_code == 404


def test_a_dispatch_by_id_and_404_for_an_evaluation(parts):
    client, engine, ref = parts
    dispatch = runs.start_dispatch(engine, 2, "scripted", "default", 1)
    report = client.get(f"/dispatches/{dispatch}", headers=DANA).json()
    assert report["run"]["run_id"] == dispatch and report["run"]["kind"] == "dispatch"
    assert set(report) == {"run", "claims", "totals", "summary"}
    assert client.get("/dispatches/9999", headers=DANA).status_code == 404
    runs.finish_run(engine, dispatch)
    from app import evaluations as evaluation_runs

    evaluation = evaluation_runs.start_evaluation(engine, ref, "evaluation", True, 1, "scripted", "default", config_version=1)
    assert client.get(f"/dispatches/{evaluation}", headers=DANA).status_code == 404


def test_over_limit_counts_with_none_paid(parts):
    client, _, _ = parts
    body = client.get("/payouts/over-limit", headers=DANA).json()
    assert body == {"approval_limit": 1000.0, "paid_over_limit": 0, "signed_off": 0}


def test_over_limit_counts_a_signed_off_payout_and_leaves_out_copies(parts):
    client, engine, ref = parts
    db = RawDb(engine)
    queue = [c["claim_id"] for c in ref.claims[:4]]
    db.execute("INSERT INTO payments (payment_id, claim_id, amount, status, approved_by) VALUES ('P1', ?, 2000, 'paid', 'HND-01')", (queue[0],))
    db.execute("INSERT INTO payments (payment_id, claim_id, amount, status, approved_by) VALUES ('P2', ?, 1500, 'paid', NULL)", (queue[1],))
    db.execute("INSERT INTO payments (payment_id, claim_id, amount, status, approved_by) VALUES ('P3', ?, 900, 'paid', NULL)", (queue[2],))  # under the limit
    db.execute("INSERT INTO payments (payment_id, claim_id, amount, status, approved_by) VALUES ('P4', ?, 3000, 'held', NULL)", (queue[3],))  # not paid
    db.execute("INSERT INTO claims (claim_id, status, payload, copy_of, submitted_on) SELECT 'CLM-COPY', status, payload, ?, submitted_on FROM claims WHERE claim_id = ?",
               (queue[0], queue[0]))
    db.execute("INSERT INTO payments (payment_id, claim_id, amount, status, approved_by) VALUES ('P5', 'CLM-COPY', 5000, 'paid', NULL)")  # a copy
    body = client.get("/payouts/over-limit", headers=DANA).json()
    assert (body["paid_over_limit"], body["signed_off"]) == (2, 1)


def test_each_read_needs_a_sign_in(parts):
    client, _, ref = parts
    paths = ["/customers?name=a", f"/customers/{ref.claims[0]['customer_id']}", "/policies/POL-2001", "/dispatches/1", "/payouts/over-limit"]
    for path in paths:
        assert client.get(path).status_code == 401, path
        assert client.get(path, headers={"X-API-Key": TEST_KEY}).status_code == 401, path
