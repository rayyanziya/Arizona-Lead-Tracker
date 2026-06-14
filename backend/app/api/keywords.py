"""Keywords admin API: tenant-scoped CRUD over the matched terms."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi import status as http_status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models import Keyword, User
from app.schemas.admin import KeywordCreate, KeywordOut, KeywordUpdate

router = APIRouter(prefix="/keywords", tags=["keywords"])

_DUPLICATE = HTTPException(
    status_code=http_status.HTTP_409_CONFLICT,
    detail="A keyword with this term and match type already exists",
)
_NOT_FOUND = HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Keyword not found")


async def _get_owned(db: AsyncSession, tenant_id: int, keyword_id: int) -> Keyword:
    keyword = (
        await db.execute(
            select(Keyword).where(Keyword.id == keyword_id, Keyword.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    if keyword is None:
        raise _NOT_FOUND
    return keyword


@router.get("", response_model=list[KeywordOut])
async def list_keywords(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Keyword]:
    return list(
        (
            await db.execute(
                select(Keyword)
                .where(Keyword.tenant_id == current_user.tenant_id)
                .order_by(Keyword.id)
            )
        ).scalars().all()
    )


@router.post("", response_model=KeywordOut, status_code=http_status.HTTP_201_CREATED)
async def create_keyword(
    body: KeywordCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Keyword:
    keyword = Keyword(
        tenant_id=current_user.tenant_id,
        term=body.term,
        language=body.language,
        match_type=body.match_type,
        is_active=body.is_active,
    )
    db.add(keyword)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise _DUPLICATE from None
    await db.refresh(keyword)
    return keyword


@router.patch("/{keyword_id}", response_model=KeywordOut)
async def update_keyword(
    keyword_id: int,
    body: KeywordUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Keyword:
    keyword = await _get_owned(db, current_user.tenant_id, keyword_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(keyword, field, value)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise _DUPLICATE from None
    await db.refresh(keyword)
    return keyword


@router.delete("/{keyword_id}", status_code=http_status.HTTP_204_NO_CONTENT)
async def delete_keyword(
    keyword_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    keyword = await _get_owned(db, current_user.tenant_id, keyword_id)
    await db.delete(keyword)
    await db.commit()