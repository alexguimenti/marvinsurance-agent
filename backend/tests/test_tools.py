import json

import pytest

from app.db import init_schema
from agent import connection
from app.api import create_app
from app.config import Settings
from tests.fakes import MINT, api_client, connect
from app.reference import Reference
from app.seed import seed_claims
from agent.direct import DirectRoute
from agent.tools import build_tools

EXPECTED = {
    "get_claim", "get_policy", "get_claim_history", "get_plan",
    "list_sections", "read_section", "search_documents",
    "lookup_knowledge", "calculate_payout", "save_decision",
}


@pytest.fixture
def ref(data_dir):
    return Reference(data_dir)


@pytest.fixture
def conn(db_path, data_dir, ref):
    c = connect(db_path)
    init_schema(c.engine)
    seed_claims(c.engine, ref)
    settings = Settings(_env_file=None, agent_provider="scripted", db_path=db_path, data_dir=data_dir)
    with api_client(create_app(settings, jwks=MINT)) as backend, connection.use(backend):  # the tools reach the claims over HTTP
        yield c


def unwrap(text: str) -> dict:
    assert text.startswith("<claim_data>") and text.rstrip().endswith("</claim_data>")
    return json.loads(text.removeprefix("<claim_data>").rstrip().removesuffix("</claim_data>"))


class Bound:
    """A tool as the graph calls it, with what it takes from the state filled in."""

    def __init__(self, tool, claim_id):
        self.tool, self.claim_id = tool, claim_id

    def invoke(self, args):
        injected = {"claim_id": self.claim_id, "messages": []}
        return self.tool.invoke({**args, **{k: v for k, v in injected.items() if k in self.tool.args_schema.model_fields}})


def tools_for(conn, ref, claim_id="CLM-1001"):
    return {t.name: Bound(t, claim_id) for t in build_tools(DirectRoute())}


def test_tools_offer_lookups_not_whole_wording(conn, ref):
    assert set(tools_for(conn, ref)) == EXPECTED


def test_tools_expose_no_file_or_eval_access(conn, ref):
    for t in build_tools(DirectRoute()):
        offered_to_the_model = set(t.tool_call_schema.model_fields)
        assert not offered_to_the_model & {"path", "file", "filename", "claim_id", "messages", "proposal"}, t.name


def test_get_claim_returns_only_bound_claim(conn, ref):
    claim = unwrap(tools_for(conn, ref, "CLM-1002")["get_claim"].invoke({}))
    assert claim["claim_id"] == "CLM-1002"


def test_customer_history_has_no_contact_or_birth_data(conn, ref):
    raw = tools_for(conn, ref)["get_claim_history"].invoke({})
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
        "items": [{"description": "Hospital bill", "amount": 3800, "item_limit": None}],
        "third_party_paid": 0,
        "deductible": 50,
        "benefit_max": None,
    }))
    assert out["payout"] == 3750.0 and out["steps"] and out["needs_sign_off"] is True
