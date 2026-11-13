"""The context layer's seven lookups as the Copilot's tools, loaded with `MultiServerMCPClient`, on the route the claim agent takes:
through the AgentCore Gateway (signed with AWS Signature Version 4) when `UNDERCOVER_GATEWAY_URL` is set, or straight to the context
layer when it is not. On the Gateway the Copilot keeps only the context layer's tools: the app's tools there act as the agent.

The adapters' tools are asynchronous, and the Copilot is synchronous throughout, so each is wrapped to run on an event loop of its
own, on a thread of its own (`asyncio.run` would leave the calling thread without a loop), and renamed to its own name."""
import asyncio
import os
import threading
from typing import Any, Callable, Mapping

from langchain_core.tools import BaseTool, StructuredTool, ToolException

from agent.copilot.cards import REFUSED
from agent.direct import DEFAULT_CONTEXT
from agent.operations import CONTEXT_TARGET, CONTEXT_TOOLS


def run_sync(function: Callable, *args) -> Any:
    """Run an async function to its end on an event loop of its own, and return what it returned."""
    out: dict = {}

    def work() -> None:
        loop = asyncio.new_event_loop()
        try:
            out["value"] = loop.run_until_complete(function(*args))
        except BaseException as exc:  # handed back to the caller's thread
            out["error"] = exc
        finally:
            loop.close()

    thread = threading.Thread(target=work, name="mcp-call", daemon=True)
    thread.start()
    thread.join()
    if "error" in out:
        raise out["error"]
    return out["value"]


def connection(env: Mapping[str, str] | None = None, make_auth: Callable[[], Any] | None = None) -> dict:
    """The one connection `MultiServerMCPClient` is given: the Gateway with SigV4, or the context layer direct."""
    env = os.environ if env is None else env
    gateway = env.get("UNDERCOVER_GATEWAY_URL")
    if gateway:
        if make_auth is None:
            import boto3

            from agent.gateway import SigV4Auth

            def make_auth() -> Any:
                return SigV4Auth(boto3.Session())

        return {"undercover": {"transport": "streamable_http", "url": gateway, "auth": make_auth()}}
    return {"undercover": {"transport": "streamable_http", "url": env.get("UNDERCOVER_CONTEXT") or DEFAULT_CONTEXT}}


def _own_name(name: str) -> str:
    return name.removeprefix(f"{CONTEXT_TARGET}___")


def _text(content: Any) -> str:
    """A tool's answer as text: the adapters give a string or a list of content blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(block.get("text", "") if isinstance(block, dict) else str(block) for block in content)
    return str(content)


def _wrap(tool: BaseTool) -> BaseTool:
    own = _own_name(tool.name)

    def call(**args: Any) -> str:
        try:
            return _text(run_sync(tool.ainvoke, args))
        except ToolException as exc:  # the context layer's own refusal: told to the model, as the app's are
            return f"{REFUSED}{exc}"

    return StructuredTool.from_function(func=call, name=own, description=tool.description, args_schema=tool.args_schema)


def load_context_tools(env: Mapping[str, str] | None = None, client: Any = None, make_auth: Callable[[], Any] | None = None) -> list[BaseTool]:
    """The context layer's tools, each under its own name. `client` is a test's stand-in for `MultiServerMCPClient`."""
    if client is None:
        from langchain_mcp_adapters.client import MultiServerMCPClient

        client = MultiServerMCPClient(connection(env, make_auth))
    found = run_sync(client.get_tools)
    wanted = [t for t in found if _own_name(t.name) in CONTEXT_TOOLS]  # on the Gateway, the app's own tools are left out
    return [_wrap(t) for t in wanted]
