"""The Copilot's graph: the loop between the model and its tools, the 25-call limit, and what is and is not saved."""
import itertools
import sqlite3

import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.checkpoint.sqlite import SqliteSaver

from agent import connection
from agent.copilot.asker import Asker
from agent.copilot.graph import MAX_MODEL_CALLS, TooManyModelCalls, build_copilot_graph, load_instructions
from agent.copilot.scripted import ANSWER, ScriptedCopilotModel
from agent.copilot.tools import build_tools
from tests.fakes import FakeToolModel, script, tool_call
from tests.test_copilot_tools import App, DANA

THREAD = "conversation-" + "b" * 36


def asker(turn_id=7):
    return Asker("tok-dana-secret", DANA, THREAD, turn_id)


def run_turn(model, app, message="What needs me?", saver=None, state=None):
    graph = build_copilot_graph(model, saver, build_tools([]), None, "INSTRUCTIONS")
    config = {"configurable": {"thread_id": THREAD}, "recursion_limit": 100}
    with connection.use(httpx.Client(transport=httpx.MockTransport(app))):
        return graph.invoke({"messages": [HumanMessage(message)], "model_calls": 0, "cost_usd": 0.0, **(state or {})}, config, context=asker())


def priced(message: AIMessage, cost: float) -> AIMessage:
    message.response_metadata = {"token_usage": {"cost": cost}}
    return message


def test_the_loop_calls_tools_until_the_model_answers():
    app = App({("GET", "/api/claims"): (200, [{"claim_id": "CLM-1001"}])})
    model = script(priced(tool_call("list_claims", {}, "c1"), 0.01), priced(AIMessage("One claim is waiting."), 0.02))
    out = run_turn(model, app)
    assert out["messages"][-1].content == "One claim is waiting."
    assert (out["model_calls"], out["cost_usd"]) == (2, 0.03)
    assert [r.url.path for r in app.requests if r.url.path == "/api/claims"] == ["/api/claims"]


def test_more_than_25_model_calls_fail_the_turn():
    assert MAX_MODEL_CALLS == 25
    app = App({("GET", "/api/claims"): (200, [])})
    # a new message each time: the same id would replace the last
    model = FakeToolModel(messages=(tool_call("list_claims", {}, f"again-{i}") for i in itertools.count()), seen=[])
    with pytest.raises(TooManyModelCalls):
        run_turn(model, app)
    assert len(app.sent("GET", "/api/claims")) == 25


def test_the_token_is_in_no_checkpoint(tmp_path):
    connection_ = sqlite3.connect(tmp_path / "copilot.db", check_same_thread=False)
    saver = SqliteSaver(connection_)
    app = App({("GET", "/api/claims"): (200, [{"claim_id": "CLM-1001"}])})
    run_turn(script(tool_call("list_claims", {}, "c1"), AIMessage("Done.")), app, saver=saver)
    dumped = []
    for table in [r[0] for r in connection_.execute("SELECT name FROM sqlite_master WHERE type='table'")]:
        dumped += [repr(row) for row in connection_.execute(f"SELECT * FROM {table}")]
    assert dumped and not any("tok-dana-secret" in row for row in dumped)
    assert saver.get_tuple({"configurable": {"thread_id": THREAD}}) is not None  # and the state was saved


def test_the_system_prompt_names_the_handler_and_is_not_saved(tmp_path):
    saver = SqliteSaver(sqlite3.connect(tmp_path / "copilot.db", check_same_thread=False))
    model = script(AIMessage("Hello."))
    out = run_turn(model, App(), saver=saver)
    system = model.seen[0][0]
    assert isinstance(system, SystemMessage) and "INSTRUCTIONS" in system.content
    assert "Dana Whitfield" in system.content and "HND-01" in system.content and "may sign off" in system.content
    assert not any(isinstance(m, SystemMessage) for m in out["messages"])


def test_the_prompt_says_a_handler_may_not_sign_off_over_the_limit():
    model = script(AIMessage("Hello."))
    graph = build_copilot_graph(model, None, build_tools([]), None, "INSTRUCTIONS")
    marcus = Asker("tok-m", {"handler_id": "HND-02", "name": "Marcus Oyelaran", "role": "Handler", "can_approve_over_limit": False}, THREAD, 1)
    graph.invoke({"messages": [HumanMessage("hi")], "model_calls": 0, "cost_usd": 0.0}, {"configurable": {"thread_id": "t"}}, context=marcus)
    assert "may not sign off" in model.seen[0][0].content


def test_the_instructions_exist_and_say_who_the_copilot_is():
    text = load_instructions()
    assert "UnderCover Copilot" in text and "70%" in text and "US dollars" in text


def test_the_scripted_model_plays_the_e2e_turn():
    app = App({("GET", "/api/claims"): (200, []), ("POST", "/api/dispatches"): (202, {"status": "started", "run_id": 3}),
               ("GET", "/api/claims/CLM-0000"): (404, {"detail": "unknown claim CLM-0000"})})
    out = run_turn(ScriptedCopilotModel(), app, "Work the queue")
    assert out["messages"][-1].content == ANSWER
    statuses = [b["status"] for b in app.sent("POST", "/api/copilot/steps/11")]
    assert statuses == ["done", "done", "refused"]
