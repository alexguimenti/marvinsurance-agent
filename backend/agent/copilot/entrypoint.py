"""The Copilot as an AgentCore app of its own, built as the claim agent's is (`agent/entrypoint.py`): the same app on a laptop (`npm run dev` serves
it with uvicorn on 127.0.0.1:8081) and on AgentCore Runtime as the second runtime, `copilot` (`runtime/copilot.py` runs it, on 8080).

One entrypoint; each payload names an operation (agent/copilot/payloads.py): `turn` (a handler's message), `answer` (a handler's answer to a
confirmation) and `health`. A turn or an answer is accepted at once, and its work goes on a thread of its own, registered with the app as an
async task, so `/ping` says HealthyBusy while it works and a Runtime session lasts as long as the turn.

The Copilot asks for no key. Everything it does carries the handler's sign-in, which the payload brings, and the app checks it on every call.
On Runtime AWS checked that the caller may invoke the runtime. Nothing happens when this module is imported or when the app starts: the
Copilot gets ready on its first request, once in its process, because every Runtime session starts from one snapshot, so a connection opened
earlier would be shared by every session. On Runtime it fetches only OPENROUTER_API_KEY and DATABASE_URL from AgentCore Identity."""
import logging
import random
import threading
from contextlib import ExitStack
from typing import Callable

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from starlette.exceptions import HTTPException

from agent.copilot.payloads import Answer, BadPayload, Health, Turn, parse
from agent.copilot.turns import CopilotEnv, answer, open_env, run_turn
from agent.identity import fetch_credentials, on_runtime
from agent.settings import AgentSettings

log = logging.getLogger(__name__)
CREDENTIALS = {"OPENROUTER_API_KEY", "DATABASE_URL"}  # the Copilot never fetches the agent's API key


def build_copilot_app(make_settings: Callable[[], AgentSettings] = AgentSettings, inline: bool = False) -> BedrockAgentCoreApp:
    """With inline, work happens before a call returns, which is what tests want."""
    app = BedrockAgentCoreApp()
    holder: dict = {}
    stack = ExitStack()
    ready = threading.Lock()

    def get_ready() -> CopilotEnv:
        """Once in this process: credentials (on Runtime), the checkpoint pool. A failure answers the request with the error, and the next
        request tries again."""
        with ready:
            if "env" in holder:
                return holder["env"]
            try:
                if on_runtime():
                    random.seed()  # every session starts from one snapshot, with the same random numbers
                    fetch_credentials(only=CREDENTIALS)
                env = stack.enter_context(open_env(make_settings()))
            except BaseException:
                stack.close()
                raise
            holder["env"] = env
            return env

    def in_background(name: str, work: Callable, *args) -> None:
        task = app.add_async_task(name)  # registered before the answer: the ping says HealthyBusy until the work ends

        def guarded() -> None:
            try:
                work(*args)
            except Exception:  # the work reports its own failures to the app; this is what it could not
                log.exception("%s failed", name)
            finally:
                app.complete_async_task(task)

        if inline:
            guarded()
        else:
            threading.Thread(target=guarded, name=name, daemon=True).start()  # daemon: stopping the Copilot stops the work

    @app.entrypoint
    def invoke(payload: dict, context) -> dict:
        try:
            body = parse(payload)
        except BadPayload as exc:
            raise HTTPException(400, str(exc)) from exc
        env = get_ready()  # a health check gets ready like any other request
        if isinstance(body, Health):
            return {"healthy": True}
        if isinstance(body, Turn):
            in_background(f"turn {body.turn_id}", run_turn, env, body)
        else:
            in_background(f"answer {body.turn_id}", answer, env, body)
        return {"accepted": True}

    app.state_holder = holder
    return app


# `uvicorn agent.copilot.entrypoint:app` and runtime/copilot.py ask for this: it builds nothing but the app itself.
app = build_copilot_app()
