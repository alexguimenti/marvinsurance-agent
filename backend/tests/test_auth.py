"""Handlers sign in with Supabase Auth, and the backend checks the handler on every action."""
import pytest
from fastapi.testclient import TestClient

from agent.service import create_service
from app.api import create_app
from app.auth import NotSignedIn, UnknownHandler, handler_from_token
from app.config import Settings
from app.reference import Reference
from tests.fakes import service_client, api_client, EMAILS, TokenMint


@pytest.fixture
def mint():
    return TokenMint()


@pytest.fixture
def ref(data_dir):
    return Reference(data_dir)


def test_a_valid_token_finds_the_handler_by_email(mint, ref):
    handler = handler_from_token(mint.token(EMAILS["HND-01"]), ref, mint)
    assert (handler.handler_id, handler.name, handler.can_approve_over_limit) == ("HND-01", "Dana Whitfield", True)


def test_the_email_is_matched_whatever_its_case(mint, ref):
    assert handler_from_token(mint.token(EMAILS["HND-01"].upper()), ref, mint).handler_id == "HND-01"


def test_marcus_and_priya_cannot_sign_off_over_the_limit(mint, ref):
    for handler_id in ("HND-02", "HND-03"):
        assert handler_from_token(mint.token(EMAILS[handler_id]), ref, mint).can_approve_over_limit is False


@pytest.mark.parametrize("bad", ["garbage", "", "a.b.c"])
def test_a_token_that_is_not_one_is_refused(mint, ref, bad):
    with pytest.raises(NotSignedIn):
        handler_from_token(bad, ref, mint)


def test_an_expired_token_is_refused(mint, ref):
    with pytest.raises(NotSignedIn):
        handler_from_token(mint.token(EMAILS["HND-01"], expires_in=-600), ref, mint)  # past the clock leeway


def test_a_token_from_a_clock_a_few_seconds_ahead_of_ours_is_accepted(mint, ref):
    # Supabase's clock and this computer's can differ by seconds; a fresh token must not be refused for it
    token = mint.token(EMAILS["HND-01"], issued_in=20)
    assert handler_from_token(token, ref, mint).handler_id == "HND-01"


def test_a_token_issued_well_in_the_future_is_refused(mint, ref):
    with pytest.raises(NotSignedIn):
        handler_from_token(mint.token(EMAILS["HND-01"], issued_in=600), ref, mint)


def test_another_audience_is_refused(mint, ref):
    with pytest.raises(NotSignedIn):
        handler_from_token(mint.token(EMAILS["HND-01"], audience="somebody-else"), ref, mint)


def test_a_token_signed_with_another_key_is_refused(mint, ref):
    with pytest.raises(NotSignedIn):
        handler_from_token(TokenMint().token(EMAILS["HND-01"]), ref, mint)


def test_a_token_for_an_unknown_email_is_refused(mint, ref):
    with pytest.raises(UnknownHandler):
        handler_from_token(mint.token("stranger@example.com"), ref, mint)


def test_with_no_keys_to_check_against_every_token_is_refused(mint, ref):
    with pytest.raises(NotSignedIn):
        handler_from_token(mint.token(EMAILS["HND-01"]), ref, None)


@pytest.fixture
def backend(db_path, data_dir, mint):
    settings = Settings(_env_file=None, agent_provider="scripted", db_path=db_path, data_dir=data_dir)
    with service_client(create_service(settings, inline=True)) as service:
        with api_client(create_app(settings, http=service, jwks=mint)) as client:
            yield client


ACTIONS = [("POST", "/dispatches", {"workers": 3}), ("POST", "/evaluations", {"eval_set": "evaluation", "tiny": True, "workers": 3}),
           ("POST", "/payments/PAY-CLM-1003/approve", None)]


@pytest.mark.parametrize("method,path,body", ACTIONS)
def test_every_handler_action_needs_a_sign_in(backend, method, path, body):
    assert backend.request(method, path, json=body).status_code == 401
    assert backend.request(method, path, json=body, headers={"Authorization": "Bearer nonsense"}).status_code == 401


def test_a_token_for_an_unknown_email_is_403_on_an_action(backend, mint):
    answer = backend.post("/dispatches", json={"workers": 3}, headers={"Authorization": f"Bearer {mint.token('x@example.com')}"})
    assert answer.status_code == 403


def test_me_says_who_is_signed_in_and_what_they_may_do(backend, mint):
    me = backend.get("/me", headers=mint.header("HND-02")).json()
    assert me == {"handler_id": "HND-02", "name": "Marcus Oyelaran", "role": "Claims Handler",
                  "email": EMAILS["HND-02"], "can_approve_over_limit": False}
    assert backend.get("/me").status_code == 401


def test_a_sign_off_records_the_signed_in_handler(backend, mint):
    backend.post("/dispatches", json={"workers": 5}, headers=mint.header("HND-01"))
    answer = backend.post("/payments/PAY-CLM-1003/approve", headers=mint.header("HND-01"))
    assert answer.status_code == 200 and answer.json()["approved_by"] == "HND-01"


def test_marcus_cannot_sign_off_over_the_limit(backend, mint):
    backend.post("/dispatches", json={"workers": 5}, headers=mint.header("HND-01"))
    answer = backend.post("/payments/PAY-CLM-1003/approve", headers=mint.header("HND-02"))
    assert answer.status_code == 403
    assert "PAY-CLM-1003" in {a["payment_id"] for a in backend.get("/approvals", headers=mint.header("HND-01")).json()}  # still held


def test_reads_need_a_sign_in(backend, mint):
    assert backend.get("/claims").status_code == 401 and backend.get("/approvals").status_code == 401
    assert backend.get("/claims", headers=mint.header()).status_code == 200
