"""What the agent's AgentCore app accepts: a payload that names an operation and carries its inputs, each operation with the
inputs its route took before, and no optional inputs."""
from typing import Annotated, Literal

from pydantic import BaseModel, Field, TypeAdapter

MAX_WORKERS = 25


class Health(BaseModel):
    operation: Literal["health"]


class StartDispatch(BaseModel):
    operation: Literal["start_dispatch"]
    workers: int = Field(ge=1, le=MAX_WORKERS)  # claim agents at once


class StartEvaluation(BaseModel):
    operation: Literal["start_evaluation"]
    eval_set: Literal["evaluation", "held_out"]
    tiny: bool  # only the set's five tiny claims
    workers: int = Field(ge=1, le=MAX_WORKERS)


class StartImproverRound(BaseModel):
    operation: Literal["start_improver_round"]
    handler_id: str  # who started the round


class DecideRoundProposal(BaseModel):
    operation: Literal["decide_round_proposal"]
    round_id: int
    proposal_id: int
    approve: bool
    reason: str  # a rejection's reason goes back to the improver
    handler_id: str


class ResumeClaim(BaseModel):
    operation: Literal["resume_claim"]
    claim_id: str


Payload = Annotated[
    Health | StartDispatch | StartEvaluation | StartImproverRound | DecideRoundProposal | ResumeClaim,
    Field(discriminator="operation"),
]
OPERATIONS = ("health", "start_dispatch", "start_evaluation", "start_improver_round", "decide_round_proposal", "resume_claim")
_adapter = TypeAdapter(Payload)


class BadPayload(ValueError):
    """The payload names no operation the agent knows, or has an input missing or wrong. The message says what."""


def parse(payload: object) -> Payload:
    if not isinstance(payload, dict):
        raise BadPayload("the payload must be a JSON object with an operation")
    name = payload.get("operation")
    if name not in OPERATIONS:
        raise BadPayload(f"unknown operation {name!r}; the operations are {', '.join(OPERATIONS)}")
    from pydantic import ValidationError

    try:
        return _adapter.validate_python(payload)
    except ValidationError as exc:
        first = exc.errors()[0]
        field = ".".join(str(p) for p in first["loc"][1:]) or "payload"
        raise BadPayload(f"{name}: {field}: {first['msg']}") from exc
