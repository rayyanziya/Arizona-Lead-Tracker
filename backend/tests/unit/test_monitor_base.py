"""Unit tests for the monitor base: the Monitor contract + run_monitor telemetry.

A monitor is the thin collector half of the system -- it pulls posts from one
platform and yields them as RawPost; everything expensive (dedup, matching,
scoring, notifying) is downstream. run_monitor wraps one pass in a ScrapeRun row
so every scrape leaves an audit trail (count, success/blocked/error, finished_at)
even when the collector throws partway through.

Driver-free: in-memory SQLite + a fake monitor, no Playwright/PRAW/network. The
real browser collectors are exercised separately by browser/integration tests.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base, Platform, ScrapeRun, ScrapeStatus, Tenant
from app.monitors.base import Monitor, MonitorBlocked, run_monitor
from app.schemas.raw_post import RawPost

pytestmark = pytest.mark.unit


def _session(*tenant_ids: int) -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = Session(engine)
    for tid in tenant_ids or (1,):
        s.add(Tenant(id=tid, name=f"T{tid}", slug=f"t{tid}"))
    s.flush()
    return s


def _raw(n: int = 1, **overrides) -> RawPost:
    base = {
        "platform": "facebook",
        "external_id": f"p{n}",
        "url": f"http://x/p{n}",
        "body": f"butuh aplikasi kasir {n}",
    }
    base.update(overrides)
    return RawPost(**base)


class _Clock:
    """Deterministic clock: each call returns a time one second later."""

    def __init__(self) -> None:
        self._t = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        now = self._t
        self._t = self._t + timedelta(seconds=1)
        return now


class _FakeMonitor(Monitor):
    """Yields a fixed list, optionally raising at an index. Records progress."""

    platform = Platform.FACEBOOK

    def __init__(self, posts, *, source_id=None, raise_at=None, exc=None) -> None:
        super().__init__(source_id=source_id)
        self._posts = posts
        self._raise_at = raise_at
        self._exc = exc
        self.yielded = 0

    def collect(self) -> Iterator[RawPost]:
        for i, post in enumerate(self._posts):
            if self._raise_at is not None and i == self._raise_at:
                raise self._exc
            self.yielded += 1
            yield post


class TestMonitorContract:
    def test_cannot_instantiate_the_abstract_base(self):
        with pytest.raises(TypeError):
            Monitor(source_id=1)

    def test_subclass_without_platform_is_rejected(self):
        with pytest.raises(TypeError):

            class _NoPlatform(Monitor):
                def collect(self) -> Iterator[RawPost]:
                    yield from ()

    def test_source_id_is_retained(self):
        assert _FakeMonitor([], source_id=7).source_id == 7


class TestRunMonitorSuccess:
    def test_records_success_with_count_and_provenance(self):
        s = _session()
        run = run_monitor(
            s, 1, _FakeMonitor([_raw(1), _raw(2), _raw(3)], source_id=5),
            on_post=lambda p: None, now=_Clock(),
        )
        assert isinstance(run, ScrapeRun)
        assert run.id is not None
        assert run.status is ScrapeStatus.SUCCESS
        assert run.posts_collected == 3
        assert run.tenant_id == 1
        assert run.source_id == 5
        assert run.platform is Platform.FACEBOOK
        assert run.error is None
        assert run.finished_at is not None

    def test_every_post_is_handed_to_on_post_in_order(self):
        s = _session()
        seen: list[RawPost] = []
        run_monitor(
            s, 1, _FakeMonitor([_raw(1), _raw(2), _raw(3)]), on_post=seen.append, now=_Clock()
        )
        assert [p.external_id for p in seen] == ["p1", "p2", "p3"]

    def test_empty_collection_is_a_clean_success(self):
        s = _session()
        run = run_monitor(s, 1, _FakeMonitor([]), on_post=lambda p: None, now=_Clock())
        assert run.status is ScrapeStatus.SUCCESS
        assert run.posts_collected == 0
        assert run.finished_at is not None

    def test_run_row_is_persisted(self):
        s = _session()
        run = run_monitor(s, 1, _FakeMonitor([_raw(1)]), now=_Clock())
        assert s.get(ScrapeRun, run.id) is not None

    def test_finished_at_is_not_before_started_at(self):
        s = _session()
        run = run_monitor(s, 1, _FakeMonitor([_raw(1)]), now=_Clock())
        assert run.finished_at >= run.started_at

    def test_on_post_is_optional(self):
        s = _session()
        run = run_monitor(s, 1, _FakeMonitor([_raw(1)]), now=_Clock())
        assert run.posts_collected == 1


class TestMaxPostsCap:
    def test_cap_limits_count_and_stops_pulling_the_generator(self):
        s = _session()
        m = _FakeMonitor([_raw(i) for i in range(100)])
        seen: list[RawPost] = []
        run = run_monitor(s, 1, m, on_post=seen.append, max_posts=5, now=_Clock())
        assert run.posts_collected == 5
        assert len(seen) == 5
        assert m.yielded == 5  # did not over-scrape past the cap
        assert run.status is ScrapeStatus.SUCCESS

    def test_without_a_cap_everything_is_collected(self):
        s = _session()
        run = run_monitor(s, 1, _FakeMonitor([_raw(i) for i in range(10)]), now=_Clock())
        assert run.posts_collected == 10


class TestRunMonitorFailure:
    def test_blocked_collector_is_recorded_as_blocked(self):
        s = _session()
        m = _FakeMonitor([_raw(1), _raw(2)], raise_at=1, exc=MonitorBlocked("checkpoint hit"))
        run = run_monitor(s, 1, m, on_post=lambda p: None, now=_Clock())
        assert run.status is ScrapeStatus.BLOCKED
        assert "checkpoint" in (run.error or "")
        assert run.posts_collected == 1
        assert run.finished_at is not None

    def test_generic_error_is_recorded_as_error(self):
        s = _session()
        m = _FakeMonitor([_raw(1), _raw(2)], raise_at=1, exc=RuntimeError("boom"))
        run = run_monitor(s, 1, m, on_post=lambda p: None, now=_Clock())
        assert run.status is ScrapeStatus.ERROR
        assert "boom" in (run.error or "")
        assert run.posts_collected == 1

    def test_collector_failure_is_not_re_raised(self):
        s = _session()
        m = _FakeMonitor([_raw(1)], raise_at=0, exc=RuntimeError("immediate"))
        run = run_monitor(s, 1, m, on_post=lambda p: None, now=_Clock())
        assert run.status is ScrapeStatus.ERROR
        assert run.posts_collected == 0

    def test_on_post_failure_marks_the_run_error(self):
        s = _session()

        def boom(_: RawPost) -> None:
            raise RuntimeError("pipeline exploded")

        run = run_monitor(s, 1, _FakeMonitor([_raw(1)]), on_post=boom, now=_Clock())
        assert run.status is ScrapeStatus.ERROR
        assert "exploded" in (run.error or "")