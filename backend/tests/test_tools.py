import json

import pytest

from app.db import init_schema
from agent import connection
from app.api import create_app
from app.config import Settings
from tests.fakes import MINT, StandInGateway, api_client, connect, direct_route
from app.reference import Reference
from app.seed import seed_claims
from agent.gateway import GatewayRoute
from agent.mcp_bridge import ToolRefused
from agent.tools import build_tools

EXPECTED = {
    "get_claim", "get_policy", "get_claim_history", "get_plan", "lookup_term", "describe_fields", "get_claim_rules",
    "search_reference_data", "search_documents", "read_section", "calculate_payout", "record_decision",
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
    return {t.name: Bound(t, claim_id) for t in build_tools(direct_route())}


def test_tools_offer_lookups_not_whole_wording(conn, ref):
    assert set(tools_for(conn, ref)) == EXPECTED


def test_tools_expose_no_file_or_eval_access(conn, ref):
    for t in build_tools(direct_route()):
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


def test_get_plan_gives_the_plan_with_its_add_ons_and_where_it_came_from(conn, ref):
    answer = json.loads(tools_for(conn, ref)["get_plan"].invoke({"plan": "premium"}))
    assert answer["answer"]["plan"]["plan"] == "premium" and isinstance(answer["answer"]["add_ons"], list)
    assert answer["sources"][0]["file"] == "plans.yaml" and answer["sources"][0]["owner"] == "Product"


def test_read_section_returns_text_and_its_source(conn, ref):
    out = tools_for(conn, ref)["read_section"].invoke({"document": "claims_manual", "section": 10})
    assert "$35" in out and "[From claims_manual.md: Claims operations, Head of claims operations, reviewed 2026-09-01]" in out


@pytest.mark.parametrize("document,section", [
    ("claims_manual", 99),
    ("evals", 1),
    ("../evals/decisions.json", 1),
])
def test_bad_lookups_are_refusals_the_model_is_shown(conn, ref, document, section):
    with pytest.raises(ToolRefused, match="claims_manual has no section 99|unknown document"):
        tools_for(conn, ref)["read_section"].invoke({"document": document, "section": section})


def test_the_context_tools_answer_with_their_files(conn, ref):
    tools = tools_for(conn, ref)
    term = json.loads(tools["lookup_term"].invoke({"term": "carrier"}))
    assert term["answer"]["term"] == "Carrier" and term["sources"][0]["file"] == "glossary.yaml"
    assert json.loads(tools["describe_fields"].invoke({"record": "policy"}))["answer"]["record"] == "policy"
    assert json.loads(tools["get_claim_rules"].invoke({}))["answer"]["rules"]
    events = json.loads(tools["search_reference_data"].invoke({"query": "lisbon strike"}))["answer"]
    assert events and all(f["topic"] == "events" for f in events)
    hits = json.loads(tools["search_documents"].invoke({"query": "meals without receipts"}))["answer"]
    assert hits[0]["section"] == 10 and hits[0]["file"] == "claims_manual.md"


def test_an_unknown_record_or_plan_is_a_refusal(conn, ref):
    tools = tools_for(conn, ref)
    with pytest.raises(ToolRefused, match="unknown record"):
        tools["describe_fields"].invoke({"record": "customer"})
    with pytest.raises(ToolRefused, match="unknown plan"):
        tools["get_plan"].invoke({"plan": "gold"})


def test_a_lookup_leaves_its_sources_as_the_tools_artifact(conn, ref):
    message = next(t for t in build_tools(direct_route()) if t.name == "lookup_term").invoke(
        {"type": "tool_call", "name": "lookup_term", "id": "t1", "args": {"term": "Carrier"}})
    assert message.artifact["sources"][0]["file"] == "glossary.yaml" and message.artifact["found"].startswith("An airline")


def test_on_the_gateway_route_context_tools_are_called_undercover_context_name(conn, ref):
    stand_in = StandInGateway()
    tools = {t.name: Bound(t, "CLM-1001") for t in build_tools(GatewayRoute("https://gateway.example/mcp", None, call=stand_in))}
    tools["lookup_term"].invoke({"term": "carrier"})
    tools["get_claim"].invoke({})
    assert stand_in.names == ["undercover-context___lookup_term", "undercover-app___get_claim"]


def test_calculate_payout_tool_returns_steps(conn, ref):
    out = json.loads(tools_for(conn, ref)["calculate_payout"].invoke({
        "items": [{"description": "Hospital bill", "amount": 3800, "item_limit": None}],
        "third_party_paid": 0,
        "deductible": 50,
        "benefit_max": None,
    }))
    assert out["payout"] == 3750.0 and out["steps"] and out["needs_sign_off"] is True
