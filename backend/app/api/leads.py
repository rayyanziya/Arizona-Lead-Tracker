"""Leads API: list/filter the tenant's AI-scored matches and triage their status.

Every query is scoped to the authenticated user's tenant, so a token for one
workspace can never read or mutate another's leads. The source post is eager-loaded
(selectinload) because lazy relationship access is unsafe under the async session.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi import status as http_status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models import Match, MatchStatus, Platform, Post, User
from app.schemas.lead import LeadListOut, LeadOut, LeadStatusUpdate

router = APIRouter(prefix="/leads", tags=["leads"])


@router.get("", response_model=LeadListOut)
async def list_leads(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    status: MatchStatus | None = Query(None),
    platform: Platform | None = Query(None),
    min_score: int | None = Query(None, ge=1, le=10),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> LeadListOut:
    base = select(Match).where(Match.tenant_id == current_user.tenant_id)
    if status is not None:
        base = base.where(Match.status == status)
    if min_score is not None:
        base = base.where(Match.ai_score >= min_score)
    if platform is not None:
        base = base.join(Post, Post.id == Match.post_id).where(Post.platform == platform)

    total = (
        await db.execute(select(func.count()).select_from(base.subquery()))
    ).scalar_one()
    rows = (
        await db.execute(
            base.options(selectinload(Match.post))
            .order_by(Match.created_at.desc(), Match.id.desc())
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()
    return LeadListOut(items=rows, total=total, limit=limit, offset=offset)


@router.patch("/{lead_id}", response_model=LeadOut)
async def update_lead(
    lead_id: int,
    body: LeadStatusUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Match:
    match = (
        await db.execute(
            select(Match)
            .options(selectinload(Match.post))
            .where(Match.id == lead_id, Match.tenant_id == current_user.tenant_id)
        )
    ).scalar_one_or_none()
    if match is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Lead not found")
    match.status = body.status
    await db.commit()
    return match