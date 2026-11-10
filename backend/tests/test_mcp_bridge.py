"""The bridge from the synchronous agent to MCP: one call, one session, and the thread's event loop left as it was."""
import asyncio
import socket
import threading
import time

import pytest
import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from agent.mcp_bridge import ToolRefused, call_tool


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def url():
    server = FastMCP("test", stateless_http=True, json_response=True,
                     transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))

    @server.tool()
    def echo(text: str) -> str:
        """Say it back."""
        return text

    @server.tool()
    def boom() -> str:
        """Always fails."""
        raise ValueError("no such thing")

    port = free_port()
    runner = uvicorn.Server(uvicorn.Config(server.streamable_http_app(), host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=runner.run, daemon=True)
    thread.start()
    while not runner.started:
        time.sleep(0.02)
    yield f"http://127.0.0.1:{port}/mcp"
    runner.should_exit = True
    thread.join(5)


def test_a_call_returns_the_tools_text(url):
    assert call_tool(url, "echo", {"text": "hi"}) == "hi"


def test_a_tool_error_is_tool_refused_with_its_message(url):
    with pytest.raises(ToolRefused, match="no such thing"):
        call_tool(url, "boom", {})


def test_the_main_threads_loop_is_left_as_it_was(url):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        call_tool(url, "echo", {"text": "a"})
        call_tool(url, "echo", {"text": "b"})
        assert asyncio.get_event_loop() is loop and not loop.is_closed()
    finally:
        asyncio.set_event_loop(None)
        loop.close()


def test_a_call_from_inside_a_running_loop_works_too(url):
    async def inside():
        return call_tool(url, "echo", {"text": "x"})

    done = []
    thread = threading.Thread(target=lambda: done.append(asyncio.run(inside())))  # a thread of its own: asyncio.run unsets the loop
    thread.start()
    thread.join(30)
    assert done == ["x"]
