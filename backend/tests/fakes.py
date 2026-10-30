from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage


class FakeToolModel(GenericFakeChatModel):
    """Plays back scripted AI messages and records everything it is shown."""

    seen: list = []

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, *args, **kwargs):
        self.seen.append(list(messages))
        return super()._generate(messages, *args, **kwargs)


def script(*messages: AIMessage) -> FakeToolModel:
    return FakeToolModel(messages=iter(messages), seen=[])


def tool_call(name: str, args: dict, call_id: str) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


STUB_USAGE = {"calls": 2, "input_tokens": 100, "output_tokens": 10, "reasoning_tokens": 5, "cost_usd": 0.001}


class StubAgent:
    """A claim agent with no model. Each claim plays its outcomes in order, one per call:
    a Decision, an Exception to raise, or a function (conn, claim_id) that returns or raises.
    Claims with no outcomes left get `default`, or an approval of $100."""

    model_id = "stub/model"

    def __init__(self, outcomes: dict | None = None, conn=None, default=None):
        self.outcomes = {k: list(v) for k, v in (outcomes or {}).items()}
        self.conn, self.default = conn, default
        self.asked: list[str] = []
        self.usage = None

    def decide(self, claim_id, on_event):
        from app.agent import Decision

        self.asked.append(claim_id)
        self.usage = dict(STUB_USAGE)
        on_event("tool_call", {"name": "get_claim", "args": {}})
        on_event("tool_result", {"name": "get_claim", "content": "x"})
        queue = self.outcomes.get(claim_id) or []
        outcome = queue.pop(0) if queue else self.default
        if outcome is None:
            outcome = Decision(decision="approve", payout=100.0,
                               rationale="The loss is covered. Section 10 of the wording sets the payment.")
        if callable(outcome) and not isinstance(outcome, Decision):
            outcome = outcome(self.conn, claim_id)
        if isinstance(outcome, Exception):
            raise outcome
        on_event("decision", outcome.model_dump())
        return outcome
