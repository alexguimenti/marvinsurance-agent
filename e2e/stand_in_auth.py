"""A stand-in for Supabase Auth on port 8004, so the end-to-end tests never reach the real project.

It signs tokens with a key of its own and serves the matching public key at the address Supabase uses, so the backend
checks them the way it checks the real ones. The three handlers sign in with the password `e2e-password`. It also
answers the browser's cross-origin checks, since the dashboard and this server are on different ports.
"""
import base64
import time

import jwt
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

PASSWORD = "e2e-password"
EMAILS = {
    "dana.whitfield@marvinsurance.example",
    "marcus.oyelaran@marvinsurance.example",
    "priya.raman@marvinsurance.example",
}
KID = "e2e-key"
KEY = ec.generate_private_key(ec.SECP256R1())
EXPIRES_IN = 3600

app = FastAPI(title="Stand-in for Supabase Auth")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def _b64(number: int) -> str:
    return base64.urlsafe_b64encode(number.to_bytes(32, "big")).rstrip(b"=").decode()


def _user(email: str) -> dict:
    return {"id": f"user-{email.split('@')[0]}", "aud": "authenticated", "role": "authenticated", "email": email,
            "app_metadata": {}, "user_metadata": {}, "created_at": "2026-01-01T00:00:00Z"}


def _session(email: str) -> dict:
    now = int(time.time())
    claims = {"sub": _user(email)["id"], "email": email, "aud": "authenticated", "role": "authenticated",
              "iat": now, "exp": now + EXPIRES_IN}
    token = jwt.encode(claims, KEY, algorithm="ES256", headers={"kid": KID})
    return {"access_token": token, "token_type": "bearer", "expires_in": EXPIRES_IN, "expires_at": now + EXPIRES_IN,
            "refresh_token": f"refresh-{email}", "user": _user(email)}


class Credentials(BaseModel):
    email: str | None = None
    password: str | None = None
    refresh_token: str | None = None


@app.post("/auth/v1/token")
def token(body: Credentials, grant_type: str):
    """Sign a handler in with a password, or give a new session for a refresh token."""
    if grant_type == "refresh_token" and body.refresh_token and body.refresh_token.startswith("refresh-"):
        return _session(body.refresh_token.removeprefix("refresh-"))
    if grant_type == "password" and body.email in EMAILS and body.password == PASSWORD:
        return _session(body.email)
    raise HTTPException(400, {"error": "invalid_grant", "error_description": "Invalid login credentials"})


@app.get("/auth/v1/.well-known/jwks.json")
def jwks():
    """The public key the backend checks tokens against."""
    point = KEY.public_key().public_numbers()
    return {"keys": [{"kty": "EC", "crv": "P-256", "alg": "ES256", "use": "sig", "kid": KID,
                      "x": _b64(point.x), "y": _b64(point.y)}]}


@app.post("/auth/v1/logout", status_code=204)
def logout():
    """Accept a sign-out."""


@app.get("/auth/v1/health")
def health():
    """Say that the stand-in is up."""
    return {"status": "ok"}
