"""The two routes to the model's tools: the app direct with its key, or the Gateway's MCP tools signed with SigV4."""
import json

import httpx
import pytest

from agent import connection
from agent.direct import DirectRoute
from agent.gateway import GatewayRefused, GatewayRoute, SigV4Auth
from agent.mcp_bridge import ToolRefused
from agent.route import make_route
from tests.fakes import StandInGateway, running_backend

CLAIM = "CLM-1001"


@pytest.fixture
def backend(db_path, data_dir):
    with running_backend(db_path, data_dir):
        yield


def gateway_route(**kwargs) -> tuple[GatewayRoute, StandInGateway]:
    stand_in = StandInGateway(**kwargs)
    return GatewayRoute("https://gateway.example/mcp", auth=None, call=stand_in), stand_in


@pytest.mark.parametrize("operation", ["get_claim", "get_policy", "get_claim_history"])
def test_both_routes_return_the_same_answer_for_each_app_tool(backend, operation):
    gateway, _ = gateway_route()
    assert gateway.app(operation, {"claim_id": CLAIM}) == DirectRoute().app(operation, {"claim_id": CLAIM})


def test_the_gateway_route_calls_tools_by_the_gateways_names(backend):
    gateway, stand_in = gateway_route()
    gateway.app("get_claim", {"claim_id": CLAIM})
    gateway.app("get_policy", {"claim_id": CLAIM})
    assert stand_in.names == ["undercover-app___get_claim", "undercover-app___get_policy"]


def test_an_app_refusal_on_each_route_has_the_apps_reason(backend):
    gateway, _ = gateway_route()
    with pytest.raises(LookupError, match="unknown claim CLM-0000"):
        DirectRoute().app("get_claim", {"claim_id": "CLM-0000"})
    with pytest.raises(ToolRefused, match="unknown claim CLM-0000"):
        gateway.app("get_claim", {"claim_id": "CLM-0000"})


def test_a_refusal_by_the_gateways_policy_is_gateway_refused(backend):
    gateway, _ = gateway_route()
    with pytest.raises(GatewayRefused, match="Tool Execution Denied"):
        gateway.app("pay_claim", {"claim_id": CLAIM, "amount": 4800.0})


def test_a_tool_error_whose_text_is_the_apps_json_gives_its_detail():
    def refusing(url, name, args, auth=None):
        raise ToolRefused(json.dumps({"detail": "unknown claim CLM-0000"}))

    with pytest.raises(ToolRefused) as raised:
        GatewayRoute("https://gateway.example/mcp", auth=None, call=refusing).app("get_claim", {"claim_id": "CLM-0000"})
    assert str(raised.value) == "unknown claim CLM-0000"


def test_the_shown_name_is_the_gateways_on_that_route_and_the_tools_own_otherwise():
    gateway, _ = gateway_route()
    assert gateway.shown_name("get_claim") == "undercover-app___get_claim"
    assert gateway.shown_name("lookup_term") == "undercover-context___lookup_term"
    assert DirectRoute().shown_name("get_claim") == "get_claim"


def test_make_route_reads_the_environment_when_it_is_called(monkeypatch):
    assert make_route().kind == "direct"
    monkeypatch.setenv("UNDERCOVER_GATEWAY_URL", "https://gateway.example/mcp")
    monkeypatch.setattr("agent.route.SigV4Auth", lambda session: "signed")
    monkeypatch.setattr("agent.route.boto3.Session", lambda: object())
    route = make_route()
    assert route.kind == "gateway" and route.url == "https://gateway.example/mcp"


def test_the_direct_context_url_defaults_and_follows_the_environment(monkeypatch):
    monkeypatch.delenv("UNDERCOVER_CONTEXT")
    assert make_route().context_url == "http://127.0.0.1:8006/mcp"
    monkeypatch.setenv("UNDERCOVER_CONTEXT", "http://elsewhere.example/mcp")
    assert make_route().context_url == "http://elsewhere.example/mcp"


def test_sigv4_signs_for_bedrock_agentcore():
    class Credentials:
        def get_frozen_credentials(self):
            from botocore.credentials import ReadOnlyCredentials

            return ReadOnlyCredentials("AKIAEXAMPLE", "secret", None)

    class Session:
        region_name = "us-east-1"

        def get_credentials(self):
            return Credentials()

    request = httpx.Request("POST", "https://gateway.example/mcp", json={"a": 1})
    signed = next(SigV4Auth(Session()).auth_flow(request))
    assert "/us-east-1/bedrock-agentcore/aws4_request" in signed.headers["Authorization"]
    assert "X-Amz-Date" in signed.headers


def test_an_mcp_error_is_never_swallowed_into_an_answer(backend):
    gateway, _ = gateway_route(deny=["undercover-app___get_claim"])
    with pytest.raises(GatewayRefused):
        gateway.app("get_claim", {"claim_id": CLAIM})
