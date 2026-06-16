"""Monitored-sources admin API: tenant-scoped CRUD over watched feeds."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi import status as http_status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models import MonitoredSource, Platform, User
from app.schemas.admin import SourceCreate, SourceOut, SourceUpdate
from app.services.facebook_group import facebook_group_id

router = APIRouter(prefix="/sources", tags=["sources"])

_DUPLICATE = HTTPException(
    status_code=http_status.HTTP_409_CONFLICT,
    detail="A source with this platform and identifier already exists",
)
_NOT_FOUND = HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Source not found")
_BAD_FACEBOOK_IDENTIFIER = HTTPException(
    status_code=422,  # Unprocessable: matches Pydantic's own validation status
    detail=(
        "Facebook source must be a group URL or id, e.g. "
        "https://facebook.com/groups/<id> or just <id>"
    ),
)


async def _get_owned(db: AsyncSession, tenant_id: int, source_id: int) -> MonitoredSource:
    source = (
        await db.execute(
            select(MonitoredSource).where(
                MonitoredSource.id == source_id, MonitoredSource.tenant_id == tenant_id
            )
        )
    ).scalar_one_or_none()
    if source is None:
        raise _NOT_FOUND
    return source


@router.get("", response_model=list[SourceOut])
async def list_sources(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[MonitoredSource]:
    return list(
        (
            await db.execute(
                select(MonitoredSource)
                .where(MonitoredSource.tenant_id == current_user.tenant_id)
                .order_by(MonitoredSource.id)
            )
        ).scalars().all()
    )


@router.post("", response_model=SourceOut, status_code=http_status.HTTP_201_CREATED)
async def create_source(
    body: SourceCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MonitoredSource:
    source = MonitoredSource(
        tenant_id=current_user.tenant_id,
        platform=body.platform,
        identifier=body.identifier,
        label=body.label,
        is_active=body.is_active,
    )
    db.add(source)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise _DUPLICATE from None
    await db.refresh(source)
    return source


@router.patch("/{source_id}", response_model=SourceOut)
async def update_source(
    source_id: int,
    body: SourceUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MonitoredSource:
    source = await _get_owned(db, current_user.tenant_id, source_id)
    changes = body.model_dump(exclude_unset=True)
    # Platform is immutable on update, so editing a Facebook source's identifier
    # must satisfy the same group-reference rule as creation -- otherwise an
    # operator could edit a working source into one that silently scrapes nothing.
    if (
        "identifier" in changes
        and source.platform == Platform.FACEBOOK
        and facebook_group_id(changes["identifier"]) is None
    ):
        raise _BAD_FACEBOOK_IDENTIFIER
    for field, value in changes.items():
        setattr(source, field, value)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise _DUPLICATE from None
    await db.refresh(source)
    return source


@router.delete("/{source_id}", status_code=http_status.HTTP_204_NO_CONTENT)
async def delete_source(
    source_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    source = await _get_owned(db, current_user.tenant_id, source_id)
    await db.delete(source)
    await db.commit()