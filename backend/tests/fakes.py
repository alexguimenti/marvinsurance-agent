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
    return {"decision": "approve", "payout": payout, "calculation": [{"step": "Eligible", "amount": payout}],
            "rationale": rationale, "citations": ["Policy wording, section 10"]}


def decline(rationale: str = "The claim is excluded. Section 9 of the wording does not pay it.") -> dict:
    return {"decision": "decline", "payout": 0, "calculation": [], "rationale": rationale, "citations": []}


def refer() -> dict:
    return {"decision": "refer", "payout": 0, "calculation": [], "citations": [],
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
            self.current[claim_id] = list(outcome) if isinstance(outcome, list) else [outcome]
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
            step = tool_call("save_decision", step, f"save-{self.calls}")
        step = step.model_copy(update={
            "usage_metadata": {"input_tokens": 50, "output_tokens": 5, "total_tokens": 55,
                               "output_token_details": {"reasoning": 2}},
            "response_metadata": {"token_usage": {"cost": 0.0005}},
        })
        return ChatResult(generations=[ChatGeneration(message=step)])


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


@contextmanager
def agent_env(db_path: str, data_dir: str, model, improver_model=None, **settings):
    """A real AgentEnv, with its checkpoints on disk, around a stub model."""
    from agent.worker import open_env

    config = Settings(_env_file=None, agent_provider="scripted", db_path=db_path, data_dir=data_dir, **settings)
    engine = make_engine(config, max_connections=1)
    init_schema(engine)
    seed_claims(engine, Reference(data_dir))
    engine.dispose()
    with open_env(config, model=model, model_id="stub/model", improver_model=improver_model) as env:
        yield env


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

    def token(self, email: str, audience: str = "authenticated", expires_in: int = 3600) -> str:
        now = int(_time.time())
        claims = {"sub": "user-1", "email": email, "aud": audience, "iat": now, "exp": now + expires_in}
        return _jwt.encode(claims, self.private, algorithm="ES256")

    def header(self, handler_id: str = "HND-01", **kwargs) -> dict:
        return {"Authorization": f"Bearer {self.token(EMAILS[handler_id], **kwargs)}"}


MINT = TokenMint()  # one key pair for the whole run: the backend under test checks tokens against it
