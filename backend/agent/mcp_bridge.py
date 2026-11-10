"""The synchronous agent's one way to an MCP server: a session for each call (the Gateway is stateless).

The async client runs on a loop of its own that is closed afterwards. `asyncio.run` is never used: on Python 3.12 it
leaves the main thread with no event loop, and Mangum, which the app's and the context layer's tests call, asks for it."""
import asyncio
import threading

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

TIMEOUT_S = 60


class ToolRefused(Exception):
    """The tool ran and said no: the app's or the context layer's refusal, with its reason."""


async def _call(url: str, name: str, args: dict, auth: httpx.Auth | None) -> str:
    async with httpx.AsyncClient(auth=auth, timeout=TIMEOUT_S, follow_redirects=True) as http:
        async with streamable_http_client(url, http_client=http) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(name, args)
    text = "".join(block.text for block in result.content if getattr(block, "type", "") == "text")
    if result.isError:
        raise ToolRefused(text)
    return text


def _run(url: str, name: str, args: dict, auth: httpx.Auth | None) -> str:
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_call(url, name, args, auth))
    finally:
        loop.close()


def call_tool(url: str, name: str, args: dict, auth: httpx.Auth | None = None) -> str:
    """Call one tool and return its text. A tool's refusal is ToolRefused; a protocol error (the Gateway's refusal is
    one) is the MCP client's McpError."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return _run(url, name, args, auth)
    outcome: list = []  # called from inside a running loop: that loop cannot be blocked, so a thread of its own

    def work() -> None:
        try:
            outcome.append((True, _run(url, name, args, auth)))
        except BaseException as exc:
            outcome.append((False, exc))

    thread = threading.Thread(target=work)
    thread.start()
    thread.join()
    ok, value = outcome[0]
    if ok:
        return value
    raise value
