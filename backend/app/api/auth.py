"""Auth routes: exchange credentials for a JWT, and report the current user."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.database import get_db
from app.core.security import create_access_token, verify_password
from app.models import Tenant, User
from app.schemas.auth import LoginRequest, TokenResponse, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])

_INVALID_CREDENTIALS = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Invalid email or password",
)


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    """Verify credentials against an active user in an active tenant, issue a token.

    MVP is single-tenant, so email alone identifies the user; the active-tenant join
    keeps a disabled workspace from logging in even if the user row is active.
    """
    user = (
        await db.execute(
            select(User)
            .join(Tenant, Tenant.id == User.tenant_id)
            .where(
                User.email == body.email,
                User.is_active.is_(True),
                Tenant.is_active.is_(True),
            )
        )
    ).scalars().first()
    if user is None or not verify_password(body.password, user.hashed_password):
        raise _INVALID_CREDENTIALS
    token = create_access_token(
        user_id=user.id, tenant_id=user.tenant_id, role=user.role.value
    )
    return TokenResponse(access_token=token)


@router.get("/me", response_model=UserOut)
async def me(current_user: User = Depends(get_current_user)) -> User:
    return current_user