import re
import threading
import time
from contextlib import contextmanager

from langchain_core.exceptions import ModelRateLimitError, ModelTimeoutError
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from app.config import Settings
from app.db import init_schema, make_engine
from app.reference import Reference
from app.seed import seed_claims


class _Row:
    """A row readable by column name or by position."""

    def __init__(self, mapping):
        self._values = dict(mapping)

    def __getitem__(self, key):
        return list(self._values.values())[key] if isinstance(key, int) else self._values[key]

    def __iter__(self):
        return iter(self._values.values())

    def keys(self):
        return self._values.keys()


class _Rows(list):
    def fetchone(self):
        return self[0] if self else None

    def fetchall(self):
        return list(self)


class RawDb:
    """Lets a test look at or arrange the tables in plain SQL. Operations take `.engine`."""

    def __init__(self, engine):
        self.engine = engine

    def execute(self, sql: str, params=()):
        with self.engine.begin() as conn:
            result = conn.exec_driver_sql(sql, tuple(params))
            return _Rows(_Row(r) for r in result.mappings().all()) if result.returns_rows else _Rows()

    def commit(self):
        pass

    def close(self):
        self.engine.dispose()


def connect(db_path: str) -> RawDb:
    return RawDb(make_engine(Settings(_env_file=None, db_path=db_path), max_connections=4))


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


GOOD = "The loss is covered. Section 10 of the wording sets the payment."


def approve(payout: float, rationale: str = GOOD) -> dict:
    return {"decision": "approve", "payout": payout, "rationale": rationale, "citations": ["Policy wording, section 10"]}


def decline(rationale: str = "The claim is excluded. Section 9 of the wording does not pay it.") -> dict:
    return {"decision": "decline", "payout": 0, "rationale": rationale, "citations": ["Policy wording, section 9"]}


def refer() -> dict:
    return {"decision": "refer", "payout": 0, "citations": ["Policy wording, section 11"],
            "rationale": "A document is missing. Section 11 of the wording says the claim waits."}


class FakeClock:
    """A monotonic clock that moves on `step` seconds at every reading, and `jump`s when a test says so."""

    def __init__(self, step: float = 1.0):
        self.now, self.step = 0.0, step

    def __call__(self) -> float:
        reading = self.now
        self.now += self.step
        return reading

    def jump(self, seconds: float) -> None:
        self.now += seconds


class Stopped(BaseException):
    """Stands in for a kill or Ctrl-C: like them, it is no ordinary error."""


_STUB_LOCK = threading.RLock()


def _with_calculations(steps: list) -> list:
    """An approval a stub plays calculates its payout first, as the section 14 check needs: one calculate_payout call
    for the payout, unless the script already calls calculate_payout itself."""
    calls_it = any(isinstance(s, AIMessage) and any(c["name"] == "calculate_payout" for c in s.tool_calls) for s in steps)
    out = []
    for step in steps:
        if isinstance(step, dict) and step.get("decision") == "approve" and not calls_it:
            out.append(tool_call("calculate_payout", {"items": [{"description": "Payout", "amount": step["payout"], "item_limit": 0}],
                                                      "third_party_paid": 0, "deductible": 0, "benefit_max": 0}, f"calc-{id(step)}"))
        out.append(step)
    return out


class StubModel(BaseChatModel):
    """A claim model with no network. Each claim plays its outcomes in order, one per attempt; an outcome is one
    step or a list of steps, one per model call. A step is a decision (dict), an AIMessage, or an exception to raise.
    A Stopped, a rate limit or a timeout goes off once. Claims with no outcomes left get `default`, or an approval of $100."""

    outcomes: dict = {}
    default: object = None
    asked: list = []  # the claim of every attempt, in order
    calls: int = 0
    current: dict = {}
    shown: list = []
    bound_tools: list = []

    @property
    def _llm_type(self) -> str:
        return "stub"

    def bind_tools(self, tools, **kwargs):
        self.bound_tools = [t.name for t in tools]
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        # Claim agents call one stub from many threads, and its bookkeeping is shared: one at a time.
        with _STUB_LOCK:
            return self._step(messages)

    def _step(self, messages) -> ChatResult:
        self.calls += 1
        self.shown.append(list(messages))
        claim_id = re.search(r"Decide claim (\S+)\.", next(m.content for m in messages if m.type == "human"))[1]
        turn = sum(1 for m in messages if m.type == "ai" and not any(c["id"].startswith("prefetch-") for c in m.tool_calls))
        if turn == 0 and claim_id not in self.current:
            self.asked.append(claim_id)
            queue = self.outcomes.get(claim_id) or []
            outcome = queue.pop(0) if queue else (self.default if self.default is not None else approve(100.0))
            self.current[claim_id] = _with_calculations(list(outcome) if isinstance(outcome, list) else [outcome])
        steps = self.current[claim_id]
        step = steps[min(turn, len(steps) - 1)]
        if isinstance(step, (Stopped, ModelRateLimitError, ModelTimeoutError)):  # goes off once; the claim carries on
            steps.remove(step)
            raise step
        if isinstance(step, Exception):
            del self.current[claim_id]
            raise step
        if isinstance(step, dict):
            del self.current[claim_id]
            step = tool_call("record_decision", step, f"save-{self.calls}")
        step = step.model_copy(update={
            "usage_metadata": {"input_tokens": 50, "output_tokens": 5, "total_tokens": 55,
                               "output_token_details": {"reasoning": 2}},
            "response_metadata": {"token_usage": {"cost": 0.0005}},
        })
        return ChatResult(generations=[ChatGeneration(message=step)])


def credit_error() -> Exception:
    """What OpenRouter answers when the key's credit or daily limit cannot pay for the request: a 402."""
    import httpx
    import openai

    request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    return openai.APIStatusError("This request requires more credits, or fewer max_tokens.",
                                 response=httpx.Response(402, request=request), body=None)


def stub(outcomes: dict | None = None, default=None) -> StubModel:
    return StubModel(outcomes=outcomes or {}, default=default, asked=[], shown=[], current={}, calls=0)


class CountingStub(StubModel):
    """Takes its time over every reply, and counts how many replies are being written at once."""

    seconds: float = 0.05
    running: int = 0
    peak: int = 0
    lock: object = None

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        with self.lock:
            self.running += 1
            self.peak = max(self.peak, self.running)
        try:
            time.sleep(self.seconds)
            with self.lock:  # the stub's own bookkeeping is not thread-safe
                return super()._generate(messages, stop, run_manager, **kwargs)
        finally:
            with self.lock:
                self.running -= 1


def counting_stub(seconds: float = 0.05, outcomes: dict | None = None, default=None) -> CountingStub:
    return CountingStub(outcomes=outcomes or {}, default=default, asked=[], shown=[], current={}, calls=0,
                        seconds=seconds, lock=threading.Lock())


class ScreenStub:
    """The security check's model with no network. It passes every claim, or plays `outcomes` in order, one for each call:
    a Screening, "unreadable" for an answer that cannot be read, or an exception to raise. It records what it was shown."""

    def __init__(self, outcomes=()):
        self.outcomes, self.calls, self.shown = list(outcomes), 0, []

    def with_structured_output(self, schema, **kwargs):
        from langchain_core.runnables import RunnableLambda

        def answer(messages):
            from agent.screening import Screening

            self.calls += 1
            self.shown.append(list(messages))
            outcome = self.outcomes.pop(0) if self.outcomes else Screening(reason="Nothing to find.", instructions_found=False)
            if isinstance(outcome, Exception):
                raise outcome
            raw = AIMessage(content="", usage_metadata={"input_tokens": 40, "output_tokens": 4, "total_tokens": 44},
                            response_metadata={"token_usage": {"cost": 0.0001}})
            if outcome == "unreadable":
                return {"raw": raw, "parsed": None, "parsing_error": ValueError("not JSON")}
            return {"raw": raw, "parsed": outcome, "parsing_error": None}

        return RunnableLambda(answer)


@contextmanager
def running_backend(db_path: str, data_dir: str, **settings):
    """The backend on a fresh database, in this process, with the agent's connection pointed at it."""
    from agent import connection
    from app.api import create_app

    config = Settings(_env_file=None, agent_provider="scripted", db_path=db_path, data_dir=data_dir, **settings)
    engine = make_engine(config, max_connections=1)
    init_schema(engine)
    seed_claims(engine, Reference(data_dir))
    engine.dispose()
    with api_client(create_app(config, jwks=MINT)) as backend, connection.use(backend):
        yield config


def context_answer(tool: str, args: dict) -> str:
    """What the context layer answers, in this process, from the real files: the JSON of a tool's answer. Something it
    cannot find is a ToolRefused, as the server's refusal is."""
    import json

    from agent.mcp_bridge import ToolRefused
    from context import tools

    try:
        return json.dumps(getattr(tools, tool)(**args))
    except ValueError as exc:
        raise ToolRefused(str(exc)) from exc


def in_process_context(url, name, args, auth=None) -> str:
    """The `call` of a DirectRoute whose context layer is in this process."""
    return context_answer(name, args)


def direct_route():
    """The direct route with the context layer answering in this process: tests never reach a server."""
    from agent.direct import DirectRoute

    return DirectRoute(call=in_process_context)


@contextmanager
def agent_env(db_path: str, data_dir: str, model, improver_model=None, screen_model=None, route=None, memory_store=None, **settings):
    """A real AgentEnv, with its checkpoints on disk, around a stub model, and the backend it records through, in
    this process. `env.engine` looks at the records the backend keeps: the agent has no engine of its own."""
    from agent.worker import open_env

    with running_backend(db_path, data_dir, **settings) as config:
        with open_env(config, model=model, model_id="stub/model", improver_model=improver_model,
                      screen_model=screen_model or ScreenStub(), route=route or direct_route(), memory_store=memory_store) as env:
            env.engine = make_engine(config, max_connections=2)
            try:
                yield env
            finally:
                env.engine.dispose()


class StandInGateway:
    """The Gateway with no network: the `call` a GatewayRoute is given. It names its tools `<target>___<operation>`,
    sends the app's to the test backend with the key as the real Gateway does, and refuses what its Policy refuses:
    any payment over $1,000, and the names in `deny`, once each (as an MCP error, as the real one does).
    `context` answers the context layer's tools."""

    def __init__(self, deny=(), context=None):
        self.names, self.deny, self.context = [], list(deny), context or context_answer

    def __call__(self, url, name, args, auth=None) -> str:
        import json

        from mcp.shared.exceptions import McpError
        from mcp.types import ErrorData

        from agent import connection
        from agent.mcp_bridge import ToolRefused
        from agent.route import APP_REQUESTS, request_parts

        self.names.append(name)
        denied = name in self.deny or (name.endswith("___pay_claim") and args.get("amount", 0) > 1000)
        if denied:
            if name in self.deny:
                self.deny.remove(name)
            raise McpError(ErrorData(code=-32002, message=f"Tool Execution Denied: the Policy does not permit {name}"))
        target, _, operation = name.partition("___")
        if target == "undercover-context":
            return self.context(operation, args)
        assert target == "undercover-app" and operation in APP_REQUESTS, name
        method, path, body = request_parts(operation, args)
        try:
            return json.dumps(connection.call(method, path, json=body))
        except (LookupError, connection.Refused) as exc:
            raise ToolRefused(str(exc)) from exc


def stopped_claims(engine) -> list[str]:
    """Claims left in progress with a thread: stopped partway."""
    return [r["claim_id"] for r in RawDb(engine).execute(
        "SELECT claim_id FROM claims WHERE status = 'in_progress' AND thread_id IS NOT NULL AND copy_of IS NULL ORDER BY claim_id")]


# --- sign-in: tokens like Supabase's, signed with a key of our own ---------------------------------------------

import time as _time  # noqa: E402
import types as _types  # noqa: E402

import jwt as _jwt  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec as _ec  # noqa: E402

EMAILS = {
    "HND-01": "dana.whitfield@marvinsurance.example",
    "HND-02": "marcus.oyelaran@marvinsurance.example",
    "HND-03": "priya.raman@marvinsurance.example",
}


class TokenMint:
    """Makes access tokens the way Supabase Auth does (ES256) and stands in for PyJWKClient, which would fetch the
    project's public keys over the network."""

    def __init__(self):
        self.private = _ec.generate_private_key(_ec.SECP256R1())

    def get_signing_key_from_jwt(self, token: str):
        return _types.SimpleNamespace(key=self.private.public_key())

    def token(self, email: str, audience: str = "authenticated", expires_in: int = 3600, issued_in: int = 0) -> str:
        """issued_in: seconds from now the token says it was issued; positive is a clock that runs ahead of ours."""
        now = int(_time.time())
        claims = {"sub": "user-1", "email": email, "aud": audience, "iat": now + issued_in, "exp": now + expires_in}
        return _jwt.encode(claims, self.private, algorithm="ES256")

    def header(self, handler_id: str = "HND-01", **kwargs) -> dict:
        return {"Authorization": f"Bearer {self.token(EMAILS[handler_id], **kwargs)}"}


MINT = TokenMint()  # one key pair for the whole run: the backend under test checks tokens against it


def api_client(app, **kwargs):
    """A client for the backend: its routes are under /api, and a test's paths stay relative to it."""
    from fastapi.testclient import TestClient

    return TestClient(app, base_url="http://testserver/api", **kwargs)


TEST_KEY = "test-key-" + "k" * 40  # the agent's API key in every test: conftest puts it in the environment


class ServiceClient:
    """A client for the agent's service. Starting it starts the backend the service records through, in this process,
    on the same database, as `npm run dev` starts both."""

    def __new__(cls, app, **kwargs):
        from fastapi.testclient import TestClient

        class Client(TestClient):
            def __enter__(self):
                from contextlib import ExitStack

                from agent import connection
                from app.api import create_app

                self._backend = ExitStack()
                backend = self._backend.enter_context(api_client(create_app(app.state.settings, jwks=MINT)))
                self._backend.enter_context(connection.use(backend))
                try:
                    return super().__enter__()
                except BaseException:
                    self._backend.close()
                    raise

            def __exit__(self, *exc):
                try:
                    return super().__exit__(*exc)
                finally:
                    self._backend.close()

        return Client(app, headers={"X-API-Key": TEST_KEY}, **kwargs)


def service_client(app, **kwargs):
    """A client for the agent's service, with the key every call to it needs."""
    return ServiceClient(app, **kwargs)


# --- the decision record: a calculation on file first, and who made the decision --------------------------------------

def write_calculation(engine, claim_id: str, payout: float, attempt: int | None = None) -> None:
    """The latest payout calculation of the claim's current attempt, written straight to the table: the one line the
    payout rules would give. `attempt` says which attempt it was made in; the current one by default."""
    import json

    from sqlalchemy import select

    from app.db import claims, insert_or_ignore, payout_calculations

    with engine.begin() as conn:
        if attempt is None:
            attempt = conn.execute(select(claims.c.attempts).where(claims.c.claim_id == claim_id)).scalar_one() + 1
        conn.execute(payout_calculations.delete().where(payout_calculations.c.claim_id == claim_id))
        conn.execute(insert_or_ignore(conn, payout_calculations).values(
            claim_id=claim_id, attempt=attempt, payout=payout, steps=json.dumps([{"step": "Payout", "amount": payout}])))


def record_decision(engine, ref, claim_id: str, decision: dict, model: str = "m", decided_by: str = "Test") -> dict:
    """`app.claims.record_decision` for a test: an approval gets a matching calculation first, and every record gets a
    maker. Tests that record approvals with payouts of their own keep their payouts."""
    from app import claims as claim_records

    if decision["decision"] == "approve":
        write_calculation(engine, claim_id, decision["payout"])
    return claim_records.record_decision(engine, ref, claim_id, {**decision, "decided_by": decided_by}, model)


def record_approval(client, claim_id: str, payout: float, decided_by: str = "Test"):
    """The same, over HTTP: calculate, then record. The calculation is one `expense` line for the payout, so it comes
    to the payout whatever the claim: `client` is a backend client, and the agent's key is sent."""
    key = {"X-API-Key": TEST_KEY}
    body = {"items": [{"description": "Payout", "amount": payout, "item_limit": 0}], "third_party_paid": 0,
            "deductible": 0, "benefit_max": 0}
    assert client.post(f"/claims/{claim_id}/payout", json=body, headers=key).status_code == 200
    decision = {"decision": "approve", "payout": payout, "rationale": "Covered. Section 10 sets the payment.",
                "citations": ["Policy wording, section 10"], "decided_by": decided_by, "model": "m"}
    return client.post(f"/claims/{claim_id}/decision", json=decision, headers=key)


# --- memory: AgentCore Memory with no network ------------------------------------------------------------------

class FakeMemoryStore:
    """AgentCoreMemoryStore with no network. `put` records the events it is given, as ((actor, session), key, value);
    `search` answers from `memories`, each `(namespace tuple, text)`, for the namespace asked for. `fail` makes both raise."""

    def __init__(self, memories=(), fail: Exception | None = None):
        self.puts, self.searches, self.memories, self.fail = [], [], list(memories), fail

    def put(self, namespace, key, value, *args, **kwargs):
        if self.fail:
            raise self.fail
        self.puts.append((tuple(namespace), key, value))

    def search(self, namespace_prefix, *, query=None, limit=10, **kwargs):
        from types import SimpleNamespace

        if self.fail:
            raise self.fail
        self.searches.append((tuple(namespace_prefix), query, limit))
        prefix = tuple(namespace_prefix)
        return [SimpleNamespace(namespace=ns, value={"content": text}) for ns, text in self.memories if ns[:len(prefix)] == prefix][:limit]
