"""What the Copilot's AgentCore app accepts: a payload that names an operation and carries its inputs, with no optional inputs. A turn and an
answer to a confirmation carry the handler's sign-in. An error names the field and what is wrong with it, and never shows the input,
so the sign-in is never echoed."""
from typing import Annotated, Literal

from pydantic import BaseModel, Field, TypeAdapter, ValidationError

CONVERSATION_ID = r"^[A-Za-z0-9-]{33,100}$"  # also the Runtime session id and the Memory's session


class Health(BaseModel):
    operation: Literal["health"]


class Turn(BaseModel):
    operation: Literal["turn"]
    conversation_id: str = Field(pattern=CONVERSATION_ID)
    turn_id: int  # the turn the app recorded
    message: str = Field(min_length=1, max_length=4000)
    token: str = Field(min_length=1)  # the handler's sign-in


class Answer(BaseModel):
    operation: Literal["answer"]
    conversation_id: str = Field(pattern=CONVERSATION_ID)
    turn_id: int
    answer: Literal["sign_off", "cancel"]
    token: str = Field(min_length=1)


Payload = Annotated[Health | Turn | Answer, Field(discriminator="operation")]
OPERATIONS = ("health", "turn", "answer")
_adapter = TypeAdapter(Payload)


class BadPayload(ValueError):
    """The payload names no operation the Copilot knows, or has an input missing or wrong. The message says what."""


def parse(payload: object) -> Payload:
    if not isinstance(payload, dict):
        raise BadPayload("the payload must be a JSON object with an operation")
    name = payload.get("operation")
    if name not in OPERATIONS:
        raise BadPayload(f"unknown operation {name!r}; the operations are {', '.join(OPERATIONS)}")
    try:
        return _adapter.validate_python(payload)
    except ValidationError as exc:
        first = exc.errors()[0]
        field = ".".join(str(p) for p in first["loc"][1:]) or "payload"
        raise BadPayload(f"{name}: {field}: {first['msg']}") from exc
