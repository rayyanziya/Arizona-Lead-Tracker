"""Celery task wrappers: thin shells that build real dependencies (DB session,
Redis caches, Anthropic client, channel senders) and call the Celery-free
orchestration in app.tasks.pipeline / app.tasks.notify.

Not unit-tested -- requires Celery + Redis + network, so it is excluded from
coverage and verified by the Phase 3 end-to-end run. The testable logic all lives
upstream; these functions only wire it to the outside world.
"""

from __future__ import annotations

import re
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


def _group_id_from_url(url: str) -> str | None:
    """Pull the group id/slug out of a .../groups/<id>/... URL."""
    match = re.search(r"/groups/([^/?#]+)", url or "")
    return match.group(1) if match else None


def _subreddit_from_identifier(identifier: str) -> str | None:
    """Pull the subreddit name from a URL, an ``r/<name>``, or a bare ``<name>``."""
    text = (identifier or "").strip()
    if not text:
        return None
    match = re.search(r"(?:^|/)r/([A-Za-z0-9_]+)", text)
    if match:
        return match.group(1)
    if "/" not in text and re.fullmatch(r"[A-Za-z0-9_]+", text):
        return text
    return None


def _browser_proxy() -> dict | None:
    """Build a Playwright proxy dict from settings, or None when unconfigured."""
    if not settings.browser_proxy_server:
        return None
    proxy: dict = {"server": settings.browser_proxy_server}
    if settings.browser_proxy_username:
        proxy["username"] = settings.browser_proxy_username
        proxy["password"] = settings.browser_proxy_password
    return proxy


@app.task(
    name="app.tasks.jobs.scrape_browser_source",
    bind=True,
    max_retries=1,
    default_retry_delay=120,
)
def scrape_browser_source(self, source_id: int) -> int:
    """Scrape one Facebook source via Playwright, enqueuing each post to the pipeline.

    Builds a PlaywrightFeedDriver + FacebookMonitor from Settings and runs it through
    run_monitor, which records a ScrapeRun and hands each RawPost to on_post -- here,
    process_post_task. Heavy imports are deferred so beat/light workers never load
    Playwright.
    """
    from app.monitors.base import run_monitor
    from app.monitors.facebook import FacebookMonitor
    from app.monitors.facebook_browser import PlaywrightFeedDriver

    with session_scope() as session:
        source = session.get(MonitoredSource, source_id)
        if source is None or not source.is_active:
            logger.warning("scrape_browser_source: source %s missing or inactive", source_id)
            return 0
        tenant_id = source.tenant_id
        driver = PlaywrightFeedDriver(
            source.identifier,
            session_dir=settings.browser_session_dir,
            headless=settings.browser_headless,
            locale=settings.browser_locale,
            timezone=settings.browser_timezone,
            proxy=_browser_proxy(),
        )
        monitor = FacebookMonitor(
            driver,
            group_id=_group_id_from_url(source.identifier),
            source_id=source.id,
            max_posts=settings.scrape_max_posts_per_run,
            min_delay_ms=settings.scrape_min_delay_ms,
            max_delay_ms=settings.scrape_max_delay_ms,
        )
        run = run_monitor(
            session,
            tenant_id,
            monitor,
            on_post=lambda raw: process_post_task.delay(tenant_id, raw.model_dump(mode="json")),
            max_posts=settings.scrape_max_posts_per_run,
        )
        collected = run.posts_collected
    logger.info("scrape_browser_source: source=%s collected=%s", source_id, collected)
    return collected


@app.task(
    name="app.tasks.jobs.scrape_reddit_source",
    bind=True,
    max_retries=2,
    default_retry_delay=120,
)
def scrape_reddit_source(self, source_id: int) -> int:
    """Scrape one Reddit source via PRAW, enqueuing each submission to the pipeline.

    Builds a PrawSubmissionFeed + RedditMonitor from Settings and runs it through
    run_monitor, which records a ScrapeRun and hands each RawPost to on_post -- here,
    process_post_task. Runs on the light worker's ``reddit`` queue (no browser), so
    PRAW is imported lazily and never loaded by beat or the browser worker.
    """
    from app.monitors.base import run_monitor
    from app.monitors.reddit import RedditMonitor
    from app.monitors.reddit_client import PrawSubmissionFeed

    with session_scope() as session:
        source = session.get(MonitoredSource, source_id)
        if source is None or not source.is_active:
            logger.warning("scrape_reddit_source: source %s missing or inactive", source_id)
            return 0
        subreddit = _subreddit_from_identifier(source.identifier)
        if subreddit is None:
            logger.warning(
                "scrape_reddit_source: no subreddit in source=%s identifier=%r",
                source_id,
                source.identifier,
            )
            return 0
        tenant_id = source.tenant_id
        feed = PrawSubmissionFeed(
            subreddit,
            client_id=settings.reddit_client_id,
            client_secret=settings.reddit_client_secret,
            user_agent=settings.reddit_user_agent,
            limit=settings.scrape_max_posts_per_run,
        )
        monitor = RedditMonitor(
            feed,
            source_id=source.id,
            max_posts=settings.scrape_max_posts_per_run,
        )
        run = run_monitor(
            session,
            tenant_id,
            monitor,
            on_post=lambda raw: process_post_task.delay(tenant_id, raw.model_dump(mode="json")),
            max_posts=settings.scrape_max_posts_per_run,
        )
        collected = run.posts_collected
    logger.info("scrape_reddit_source: source=%s collected=%s", source_id, collected)
    return collected


@app.task(name="app.tasks.jobs.dispatch_due_sources")
def dispatch_due_sources() -> int:
    """Beat entrypoint: enqueue one scrape per active source, routed by platform.

    Facebook sources go to the browser queue (scrape_browser_source); Reddit sources
    go to the reddit queue (scrape_reddit_source). X (Phase 5) has no collector yet, so
    those sources are logged and skipped rather than dispatched into a queue no worker
    serves.
    """
    with session_scope() as session:
        sources = (
            session.execute(select(MonitoredSource).where(MonitoredSource.is_active.is_(True)))
            .scalars()
            .all()
        )
        pending = [(s.id, s.platform) for s in sources]
    dispatched = 0
    for source_id, platform in pending:
        if platform is Platform.FACEBOOK:
            app.send_task(
                "app.tasks.jobs.scrape_browser_source", args=[source_id], queue="browser"
            )
            dispatched += 1
        elif platform is Platform.REDDIT:
            app.send_task(
                "app.tasks.jobs.scrape_reddit_source", args=[source_id], queue="reddit"
            )
            dispatched += 1
        else:
            logger.info(
                "skipping source=%s platform=%s (collector not implemented yet)",
                source_id,
                platform.value,
            )
    return dispatched