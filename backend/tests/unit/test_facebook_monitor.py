"""Unit tests for the FacebookMonitor scroll/pace/dedup collection brain.

Driver-free: a fake FeedDriver stands in for Playwright, sleep is captured, and
the rng is seeded, so the pacing logic is exercised deterministically on the
local venv (no browser, no network).
"""

from __future__ import annotations

import random

import pytest

from app.models import Platform
from app.monitors.base import MonitorBlocked
from app.monitors.facebook import FacebookMonitor


def _post(
    external_id, *, text="want to buy custom ERP", permalink=None, timestamp=1_700_000_000
):
    el = {"external_id": external_id, "text": text, "author": "Budi", "timestamp": timestamp}
    if permalink is not None:
        el["permalink"] = permalink
    return el


class _FakeDriver:
    """Returns successive read_posts() batches; counts scrolls; flags blocked."""

    def __init__(self, batches, *, blocked_at_scroll=None):
        self._batches = [list(b) for b in batches]
        self._reads = 0
        self.scrolls = 0
        self.closed = False
        self._blocked_at_scroll = blocked_at_scroll

    def read_posts(self):
        batch = self._batches[self._reads] if self._reads < len(self._batches) else []
        self._reads += 1
        return batch

    def scroll(self):
        self.scrolls += 1

    def is_blocked(self):
        return self._blocked_at_scroll is not None and self.scrolls >= self._blocked_at_scroll

    def close(self):
        self.closed = True


def _monitor(driver, **kwargs):
    kwargs.setdefault("group_id", "777")
    kwargs.setdefault("rng", random.Random(0))
    kwargs.setdefault("sleep", lambda _seconds: None)
    return FacebookMonitor(driver, **kwargs)


class TestContract:
    def test_platform_is_facebook(self):
        assert FacebookMonitor.platform is Platform.FACEBOOK

    def test_source_id_flows_onto_posts(self):
        driver = _FakeDriver([[_post("1")]])
        monitor = _monitor(driver, source_id=42)
        posts = list(monitor.collect())
        assert posts[0].source_id == 42
        assert posts[0].platform is Platform.FACEBOOK


class TestCollect:
    def test_yields_each_post_in_feed(self):
        driver = _FakeDriver([[_post("1"), _post("2"), _post("3")]])
        posts = list(_monitor(driver).collect())
        assert [p.external_id for p in posts] == ["1", "2", "3"]
        assert posts[0].url == "https://www.facebook.com/groups/777/posts/1/"

    def test_dedupes_repeated_posts_across_scrolls(self):
        driver = _FakeDriver(
            [[_post("a"), _post("b")], [_post("b"), _post("c")], [_post("c")]]
        )
        posts = list(_monitor(driver).collect())
        assert [p.external_id for p in posts] == ["a", "b", "c"]

    def test_stops_after_max_empty_scrolls(self):
        driver = _FakeDriver([[_post("a")]])  # then empty forever
        posts = list(_monitor(driver, max_empty_scrolls=2).collect())
        assert [p.external_id for p in posts] == ["a"]
        assert driver.scrolls == 2  # two empty reads after the productive one
        assert driver.closed is True

    def test_respects_max_posts_cap(self):
        driver = _FakeDriver([[_post(str(n)) for n in range(5)]])
        posts = list(_monitor(driver, max_posts=3).collect())
        assert len(posts) == 3
        assert driver.scrolls == 0  # cap hit mid-batch, before any scroll
        assert driver.closed is True

    def test_skips_unusable_elements(self):
        # no external_id -> skipped before to_raw_post;
        # blank external_id -> skipped;
        # id but no derivable url (no permalink, group_id=None) -> to_raw_post drops it;
        # valid element with a permalink -> kept.
        driver = _FakeDriver(
            [
                [
                    {"text": "no id"},
                    _post("", permalink="/groups/777/posts/9/"),
                    _post("8"),
                    _post("7", permalink="/groups/777/posts/7/"),
                ]
            ]
        )
        posts = list(_monitor(driver, group_id=None).collect())
        assert [p.external_id for p in posts] == ["7"]

    def test_blocked_feed_raises_and_closes(self):
        driver = _FakeDriver([[_post("1")]], blocked_at_scroll=0)
        monitor = _monitor(driver)
        with pytest.raises(MonitorBlocked):
            list(monitor.collect())
        assert driver.closed is True


class TestPacing:
    def test_sleeps_within_configured_bounds(self):
        delays = []
        driver = _FakeDriver([[_post("a")]])
        monitor = _monitor(
            driver,
            min_delay_ms=1000,
            max_delay_ms=2000,
            max_empty_scrolls=2,
            sleep=delays.append,
        )
        list(monitor.collect())
        assert delays  # it paced at least once
        assert all(1.0 <= d <= 2.0 for d in delays)

    def test_default_rng_used_when_none_given(self):
        driver = _FakeDriver([[_post("a")]])
        delays = []
        monitor = FacebookMonitor(
            driver,
            group_id="777",
            min_delay_ms=500,
            max_delay_ms=600,
            max_empty_scrolls=2,
            sleep=delays.append,
        )  # no rng -> constructs random.Random()
        list(monitor.collect())
        assert all(0.5 <= d <= 0.6 for d in delays)