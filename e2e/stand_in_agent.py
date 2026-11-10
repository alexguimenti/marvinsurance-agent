"""The agent's service for the end-to-end tests, on port 8003, so they never reach the one `npm run dev` starts.

Dispatches and evaluations are real, on the scripted model, and each start is written down with its body, so the
tests can check that N reached the service. It records through the backend, over HTTP with the API key, as the real
service does.
"""
import json

from fastapi import FastAPI

from agent.service import create_service
from app.config import get_settings

received: dict[str, list] = {"starts": []}
STARTS = ("/dispatches", "/evaluations")
app = FastAPI(title="Stand-in for the agent's service")


@app.get("/received")
def get_received():
    """List the starts that arrived, in order."""
    return received


def recording(asgi):
    """Pass every request on to the real service, writing down the body of each start first."""

    async def wrapped(scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST" or scope["path"] not in STARTS:
            return await asgi(scope, receive, send)
        messages, body = [], b""
        while True:
            message = await receive()
            messages.append(message)
            body += message.get("body", b"")
            if not message.get("more_body"):
                break
        received["starts"].append({"path": scope["path"], "body": json.loads(body or b"{}")})

        async def replay():
            return messages.pop(0) if messages else {"type": "http.disconnect"}

        return await asgi(scope, replay, send)

    return wrapped


# Everything else goes to the real service. Its lifespan is its own, so mount it with one.
service = create_service(get_settings())
app.mount("/", recording(service))
app.router.lifespan_context = service.router.lifespan_context
