"""The Copilot's routes. They take a handler's sign-in, as the dashboard's routes do, and never the agent's key: a request that carries
`X-API-Key` is the agent's whatever else it carries, and these routes refuse it.

The dashboard's routes keep a handler's conversations, start each turn (and each answer to a confirmation) on the Copilot with the
handler's sign-in in the payload, and answer at once. The Copilot reports back through the report routes, with the same sign-in: each
step as it takes it, a pause for the handler's confirmation, the end of the turn and a failure. A report on another handler's turn is
an unknown turn. The sign-in is passed on and never stored."""
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy import Engine

from app import copilot, copilot_client
from app.agent_client import AgentMaybe, AgentNotReached, AgentRefused
from app.auth import Guards, Handler
from app.reference import Reference

_bearer = HTTPBearer(auto_error=False)


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class ConfirmationIn(BaseModel):
    answer: Literal["sign_off", "cancel"]


class StepIn(BaseModel):
    kind: Literal["claims", "customers", "context", "work", "memory", "action"]
    tool: str = Field(min_length=1, max_length=100)
    main_input: str | None = Field(max_length=300)  # every input is required, and may be null
    input: dict | None


class StepDoneIn(BaseModel):
    status: Literal["done", "refused", "failed"]
    output: str | None = Field(max_length=20000)
    sources: list[dict] | None
    reason: str | None = Field(max_length=1000)


class PauseIn(BaseModel):
    claim_id: str = Field(min_length=1, max_length=40)


class EndIn(BaseModel):
    answer: str = Field(max_length=20000)
    model_calls: int = Field(ge=0)
    cost_usd: float | None = Field(ge=0)


class FailIn(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def copilot_routes(engine: Engine, ref: Reference, guards: Guards, settings, http) -> APIRouter:
    def no_key(request: Request) -> None:
        if "x-api-key" in request.headers:
            raise HTTPException(403, "the Copilot's routes take a handler's sign-in, never the agent's key")

    router = APIRouter(prefix="/api/copilot", dependencies=[Depends(no_key), Depends(guards.signed_in)])
    signed_in = guards.signed_in

    def token_of(credentials: HTTPAuthorizationCredentials | None) -> str:
        return credentials.credentials  # signed_in has already refused a request with none

    def owned_turn(turn_id: int, handler: Handler):
        try:
            return copilot.turn_of(engine, turn_id, handler.handler_id)
        except LookupError as exc:
            raise HTTPException(404, str(exc))

    def hand_over(payload: dict, session_id: str, undo) -> None:
        """Ask the Copilot to work what the app has just recorded. When it did not take it (it refused, or the request never got
        there) the app undoes it; when the call may have started the work (a timeout) the app changes nothing."""
        try:
            copilot_client.send(settings, payload, session_id, http)
        except AgentRefused as exc:
            undo(exc.reason)
            raise HTTPException(409, exc.reason)
        except AgentNotReached as exc:
            undo(str(exc))
            if exc.status:
                raise HTTPException(exc.status, str(exc))
            raise HTTPException(503, "the Copilot is not running")
        except AgentMaybe:
            raise HTTPException(504, "The Copilot did not answer in time. Reload the page to see whether it went through.")

    def fail_unless_ended(turn_id: int, reason: str) -> None:
        try:
            copilot.fail_turn(engine, turn_id, reason)
        except copilot.TurnEnded:
            pass

    @router.post("/conversations", status_code=201, operation_id="create_copilot_conversation")
    def create_conversation(handler: Handler = Depends(signed_in)):
        """Start a new conversation with the Copilot, as the signed-in handler."""
        return {"conversation_id": copilot.create_conversation(engine, handler.handler_id)}

    @router.get("/conversations", operation_id="list_copilot_conversations")
    def list_conversations(handler: Handler = Depends(signed_in)):
        """List the signed-in handler's conversations with the Copilot, newest first, each titled with its first question."""
        return copilot.list_conversations(engine, handler.handler_id)

    @router.get("/conversations/{conversation_id}", operation_id="get_copilot_conversation")
    def get_conversation(conversation_id: str, handler: Handler = Depends(signed_in)):
        """Get one of the signed-in handler's conversations with each turn, its steps and its confirmation card."""
        try:
            return copilot.conversation_view(engine, conversation_id, handler.handler_id, _now())
        except LookupError as exc:
            raise HTTPException(404, str(exc))

    @router.post("/conversations/{conversation_id}/messages", status_code=202, operation_id="send_copilot_message")
    def send_message(conversation_id: str, body: MessageIn, handler: Handler = Depends(signed_in),
                     credentials: HTTPAuthorizationCredentials | None = Depends(_bearer)):
        """Send the Copilot a message and start its turn, with the handler's sign-in. One turn at a time in a conversation."""
        try:
            turn_id = copilot.start_turn(engine, conversation_id, handler.handler_id, body.text, _now())
        except LookupError as exc:
            raise HTTPException(404, str(exc))
        except copilot.TurnOpen as exc:
            raise HTTPException(409, str(exc))
        payload = {"operation": "turn", "conversation_id": conversation_id, "turn_id": turn_id, "message": body.text, "token": token_of(credentials)}
        hand_over(payload, conversation_id, lambda why: fail_unless_ended(turn_id, f"The Copilot did not start: {why}"[:1000]))
        return {"turn_id": turn_id}

    @router.post("/conversations/{conversation_id}/confirmation", status_code=202, operation_id="answer_copilot_confirmation")
    def answer_confirmation(conversation_id: str, body: ConfirmationIn, handler: Handler = Depends(signed_in),
                            credentials: HTTPAuthorizationCredentials | None = Depends(_bearer)):
        """Answer the confirmation the Copilot waits on, Sign off and pay or Cancel, once, and let its turn carry on."""
        try:
            turn_id = copilot.answer_confirmation(engine, conversation_id, handler.handler_id, body.answer, _now())
        except LookupError as exc:
            raise HTTPException(404, str(exc))
        except copilot.NotWaiting as exc:
            raise HTTPException(409, str(exc))
        payload = {"operation": "answer", "conversation_id": conversation_id, "turn_id": turn_id, "answer": body.answer, "token": token_of(credentials)}
        hand_over(payload, conversation_id, lambda why: copilot.undo_answer(engine, turn_id, body.answer))
        return {"turn_id": turn_id}

    @router.post("/turns/{turn_id}/steps", operation_id="record_copilot_step")
    def record_step(turn_id: int, body: StepIn, handler: Handler = Depends(signed_in)):
        """Record a step the Copilot is taking on a turn, as a card in the sidebar."""
        turn = owned_turn(turn_id, handler)
        return {"step_id": copilot.record_step(engine, turn_id, turn["conversation_id"], body.kind, body.tool, body.main_input, body.input)}

    @router.post("/steps/{step_id}", operation_id="finish_copilot_step")
    def finish_step(step_id: int, body: StepDoneIn, handler: Handler = Depends(signed_in)):
        """Record how a step ended: done, refused or failed, with what came back."""
        try:
            copilot.step_of(engine, step_id, handler.handler_id)
        except LookupError as exc:
            raise HTTPException(404, str(exc))
        copilot.finish_step(engine, step_id, body.status, body.output, body.sources, body.reason)
        return {"step_id": step_id}

    @router.post("/turns/{turn_id}/pause", operation_id="pause_copilot_turn")
    def pause_turn(turn_id: int, body: PauseIn, handler: Handler = Depends(signed_in)):
        """Pause a turn for the handler's confirmation to sign off a held payout, and return the card to show."""
        owned_turn(turn_id, handler)
        try:
            return copilot.pause_turn(engine, ref, turn_id, body.claim_id)
        except (copilot.NoCard, copilot.TurnEnded) as exc:
            raise HTTPException(409, str(exc))

    @router.post("/turns/{turn_id}/end", operation_id="end_copilot_turn")
    def end_turn(turn_id: int, body: EndIn, handler: Handler = Depends(signed_in)):
        """End a turn with the Copilot's answer, its model calls and what they cost."""
        owned_turn(turn_id, handler)
        try:
            copilot.end_turn(engine, turn_id, body.answer, body.model_calls, body.cost_usd)
        except copilot.TurnEnded as exc:
            raise HTTPException(409, str(exc))
        return {"turn_id": turn_id}

    @router.post("/turns/{turn_id}/fail", operation_id="fail_copilot_turn")
    def fail_turn(turn_id: int, body: FailIn, handler: Handler = Depends(signed_in)):
        """End a turn that failed, with why."""
        owned_turn(turn_id, handler)
        try:
            copilot.fail_turn(engine, turn_id, body.reason)
        except copilot.TurnEnded as exc:
            raise HTTPException(409, str(exc))
        return {"turn_id": turn_id}

    return router
