"""Unit tests for XMonitor: dedup + cap over an injected tweet feed.

Driver-free: a fake feed stands in for the X API client so the collection brain
(in-run dedup by external_id, the per-run cap, feed teardown, block propagation)
runs on the local venv with no network. Mirrors test_reddit_monitor.
"""

from __future__ import annotations

import pytest

from app.models import Platform
from app.monitors.base import MonitorBlocked
from app.monitors.x import XMonitor


def _tweet(external_id, *, text="Looking for a developer to build our POS app"):
    return {
        "external_id": external_id,
        "text": text,
        "author": "phx_buyer",
        "created_at": "2026-06-15T12:00:00.000Z",
    }


class _FakeFeed:
    """Returns a fixed tweet batch; can raise; records teardown."""

    def __init__(self, tweets, *, raises=None):
        self._tweets = list(tweets)
        self._raises = raises
        self.closed = False

    def fetch(self):
        if self._raises is not None:
            raise self._raises
        return list(self._tweets)

    def close(self):
        self.closed = True


class TestContract:
    def test_platform_is_x(self):
        assert XMonitor.platform is Platform.X

    def test_source_id_flows_onto_posts(self):
        monitor = XMonitor(_FakeFeed([_tweet("1")]), source_id=42)
        posts = list(monitor.collect())
        assert posts[0].source_id == 42
        assert posts[0].platform is Platform.X


class TestCollect:
    def test_yields_each_tweet(self):
        monitor = XMonitor(_FakeFeed([_tweet("1"), _tweet("2"), _tweet("3")]))
        assert [p.external_id for p in monitor.collect()] == ["1", "2", "3"]

    def test_dedupes_by_external_id(self):
        monitor = XMonitor(_FakeFeed([_tweet("a"), _tweet("a"), _tweet("b")]))
        assert [p.external_id for p in monitor.collect()] == ["a", "b"]

    def test_respects_max_posts_cap(self):
        feed = _FakeFeed([_tweet(str(n)) for n in range(5)])
        monitor = XMonitor(feed, max_posts=3)
        assert len(list(monitor.collect())) == 3

    def test_skips_unusable_tweets(self):
        monitor = XMonitor(_FakeFeed([{"text": "no id"}, _tweet("7")]))
        assert [p.external_id for p in monitor.collect()] == ["7"]

    def test_closes_feed_when_done(self):
        feed = _FakeFeed([_tweet("1")])
        list(XMonitor(feed).collect())
        assert feed.closed is True

    def test_blocked_feed_raises_and_closes(self):
        feed = _FakeFeed([], raises=MonitorBlocked("rate limited"))
        with pytest.raises(MonitorBlocked):
            list(XMonitor(feed).collect())
        assert feed.closed is True
