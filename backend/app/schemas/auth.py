"""Auth request/response schemas for the dashboard API."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, EmailStr


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105 - OAuth token type label, not a secret


class UserOut(BaseModel):
    """The authenticated principal, safe to return to the client (no password)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    full_name: str | None = None
    role: str
    tenant_id: int