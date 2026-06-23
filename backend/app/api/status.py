"""Config-status endpoint: what the system is actually wired to do.

Returns read-only capability booleans (never secrets) so the dashboard can
explain why leads may not be flowing -- a missing Anthropic key, missing Reddit
credentials, or no captured Facebook session. Auth-gated like the rest of the API.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.models import ScrapeRun, ScrapeStatus, User
from app.monitors.fb_session import session_path
from app.services.config_status import config_status
from app.services.scoring import AnthropicLike, score_post

router = APIRouter(prefix="/status", tags=["status"])


class ConfigStatusOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    scoring_configured: bool
    reddit_configured: bool
    x_configured: bool
    facebook_session_present: bool
    telegram_configured: bool
    email_configured: bool


@router.get("", response_model=ConfigStatusOut)
async def get_status(current_user: User = Depends(get_current_user)) -> ConfigStatusOut:
    present = session_path(settings.browser_session_dir).exists()
    return ConfigStatusOut.model_validate(
        config_status(settings, facebook_session_present=present)
    )


class ScrapeHealthOut(BaseModel):
    """Whether collection is currently being walled by the platform."""

    blocked: bool  # the newest finished run was BLOCKED -> nothing is collecting
    platform: str | None  # which platform is walled (e.g. "facebook"), if any
    consecutive_blocked: int  # unbroken BLOCKED streak from the newest run back
    last_blocked_at: datetime | None
    last_success_at: datetime | None


# How many recent runs to inspect. Sources cycle every ~20 min, so 30 runs is
# several hours of history -- plenty to see a streak without scanning the table.
_HEALTH_WINDOW = 30


@router.get("/scrape-health", response_model=ScrapeHealthOut)
async def get_scrape_health(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ScrapeHealthOut:
    """Report whether the collector is being blocked right now.

    Walks the most recent *finished* scrape runs (newest first) and counts the
    unbroken run of BLOCKED outcomes at the top. A nonzero streak means the
    platform is walling us this very cycle -- a stale Facebook session or a
    checkpoint -- so the dashboard can tell the operator to re-capture instead of
    silently collecting nothing. RUNNING rows are skipped: an in-flight scrape is
    not yet an outcome.
    """
    rows = (
        await db.execute(
            select(ScrapeRun.platform, ScrapeRun.status, ScrapeRun.started_at)
            .where(
                ScrapeRun.tenant_id == current_user.tenant_id,
                ScrapeRun.status != ScrapeStatus.RUNNING,
            )
            .order_by(ScrapeRun.started_at.desc())
            .limit(_HEALTH_WINDOW)
        )
    ).all()

    # The last success can predate the whole window (e.g. a long blocked streak),
    # so look it up directly rather than scanning `rows` -- otherwise a fully
    # blocked window would wrongly report "never collected".
    last_success_at = (
        await db.execute(
            select(ScrapeRun.started_at)
            .where(
                ScrapeRun.tenant_id == current_user.tenant_id,
                ScrapeRun.status == ScrapeStatus.SUCCESS,
            )
            .order_by(ScrapeRun.started_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    last_blocked_at = next(
        (r.started_at for r in rows if r.status == ScrapeStatus.BLOCKED), None
    )

    consecutive = 0
    platform = None
    for r in rows:
        if r.status != ScrapeStatus.BLOCKED:
            break
        consecutive += 1
        platform = r.platform

    return ScrapeHealthOut(
        blocked=consecutive > 0,
        platform=platform.value if platform is not None else None,
        consecutive_blocked=consecutive,
        last_blocked_at=last_blocked_at,
        last_success_at=last_success_at,
    )


class TestScoreIn(BaseModel):
    """Sample text to push through the real Claude scoring step."""

    title: str | None = None
    body: str = Field(min_length=1)

    @field_validator("body")
    @classmethod
    def _body_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("body must not be blank")
        return value


class TestScoreOut(BaseModel):
    is_buyer: bool
    confidence: int  # 1-10
    reason: str
    model: str  # which model produced the assessment


def get_anthropic_client() -> AnthropicLike:
    """Build a real Anthropic client, or 503 if scoring is not configured.

    Injected as a dependency so tests can override it with a fake (no key, no
    network). ``anthropic`` is imported lazily so this module stays light.
    """
    if not (settings.anthropic_api_key and settings.anthropic_api_key.strip()):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Scoring is not configured: set ANTHROPIC_API_KEY and restart.",
        )
    from anthropic import Anthropic

    return Anthropic(api_key=settings.anthropic_api_key)


# A plain ``def`` route: score_post calls the blocking Anthropic SDK, so FastAPI
# runs it in a worker thread instead of stalling the event loop.
@router.post("/test-score", response_model=TestScoreOut)
def test_score(
    payload: TestScoreIn,
    current_user: User = Depends(get_current_user),
    client: AnthropicLike = Depends(get_anthropic_client),
) -> TestScoreOut:
    """Score one sample post end-to-end (no dedup, no storage, no notification).

    Lets an operator prove the Anthropic key actually works without waiting for a
    scrape or wiring up a collector.
    """
    decision = score_post(
        title=payload.title,
        body=payload.body,
        content_hash="diagnostic",  # cache is not passed, so this key is never used
        client=client,
        model=settings.claude_model,
    )
    s = decision.score
    return TestScoreOut(
        is_buyer=s.is_buyer,
        confidence=s.confidence,
        reason=s.reason,
        model=settings.claude_model,
    )