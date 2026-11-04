"""The agent's service for the end-to-end tests, on port 8003, so they never reach the one `npm run dev` starts.

Runs are real, on the scripted model. A resume is only written down: the tests check that the sign-off reaches
the service, and the Python tests cover what happens next.
"""
from fastapi import FastAPI

from agent.service import create_service
from app.config import get_settings

received: list[str] = []
app = FastAPI(title="Stand-in for the agent's service")


@app.post("/claims/{claim_id}/resume", status_code=202)
def resume_claim(claim_id: str):
    """Write down that the sign-off arrived."""
    received.append(claim_id)
    return {"claim_id": claim_id}


@app.get("/received")
def get_received():
    """List the claims whose sign-off arrived, in order."""
    return received


# Everything else goes to the real service. Its lifespan is its own, so mount it with one.
service = create_service(get_settings())
app.mount("/", service)
app.router.lifespan_context = service.router.lifespan_context
