"""The monitor base: the collector contract and a telemetry-wrapped runner.

A monitor is the *thin* half of the system. Each platform (Facebook, Reddit, X)
implements one job -- pull raw posts and yield them as RawPost -- and nothing
else; every expensive or fragile step (dedup, keyword match, Claude scoring,
notify) lives downstream in the shared pipeline. Thin collectors are what let the
pipeline be tested once and trusted for all three platforms.

:func:`run_monitor` wraps a single execution in a :class:`ScrapeRun` row, so every
scrape leaves an audit trail: how many posts it collected, whether it succeeded,
was blocked (anti-ban tripped), or errored, and when it finished. The row is
created RUNNING *before* collection starts, so even a hard crash leaves evidence,
and the collector's exceptions are recorded rather than propagated -- the row is
the failure signal and one bad scrape must never take down the worker.

Driver-free by construction (SQLAlchemy + stdlib only): the concrete monitors own
Playwright/PRAW, so this module and its tests run on the local venv vs SQLite.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import ClassVar

from sqlalchemy.orm import Session

from app.models import Platform, ScrapeRun, ScrapeStatus
from app.schemas.raw_post import RawPost


class MonitorError(Exception):
    """Base for collector failures surfaced through ScrapeRun telemetry."""


class MonitorBlocked(MonitorError):
    """The platform blocked the collector (checkpoint, login wall, rate limit).

    Recorded as ``ScrapeStatus.BLOCKED`` so the caller can cool the scraping
    account down instead of treating it as a generic, retry-soon error.
    """


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Monitor(ABC):
    """The collector contract: declare a ``platform``, ``collect`` RawPosts.

    Subclasses set the ``platform`` class attribute and implement ``collect``.
    Forgetting ``platform`` is an error at definition time, because run_monitor
    stamps it onto every ScrapeRun (and, downstream, every Post).
    """

    platform: ClassVar[Platform]

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        if not hasattr(cls, "platform"):
            raise TypeError(f"{cls.__name__} must set a class-level `platform`")

    def __init__(self, *, source_id: int | None = None) -> None:
        self.source_id = source_id

    @abstractmethod
    def collect(self) -> Iterator[RawPost]:
        """Yield posts from the source, newest first.

        Implementations may raise :class:`MonitorBlocked` when the platform
        blocks them; any other exception is recorded as a generic error.
        """
        raise NotImplementedError


def run_monitor(
    session: Session,
    tenant_id: int,
    monitor: Monitor,
    *,
    on_post: Callable[[RawPost], None] | None = None,
    max_posts: int | None = None,
    now: Callable[[], datetime] = _utcnow,
) -> ScrapeRun:
    """Run one monitor pass, recording a ScrapeRun. The caller owns the transaction.

    Each collected post is handed to ``on_post`` (in production: enqueue the
    pipeline task). Collection stops after ``max_posts`` if given, so an
    over-eager feed cannot blow past the per-run cap. Any collector or dispatch
    exception is captured on the row -- ``BLOCKED`` for :class:`MonitorBlocked`,
    ``ERROR`` otherwise -- and swallowed, because the row is the signal and the
    worker must survive a single bad scrape.
    """
    run = ScrapeRun(
        tenant_id=tenant_id,
        source_id=monitor.source_id,
        platform=monitor.platform,
        status=ScrapeStatus.RUNNING,
        started_at=now(),
    )
    session.add(run)
    session.flush()  # persist the RUNNING row (and assign an id) before collecting

    collected = 0
    try:
        for raw in monitor.collect():
            if on_post is not None:
                on_post(raw)
            collected += 1
            if max_posts is not None and collected >= max_posts:
                break
        run.status = ScrapeStatus.SUCCESS
    except MonitorBlocked as exc:
        run.status = ScrapeStatus.BLOCKED
        run.error = str(exc)
    except Exception as exc:
        run.status = ScrapeStatus.ERROR
        run.error = str(exc)
    finally:
        run.posts_collected = collected
        run.finished_at = now()
        session.flush()

    return run