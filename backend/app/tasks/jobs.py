"""Celery task wrappers: thin shells that build real dependencies (DB session,
Redis caches, Anthropic client, channel senders) and call the Celery-free
orchestration in app.tasks.pipeline / app.tasks.notify.

Not unit-tested -- requires Celery + Redis + network, so it is excluded from
coverage and verified by the Phase 3 end-to-end run. The testable logic all lives
upstream; these functions only wire it to the outside world.
"""

from __future__ import annotations

import smtplib
from email.message import EmailMessage

import httpx
from anthropic import Anthropic
from celery.utils.log import get_task_logger
from redis import Redis
from sqlalchemy import select

from app.core.config import settings
from app.core.database import session_scope
from app.models import Keyword as KeywordRow
from app.models import MonitoredSource, Platform
from app.schemas.raw_post import RawPost
from app.services.keyword_matcher import Keyword
from app.tasks.celery_app import app
from app.tasks.notify import build_senders, deliver
from app.tasks.pipeline import process_post

logger = get_task_logger(__name__)


# --- dependency adapters / builders ----------------------------------------
class RedisCache:
    """Adapts a redis-py client to the dedup/score cache Protocol (str values)."""

    def __init__(self, client: Redis):
        self._client = client

    def get(self, key: str) -> str | None:
        value = self._client.get(key)
        return value.decode() if isinstance(value, bytes | bytearray) else value

    def set(self, key: str, value: str, ttl_seconds: int) -> None:
        self._client.set(key, value, ex=ttl_seconds)


class SmtpSender:
    """Opens a short-lived SMTP connection per message (EmailSender Protocol)."""

    def send_message(self, message: EmailMessage) -> None:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
            if settings.smtp_use_tls:
                smtp.starttls()
            if settings.smtp_user:
                smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(message)


def _redis() -> Redis:
    return Redis.from_url(settings.redis_url)


def _anthropic() -> Anthropic:
    return Anthropic(api_key=settings.anthropic_api_key)


def _senders():
    return build_senders(
        telegram_token=settings.telegram_bot_token,
        telegram_default_chat=settings.telegram_default_chat_id,
        http=httpx.Client(timeout=15.0),
        smtp_sender=SmtpSender(),
        smtp_from=settings.smtp_from,
    )


def _load_keywords(session, tenant_id: int) -> list[Keyword]:
    rows = session.execute(
        select(KeywordRow).where(
            KeywordRow.tenant_id == tenant_id,
            KeywordRow.is_active.is_(True),
        )
    ).scalars()
    return [
        Keyword(term=r.term, language=r.language.value, match_type=r.match_type, id=r.id)
        for r in rows
    ]


# --- tasks ------------------------------------------------------------------
@app.task(name="app.tasks.jobs.process_post_task", bind=True, max_retries=3, default_retry_delay=30)
def process_post_task(self, tenant_id: int, raw_payload: dict) -> str:
    raw = RawPost(**raw_payload)
    redis = _redis()
    cache = RedisCache(redis)
    to_notify: list[int] = []
    try:
        with session_scope() as session:
            keywords = _load_keywords(session, tenant_id)
            result = process_post(
                session,
                tenant_id,
                raw,
                keywords=keywords,
                client=_anthropic(),
                dedup_cache=cache,
                score_cache=cache,
                enqueue=to_notify.append,
                model=settings.claude_model,
            )
    except Exception as exc:
        raise self.retry(exc=exc) from exc
    # Enqueue only after the transaction commits, so the notify worker can find
    # the freshly-created Notification rows.
    for notification_id in to_notify:
        deliver_task.delay(notification_id)
    logger.info("processed post tenant=%s status=%s", tenant_id, result.status.value)
    return result.status.value


@app.task(name="app.tasks.jobs.deliver_task", bind=True, max_retries=5, default_retry_delay=30)
def deliver_task(self, notification_id: int) -> bool:
    with session_scope() as session:
        outcome = deliver(session, notification_id, senders=_senders())
    if not outcome.ok:
        raise self.retry(exc=RuntimeError(outcome.detail))
    return True


@app.task(name="app.tasks.jobs.dispatch_due_sources")
def dispatch_due_sources() -> int:
    """Beat entrypoint: one scrape per active source, routed by platform.

    Phase 3 registers the platform scrape tasks (scrape_reddit_source /
    scrape_browser_source) and starts the reddit/browser workers; until then this
    only logs intent, so beat is harmless before monitors exist.
    """
    with session_scope() as session:
        sources = (
            session.execute(select(MonitoredSource).where(MonitoredSource.is_active.is_(True)))
            .scalars()
            .all()
        )
    for source in sources:
        queue = "reddit" if source.platform is Platform.REDDIT else "browser"
        logger.info(
            "would dispatch scrape: source=%s platform=%s queue=%s",
            source.id,
            source.platform.value,
            queue,
        )
        # Phase 3 wires app.send_task(scrape_<queue>_source, args=[source.id], queue=queue).
    return len(sources)