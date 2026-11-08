"""Who is signed in. The dashboard signs a handler in with Supabase Auth and sends the session's access token with each
action; this checks it against the project's public keys (no secret is needed: the project signs with asymmetric keys)
and finds the handler by the token's email in data/handlers.json."""
from dataclasses import asdict, dataclass

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient

from app.config import Settings
from app.reference import Reference

AUDIENCE = "authenticated"
SIGNING_ALGORITHMS = ["ES256", "RS256"]  # asymmetric only: never a shared secret


class NotSignedIn(Exception):
    """No token, or one that is not valid: expired, for another audience or signed by another key."""


class UnknownHandler(Exception):
    """A valid sign-in for someone who is not one of our handlers."""


@dataclass
class Handler:
    handler_id: str
    name: str
    role: str
    email: str
    can_approve_over_limit: bool


def make_jwks(settings: Settings) -> PyJWKClient | None:
    """The project's public keys, fetched once and kept. None when sign-in is not set up: every token is then refused."""
    if not settings.supabase_url:
        return None
    return PyJWKClient(f"{settings.supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json", cache_keys=True)


def handler_from_token(token: str, ref: Reference, jwks) -> Handler:
    """The handler a token belongs to. Raises NotSignedIn or UnknownHandler."""
    if jwks is None:
        raise NotSignedIn("sign-in is not set up on this server")
    try:
        key = jwks.get_signing_key_from_jwt(token).key
        claims = jwt.decode(token, key, algorithms=SIGNING_ALGORITHMS, audience=AUDIENCE)
    except jwt.PyJWTError as exc:
        raise NotSignedIn(str(exc)) from exc
    email = str(claims.get("email", "")).lower()
    for row in ref.handlers:
        if row["email"].lower() == email:
            return Handler(row["handler_id"], row["name"], row["role"], row["email"], bool(row["can_approve_over_limit"]))
    raise UnknownHandler(f"{email or 'this account'} is not one of our handlers")


def make_guards(ref: Reference, jwks):
    """The two dependencies every handler action uses: any signed-in handler, and one who may sign off over the limit."""
    bearer = HTTPBearer(auto_error=False)

    def signed_in(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> Handler:
        if credentials is None:
            raise HTTPException(401, "sign in first")
        try:
            return handler_from_token(credentials.credentials, ref, jwks)
        except NotSignedIn:
            raise HTTPException(401, "your session is not valid: sign in again")
        except UnknownHandler as exc:
            raise HTTPException(403, str(exc))

    def senior(handler: Handler = Depends(signed_in)) -> Handler:
        if not handler.can_approve_over_limit:
            raise HTTPException(403, "only a handler who can sign off payouts over the limit can do this")
        return handler

    return signed_in, senior


def handler_view(handler: Handler) -> dict:
    return asdict(handler)
