import json

import pytest

from app.db import connect, init_schema
from app.reference import Reference
from app.seed import seed_claims
from app.tools import build_tools

EXPECTED = {
    "get_claim", "get_policy", "get_customer_history", "get_plan",
    "list_sections", "read_section", "search_documents",
    "lookup_knowledge", "calculate_payout", "request_payment",
}


@pytest.fixture
def ref(data_dir):
    return Reference(data_dir)


@pytest.fixture
def conn(db_path, ref):
    c = connect(db_path)
    init_schema(c)
    seed_claims(c, ref)
    return c


def unwrap(text: str) -> dict:
    assert text.startswith("<claim_data>") and text.rstrip().endswith("</claim_data>")
    return json.loads(text.removeprefix("<claim_data>").rstrip().removesuffix("</claim_data>"))


def tools_for(conn, ref, claim_id="CLM-1001"):
    return {t.name: t for t in build_tools(conn, ref, claim_id)}


def test_tools_offer_lookups_not_whole_wording(conn, ref):
    assert set(tools_for(conn, ref)) == EXPECTED


def test_tools_expose_no_file_or_eval_access(conn, ref):
    for t in build_tools(conn, ref, "CLM-1001"):
        params = set(t.args)
        assert not params & {"path", "file", "filename", "claim_id"}, t.name


def test_get_claim_returns_only_bound_claim(conn, ref):
    claim = unwrap(tools_for(conn, ref, "CLM-1002")["get_claim"].invoke({}))
    assert claim["claim_id"] == "CLM-1002"


def test_customer_history_has_no_contact_or_birth_data(conn, ref):
    raw = tools_for(conn, ref)["get_customer_history"].invoke({})
    data = json.loads(raw)
    assert data["name"] == "Maya Patel"
    assert "previous_claims" in data and "customer_since" in data
    for key in ("email", "phone", "address", "date_of_birth"):
        assert key not in data
        assert key not in raw


def test_get_plan_includes_policy_add_ons(conn, ref):
    policy = ref.policy(unwrap(tools_for(conn, ref)["get_claim"].invoke({}))["policy_id"])
    plan = json.loads(tools_for(conn, ref)["get_plan"].invoke({}))
    assert plan["plan"]["plan"] == policy["plan"]
    assert isinstance(plan["add_ons"], list)


def test_read_section_returns_text(conn, ref):
    assert "$35" in tools_for(conn, ref)["read_section"].invoke({"document": "claims_manual", "section": 10})


@pytest.mark.parametrize("document,section", [
    ("claims_manual", 99),
    ("evals", 1),
    ("../evals/decisions.json", 1),
])
def test_bad_lookups_return_error_strings(conn, ref, document, section):
    out = tools_for(conn, ref)["read_section"].invoke({"document": document, "section": section})
    assert out.startswith("Error:")
    assert "policy_wording" in out and "claims_manual" in out


def test_list_sections_of_unknown_document_is_an_error(conn, ref):
    assert tools_for(conn, ref)["list_sections"].invoke({"document": "evals"}).startswith("Error:")


def test_list_sections_gives_numbers_and_titles(conn, ref):
    sections = json.loads(tools_for(conn, ref)["list_sections"].invoke({"document": "policy_wording"}))
    assert sections[2] == {"section": 3, "title": "Trip Delay"}


def test_search_documents_returns_json_hits(conn, ref):
    hits = json.loads(tools_for(conn, ref)["search_documents"].invoke({"query": "meals without receipts"}))
    assert hits[0]["section"] == 10


def test_lookup_knowledge_by_topic(conn, ref):
    facts = json.loads(tools_for(conn, ref)["lookup_knowledge"].invoke({"topic": "events"}))
    assert facts and all(f["topic"] == "events" for f in facts)


def test_calculate_payout_tool_returns_steps(conn, ref):
    out = json.loads(tools_for(conn, ref)["calculate_payout"].invoke({
        "items": [{"description": "Hospital bill", "amount": 3800}],
        "deductible": 50,
    }))
    assert out["payout"] == 3750.0 and out["steps"]


def test_request_payment_tool_over_limit_returns_held(conn, ref):
    out = json.loads(tools_for(conn, ref)["request_payment"].invoke({"amount": 2500.0}))
    assert out["status"] == "held"
