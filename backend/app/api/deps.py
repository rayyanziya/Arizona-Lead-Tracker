"""Shared API dependencies: bearer-token auth -> the current, tenant-scoped user.

get_current_user is the single guard every protected route depends on. It decodes
the JWT (signature + expiry), then loads the user by the token's (sub, tenant_id)
so a token can never reach across tenants even if the id collided.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import TokenError, decode_access_token
from app.models import User

# auto_error=False so a missing header yields our own 401 (not a 403 from the
# security scheme), keeping "not authenticated" responses uniform.
_bearer = HTTPBearer(auto_error=False)

_UNAUTHENTICATED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Not authenticated",
    headers={"WWW-Authenticate": "Bearer"},
)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
) -> User:
    if credentials is None:
        raise _UNAUTHENTICATED
    try:
        claims = decode_access_token(credentials.credentials)
    except TokenError:
        raise _UNAUTHENTICATED from None

    try:
        user_id = int(claims["sub"])
        tenant_id = int(claims["tenant_id"])
    except (KeyError, ValueError, TypeError):
        raise _UNAUTHENTICATED from None

    user = (
        await db.execute(
            select(User).where(User.id == user_id, User.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    if user is None or not user.is_active:
        raise _UNAUTHENTICATED
    return user