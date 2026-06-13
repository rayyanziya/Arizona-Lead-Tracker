"""The processing pipeline: one collected post -> stored, scored, and notified.

This is the single place all the expensive/fragile logic converges (collectors
stay thin). For one RawPost it runs:

    dedup -> keyword match -> Claude score -> persist Match -> enqueue notify

Each stage can short-circuit: a duplicate is never scored (no tokens spent), an
unmatched post is stored but gets no Match, and a low score is recorded without a
notification. Notifications are created PENDING and their ids handed to
``enqueue`` (the Celery notify task in production); the actual send happens in
app.tasks.notify so a slow or blocked channel never stalls scoring.

Pure and injectable: the session, the Anthropic client, the caches, and
``enqueue`` are all passed in, so this unit-tests against SQLite + fakes with no
Celery, Redis, or network.
"""

from __future__ import annotations

import enum
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Match, MatchStatus, Notification, NotificationSetting
from app.schemas.raw_post import RawPost
from app.services import dedup
from app.services.keyword_matcher import Keyword, match_keywords
from app.services.scoring import (
    DEFAULT_MODEL,
    DEFAULT_THRESHOLD,
    AnthropicLike,
    Score,
    ScoreCache,
    decide,
    score_post,
)


class PipelineStatus(str, enum.Enum):
    DEDUPED = "deduped"    # already seen; nothing to do
    NO_MATCH = "no_match"  # stored, but no keyword hit
    SCORED = "scored"      # matched + scored, below every channel threshold
    NOTIFIED = "notified"  # at least one channel alerted


@dataclass(frozen=True)
class PipelineResult:
    status: PipelineStatus
    post_id: int | None = None
    match_id: int | None = None
    score: int | None = None
    is_buyer: bool | None = None
    notified_channels: tuple[str, ...] = ()


def process_post(
    session: Session,
    tenant_id: int,
    raw: RawPost,
    *,
    keywords: list[Keyword],
    client: AnthropicLike,
    dedup_cache=None,
    score_cache: ScoreCache | None = None,
    enqueue: Callable[[int], None] | None = None,
    model: str = DEFAULT_MODEL,
) -> PipelineResult:
    """Run one post through the pipeline. The caller owns the transaction."""
    # 1. Dedup -- a non-new post stops here, before any token spend.
    dd = dedup.register(session, tenant_id, raw, cache=dedup_cache)
    if not dd.is_new:
        return PipelineResult(PipelineStatus.DEDUPED, post_id=_post_id(dd.post))
    post = dd.post

    # 2. Keyword match -- a stored-but-unmatched post is not worth scoring.
    text = f"{raw.title or ''}\n{raw.body}"
    hits = match_keywords(text, keywords)
    if not hits:
        return PipelineResult(PipelineStatus.NO_MATCH, post_id=post.id)

    # 3. Score (cached by content_hash). The floor only feeds the cache call; the
    #    real gate is per-channel below.
    settings = _enabled_settings(session, tenant_id)
    floor = min((s.min_score for s in settings), default=DEFAULT_THRESHOLD)
    decision = score_post(
        title=raw.title,
        body=raw.body,
        content_hash=raw.content_hash,
        threshold=floor,
        client=client,
        cache=score_cache,
        model=model,
    )
    score = decision.score

    # 4. Persist the Match (the lead record), whatever the notification outcome.
    match = Match(
        tenant_id=tenant_id,
        post_id=post.id,
        keyword_id=_first_keyword_id(hits),
        matched_term=hits[0].keyword.term,
        matched_terms=sorted({h.matched_text for h in hits}),
        ai_score=score.confidence,
        ai_is_buyer=score.is_buyer,
        ai_reason=score.reason,
        status=MatchStatus.PENDING,
    )
    session.add(match)
    session.flush()

    # 5. Per-channel notify: create a PENDING Notification (idempotent) + enqueue.
    notified = _create_notifications(session, tenant_id, match.id, score, settings, enqueue)
    if notified:
        match.status = MatchStatus.NOTIFIED
        session.flush()
        status = PipelineStatus.NOTIFIED
    else:
        status = PipelineStatus.SCORED

    return PipelineResult(
        status,
        post_id=post.id,
        match_id=match.id,
        score=score.confidence,
        is_buyer=score.is_buyer,
        notified_channels=tuple(notified),
    )


def _enabled_settings(session: Session, tenant_id: int) -> list[NotificationSetting]:
    return list(
        session.execute(
            select(NotificationSetting).where(
                NotificationSetting.tenant_id == tenant_id,
                NotificationSetting.is_enabled.is_(True),
            )
        ).scalars()
    )


def _create_notifications(
    session: Session,
    tenant_id: int,
    match_id: int,
    score: Score,
    settings: list[NotificationSetting],
    enqueue: Callable[[int], None] | None,
) -> list[str]:
    notified: list[str] = []
    for setting in settings:
        if not decide(score, setting.min_score):
            continue
        target = (setting.config or {}).get("target")
        notif = _get_or_create_notification(session, tenant_id, match_id, setting.channel, target)
        if notif is None:
            continue  # already existed -> idempotent, do not re-enqueue
        notified.append(setting.channel.value)
        if enqueue is not None:
            enqueue(notif.id)
    return notified


def _get_or_create_notification(
    session: Session, tenant_id: int, match_id: int, channel, target: str | None
) -> Notification | None:
    existing = session.execute(
        select(Notification).where(
            Notification.tenant_id == tenant_id,
            Notification.match_id == match_id,
            Notification.channel == channel,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return None
    notif = Notification(tenant_id=tenant_id, match_id=match_id, channel=channel, target=target)
    try:
        with session.begin_nested():
            session.add(notif)
            session.flush()
    except IntegrityError:  # pragma: no cover - a concurrent producer won the unique
        return None
    return notif


def _post_id(post) -> int | None:
    return post.id if post is not None else None


def _first_keyword_id(hits) -> int | None:
    for hit in hits:
        if hit.keyword.id is not None:
            return hit.keyword.id
    return None