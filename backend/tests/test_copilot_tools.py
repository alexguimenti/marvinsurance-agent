"""The Copilot's tools and their cards: each call carries the handler's sign-in, is recorded as a step, and tells the model a refusal."""
import json

import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode

from agent import connection
from agent.copilot import context_tools
from agent.copilot.asker import Asker
from agent.copilot.cards import carded, main_input
from agent.copilot.tools import build_tools
from tests.fakes import TEST_KEY

DANA = {"handler_id": "HND-01", "name": "Dana Whitfield", "role": "Senior handler", "can_approve_over_limit": True}
SETTINGS = {"settings": [{"name": "approval_limit", "value": 1000}, {"name": "objectives", "value": {"accuracy": 0.6, "grade": 1.0}},
                         {"name": "agents_at_once", "value": 25}, {"name": "noise", "value": 0}]}


class App:
    """The app, as a stand-in: writes each request down, answers from `answers` ((method, path) to a status and JSON), and gives each
    step an id."""

    def __init__(self, answers=None, down=()):
        self.requests, self.answers, self.down = [], answers or {}, set(down)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path in self.down:
            raise httpx.ConnectError("the app is down")
        if request.url.path.endswith("/steps"):
            return httpx.Response(200, json={"step_id": 11})
        status, body = self.answers.get((request.method, request.url.path), (200, {}))
        return httpx.Response(status, json=body)

    def sent(self, method: str, path: str) -> list[dict]:
        return [json.loads(r.content) if r.content else None for r in self.requests if (r.method, r.url.path) == (method, path)]


def run_tool(app: App, name: str, args: dict, context_layer=()):
    """Run one tool call through a ToolNode with the cards hook, as the Copilot's graph does. Returns the ToolMessage."""
    node = ToolNode(build_tools(list(context_layer)), wrap_tool_call=carded)
    builder = StateGraph(MessagesState, context_schema=Asker)
    builder.add_node("tools", node)
    builder.add_edge(START, "tools")
    builder.add_edge("tools", END)
    graph = builder.compile()
    call = AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": "call-1"}])
    asker = Asker("tok-dana", DANA, "conversation-x", 7)
    with connection.use(httpx.Client(transport=httpx.MockTransport(app))):
        out = graph.invoke({"messages": [HumanMessage("hi"), call]}, context=asker)
    return out["messages"][-1]


def test_every_tool_call_carries_the_handlers_token_and_never_the_key(monkeypatch):
    monkeypatch.setenv("UNDERCOVER_API_KEY", TEST_KEY)
    app = App({("GET", "/api/settings"): (200, SETTINGS), ("POST", "/api/dispatches"): (202, {"status": "started", "run_id": 3})})
    for name, args in (("list_claims", {}), ("get_claim", {"claim_id": "CLM-1012"}), ("find_customers", {"name": "Maya"}),
                       ("start_dispatch", {"workers": 4}), ("get_settings", {})):
        run_tool(app, name, args)
    assert len(app.requests) > 10  # each call and its two reports
    assert all(r.headers["authorization"] == "Bearer tok-dana" and "x-api-key" not in r.headers for r in app.requests)


def test_a_lookup_records_a_working_card_then_done_with_its_seconds():
    app = App({("GET", "/api/claims/CLM-1012"): (200, {"status": "decided"})})
    message = run_tool(app, "get_claim", {"claim_id": "CLM-1012"})
    assert json.loads(message.content) == {"status": "decided"}
    begun = app.sent("POST", "/api/copilot/turns/7/steps")[0]
    assert begun == {"kind": "claims", "tool": "get_claim", "main_input": "CLM-1012", "input": {"claim_id": "CLM-1012"}}
    finished = app.sent("POST", "/api/copilot/steps/11")[0]
    assert finished["status"] == "done" and json.loads(finished["output"]) == {"status": "decided"}
    order = [(r.method, r.url.path) for r in app.requests]
    assert order == [("POST", "/api/copilot/turns/7/steps"), ("GET", "/api/claims/CLM-1012"), ("POST", "/api/copilot/steps/11")]


def test_a_refusal_is_told_to_the_model_and_carded_refused():
    app = App({("GET", "/api/settings"): (200, SETTINGS), ("POST", "/api/dispatches"): (409, {"detail": "a dispatch is already running"})})
    message = run_tool(app, "start_dispatch", {"workers": 3})
    assert message.content == "Refused: a dispatch is already running"
    finished = app.sent("POST", "/api/copilot/steps/11")[0]
    assert finished["status"] == "refused" and finished["reason"] == "a dispatch is already running"
    assert app.sent("POST", "/api/copilot/turns/7/steps")[0]["kind"] == "action"  # a start stands out from the lookups


def test_an_unknown_claim_is_a_refusal_too():
    app = App({("GET", "/api/claims/CLM-0000"): (404, {"detail": "unknown claim CLM-0000"})})
    assert run_tool(app, "get_claim", {"claim_id": "CLM-0000"}).content == "Refused: unknown claim CLM-0000"


def test_the_app_not_answering_fails_the_card_and_the_turn():
    app = App(down={"/api/claims"})
    with pytest.raises(httpx.ConnectError):
        run_tool(app, "list_claims", {})
    assert app.sent("POST", "/api/copilot/steps/11")[0]["status"] == "failed"


def test_start_dispatch_uses_agents_at_once_when_not_given():
    app = App({("GET", "/api/settings"): (200, SETTINGS), ("POST", "/api/dispatches"): (202, {"status": "started", "run_id": 3})})
    message = run_tool(app, "start_dispatch", {})
    assert app.sent("POST", "/api/dispatches") == [{"workers": 25}]
    assert json.loads(message.content)["run_id"] == 3


def test_start_evaluation_sends_the_set_tiny_and_agents():
    app = App({("GET", "/api/settings"): (200, SETTINGS), ("POST", "/api/evaluations"): (202, {"status": "started", "run_id": 4})})
    run_tool(app, "start_evaluation", {"eval_set": "held_out"})
    run_tool(app, "start_evaluation", {"eval_set": "evaluation", "tiny": True, "workers": 2})
    assert app.sent("POST", "/api/evaluations") == [{"eval_set": "held_out", "tiny": False, "workers": 25},
                                                    {"eval_set": "evaluation", "tiny": True, "workers": 2}]


def test_send_back_posts_the_note():
    app = App({("POST", "/api/claims/CLM-1003/send-back"): (200, {"status": "sent_back"})})
    run_tool(app, "send_back", {"claim_id": "CLM-1003", "note": "look at the baggage receipt again"})
    assert app.sent("POST", "/api/claims/CLM-1003/send-back") == [{"note": "look at the baggage receipt again"}]


def test_get_evaluation_never_returns_the_handlers_decisions():
    report = {"run": {"run_id": 5}, "measures": {"accuracy": 0.8}, "claims": [{"claim_id": "CLM-1", "handler": {"decision": "approve"}}]}
    app = App({("GET", "/api/evaluations/5"): (200, report)})
    message = run_tool(app, "get_evaluation", {"run_id": 5})
    assert json.loads(message.content) == {"run": {"run_id": 5}, "measures": {"accuracy": 0.8}}
    assert "handler" not in message.content
    assert "handler" not in app.sent("POST", "/api/copilot/steps/11")[0]["output"]


def test_get_settings_gives_only_what_the_copilot_needs():
    app = App({("GET", "/api/settings"): (200, SETTINGS)})
    message = run_tool(app, "get_settings", {})
    assert json.loads(message.content) == {"approval_limit": 1000, "objectives": {"accuracy": 0.6, "grade": 1.0}, "agents_at_once": 25}


def test_main_input_is_the_one_input_a_card_shows():
    assert main_input("get_claim", {"claim_id": "CLM-1012"}) == "CLM-1012"
    assert main_input("start_dispatch", {"workers": 10}) == "10 agents"
    assert main_input("start_dispatch", {"workers": None}) == "default agents"
    assert main_input("start_evaluation", {"eval_set": "held_out", "tiny": False}) == "held_out"
    assert main_input("list_claims", {}) == ""


class FakeMcpTool:
    """What `MultiServerMCPClient.get_tools()` gives for one tool: async, under the name the Gateway gave it."""

    def __init__(self, name, answer):
        self.name, self.description, self.answer = name, f"the {name} tool", answer
        self.args_schema = {"type": "object", "properties": {"term": {"type": "string"}}, "required": ["term"]}
        self.calls = []

    async def ainvoke(self, args):
        self.calls.append(args)
        return self.answer


class FakeClient:
    def __init__(self, *tools):
        self.tools = list(tools)

    async def get_tools(self):
        return self.tools


def test_context_tools_on_the_gateway_keep_only_the_context_target_and_show_own_names():
    client = FakeClient(FakeMcpTool("undercover-context___lookup_term", "{}"), FakeMcpTool("undercover-context___read_section", "{}"),
                        FakeMcpTool("undercover-app___get_claim", "{}"), FakeMcpTool("undercover-app___pay_claim", "{}"))
    found = context_tools.load_context_tools({"UNDERCOVER_GATEWAY_URL": "https://gateway.example/mcp"}, client)
    assert [t.name for t in found] == ["lookup_term", "read_section"]


def test_the_connection_is_signed_on_the_gateway_and_plain_on_the_direct_route():
    signed = context_tools.connection({"UNDERCOVER_GATEWAY_URL": "https://gateway.example/mcp"}, make_auth=lambda: "SIGNED")
    assert signed == {"undercover": {"transport": "streamable_http", "url": "https://gateway.example/mcp", "auth": "SIGNED"}}
    direct = context_tools.connection({"UNDERCOVER_CONTEXT": "http://127.0.0.1:9/mcp"})
    assert direct == {"undercover": {"transport": "streamable_http", "url": "http://127.0.0.1:9/mcp"}}


def test_a_context_card_carries_its_file_and_owner():
    answer = json.dumps({"answer": "A deductible is ...", "sources": [{"file": "glossary.yaml", "system": "Docs", "owner": "Claims Ops", "reviewed": "2026-01-05"}]})
    tool = FakeMcpTool("undercover-context___lookup_term", answer)
    found = context_tools.load_context_tools({}, FakeClient(tool))
    app = App()
    message = run_tool(app, "lookup_term", {"term": "deductible"}, found)
    assert json.loads(message.content)["answer"].startswith("A deductible")
    assert tool.calls == [{"term": "deductible"}]
    begun = app.sent("POST", "/api/copilot/turns/7/steps")[0]
    assert (begun["kind"], begun["tool"], begun["main_input"]) == ("context", "lookup_term", "deductible")
    assert app.sent("POST", "/api/copilot/steps/11")[0]["sources"][0]["owner"] == "Claims Ops"


def test_context_tools_over_the_direct_route(context_server_url):
    """The real adapter against the real context layer: the tool works, on a thread of its own, and answers with its sources."""
    found = context_tools.load_context_tools({"UNDERCOVER_CONTEXT": context_server_url})
    assert sorted(t.name for t in found) == sorted(["lookup_term", "describe_fields", "get_plan", "get_claim_rules", "search_reference_data",
                                                    "search_documents", "read_section"])
    lookup = next(t for t in found if t.name == "lookup_term")
    answer = json.loads(lookup.invoke({"term": "deductible"}))
    assert answer["sources"] and "file" in answer["sources"][0]
