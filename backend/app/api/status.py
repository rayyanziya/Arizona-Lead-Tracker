"""Config-status endpoint: what the system is actually wired to do.

Returns read-only capability booleans (never secrets) so the dashboard can
explain why leads may not be flowing -- a missing Anthropic key, missing Reddit
credentials, or no captured Facebook session. Auth-gated like the rest of the API.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.api.deps import get_current_user
from app.core.config import settings
from app.models import User
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