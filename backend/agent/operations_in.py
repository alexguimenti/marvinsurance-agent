"""What the agent's AgentCore app accepts: a payload that names an operation and carries its inputs, each operation with the
inputs its route took before, and no optional inputs."""
from typing import Annotated, Literal

from pydantic import BaseModel, Field, TypeAdapter


class Health(BaseModel):
    operation: Literal["health"]


class WorkRun(BaseModel):
    operation: Literal["work_run"]
    run_id: int  # a dispatch or an evaluation the app created


class WorkRound(BaseModel):
    operation: Literal["work_round"]
    round_id: int  # a round the app opened


class CarryRoundOn(BaseModel):
    operation: Literal["carry_round_on"]
    round_id: int
    proposal_id: int  # the proposal whose review the app recorded


class ResumeClaim(BaseModel):
    operation: Literal["resume_claim"]
    claim_id: str


Payload = Annotated[
    Health | WorkRun | WorkRound | CarryRoundOn | ResumeClaim,
    Field(discriminator="operation"),
]
OPERATIONS = ("health", "work_run", "work_round", "carry_round_on", "resume_claim")
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
