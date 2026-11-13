"""The Copilot's memory of each handler: looked up at the start of a turn in the signed-in handler's namespace, saved after it, off when
there is no Memory."""
import httpx
from langchain_core.messages import AIMessage, HumanMessage

from agent import connection
from agent.copilot import memory
from agent.copilot.payloads import Turn
from agent.copilot.turns import open_env, run_turn
from agent.settings import AgentSettings
from tests.fakes import FakeMemoryStore, script, tool_call
from tests.test_copilot_tools import App, DANA

THREAD = "conversation-" + "d" * 36
MARCUS = {"handler_id": "HND-02", "name": "Marcus Oyelaran", "role": "Handler", "can_approve_over_limit": False}
NOTES = [(("handlers", "HND-01", "preferences"), "Dana likes the amounts first, then the reasons, in short bullet points."),
         (("handlers", "HND-01", "facts"), "Dana is keeping an eye on CLM-1012."),
         (("handlers", "HND-02", "preferences"), "Marcus likes tables.")]


def turn(message="What needs me?", token="tok-dana"):
    return Turn(operation="turn", conversation_id=THREAD, turn_id=7, message=message, token=token)


def play(tmp_path, handler, store, model, message="What needs me?"):
    app = App({("GET", "/api/me"): (200, handler), ("GET", "/api/claims"): (200, [])})
    settings = AgentSettings(_env_file=None, db_path=str(tmp_path / "copilot.db"))
    with open_env(settings, model=model, store=store, context_tools=[]) as env, connection.use(httpx.Client(transport=httpx.MockTransport(app))):
        run_turn(env, turn(message))
    return app


def test_recall_searches_only_the_signed_in_handlers_namespace(tmp_path):
    store, model = FakeMemoryStore(NOTES), script(AIMessage("Amounts first."))
    app = play(tmp_path, DANA, store, model)
    assert store.searches == [(("handlers", "HND-01"), "What needs me?", 5)]
    system = model.seen[0][0].content
    assert "Dana likes the amounts first" in system and "keeping an eye on CLM-1012" in system and "Marcus" not in system
    card = app.sent("POST", "/api/copilot/turns/7/steps")[0]
    assert (card["kind"], card["tool"], card["main_input"]) == ("memory", "search_memory", "What needs me?")
    assert "Dana likes the amounts first" in app.sent("POST", "/api/copilot/steps/11")[0]["output"]


def test_danas_notes_are_never_used_for_marcus(tmp_path):
    store, model = FakeMemoryStore(NOTES), script(AIMessage("A table."))
    play(tmp_path, MARCUS, store, model)
    assert store.searches[0][0] == ("handlers", "HND-02")
    system = model.seen[0][0].content
    assert "Marcus likes tables" in system and "Dana" not in system


def test_a_handler_with_no_notes_gets_a_card_that_says_so(tmp_path):
    store = FakeMemoryStore([])
    app = play(tmp_path, DANA, store, script(AIMessage("Nothing yet.")))
    assert app.sent("POST", "/api/copilot/steps/11")[0]["output"] == memory.NOTHING


def test_a_turn_saves_the_message_and_the_answer_and_nothing_else(tmp_path):
    store = FakeMemoryStore()
    model = script(tool_call("list_claims", {}, "c1"), AIMessage("The queue is empty."))
    play(tmp_path, DANA, store, model, "I like short answers. What is in the queue?")
    assert [(ns, key) for ns, key, _ in store.puts] == [(("HND-01", THREAD), "turn-7-message"), (("HND-01", THREAD), "turn-7-answer")]
    message, answer = (value["message"] for _, _, value in store.puts)
    assert isinstance(message, HumanMessage) and message.content == "I like short answers. What is in the queue?"
    assert isinstance(answer, AIMessage) and answer.content == "The queue is empty."
    assert "tok-dana" not in repr(store.puts) and "[]" not in repr([v["message"].content for _, _, v in store.puts])  # no tool result, no sign-in


def test_with_memory_off_there_is_no_memory_card_and_the_turn_works(tmp_path):
    app = play(tmp_path, DANA, None, script(AIMessage("Hello.")))
    assert app.sent("POST", "/api/copilot/turns/7/steps") == []
    assert app.sent("POST", "/api/copilot/turns/7/end")[0]["answer"] == "Hello."


def test_a_memory_that_fails_does_not_fail_the_turn(tmp_path):
    store = FakeMemoryStore(fail=RuntimeError("memory is down"))
    app = play(tmp_path, DANA, store, script(AIMessage("Still here.")))
    assert app.sent("POST", "/api/copilot/steps/11")[0]["status"] == "failed"
    assert app.sent("POST", "/api/copilot/turns/7/end")[0]["answer"] == "Still here."
    assert app.sent("POST", "/api/copilot/turns/7/fail") == []


def test_the_store_is_opened_from_the_setting_each_time(monkeypatch):
    monkeypatch.delenv("UNDERCOVER_COPILOT_MEMORY_ID", raising=False)
    assert memory.open_copilot_store() is None
    monkeypatch.setenv("UNDERCOVER_COPILOT_MEMORY_ID", "undercover_copilot_memory-abc")
    monkeypatch.setattr(memory, "AgentCoreMemoryStore", lambda memory_id, region_name: (memory_id, region_name))
    monkeypatch.setattr(memory.boto3, "Session", lambda: type("S", (), {"region_name": "us-east-1"})())
    assert memory.open_copilot_store() == ("undercover_copilot_memory-abc", "us-east-1")
