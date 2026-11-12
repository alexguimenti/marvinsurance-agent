"""The agent for the end-to-end tests, on port 8003, so they never reach the one `npm run dev` starts.

It is the real AgentCore app (`agent.entrypoint`) on the scripted model: dispatches and evaluations are real, and each
start is written down with its payload, so the tests can check that N reached the agent. It records through the backend over
HTTP with the API key, as the real agent does. `/ping` is the app's own, which Playwright waits on.
"""
import json

from fastapi import FastAPI

from agent.entrypoint import app as agent_app

received: dict[str, list] = {"starts": []}
STARTS = ("start_dispatch", "start_evaluation")
app = FastAPI(title="Stand-in for the agent")


@app.get("/received")
def get_received():
    """List the starts that arrived, in order."""
    return received


def recording(asgi):
    """Pass every request on to the real agent, writing down the payload of each start first."""

    async def wrapped(scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST" or scope["path"] != "/invocations":
            return await asgi(scope, receive, send)
        messages, body = [], b""
        while True:
            message = await receive()
            messages.append(message)
            body += message.get("body", b"")
            if not message.get("more_body"):
                break
        try:
            payload = json.loads(body or b"{}")
        except ValueError:
            payload = {}
        if payload.get("operation") in STARTS:
            received["starts"].append(payload)

        async def replay():
            return messages.pop(0) if messages else {"type": "http.disconnect"}

        return await asgi(scope, replay, send)

    return wrapped


# Everything else goes to the real agent: its `/ping` and its `/invocations`.
app.mount("/", recording(agent_app))
