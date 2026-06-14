"""Unit tests for RedditMonitor: dedup + cap over an injected submission feed.

Driver-free: a fake feed stands in for PRAW so the collection brain (in-run dedup
by external_id, the per-run cap, feed teardown, block propagation) runs on the
local venv with no network.
"""

from __future__ import annotations

import pytest

from app.models import Platform
from app.monitors.base import MonitorBlocked
from app.monitors.reddit import RedditMonitor


def _sub(external_id, *, title="Need a contractor", selftext="building an ADU"):
    return {
        "external_id": external_id,
        "title": title,
        "selftext": selftext,
        "author": "phx_user",
        "permalink": f"/r/Phoenix/comments/{external_id}/t/",
        "created_utc": 1_700_000_000,
    }


class _FakeFeed:
    """Returns a fixed submission batch; can raise; records teardown."""

    def __init__(self, submissions, *, raises=None):
        self._submissions = list(submissions)
        self._raises = raises
        self.closed = False

    def fetch(self):
        if self._raises is not None:
            raise self._raises
        return list(self._submissions)

    def close(self):
        self.closed = True


class TestContract:
    def test_platform_is_reddit(self):
        assert RedditMonitor.platform is Platform.REDDIT

    def test_source_id_flows_onto_posts(self):
        monitor = RedditMonitor(_FakeFeed([_sub("1")]), source_id=42)
        posts = list(monitor.collect())
        assert posts[0].source_id == 42
        assert posts[0].platform is Platform.REDDIT


class TestCollect:
    def test_yields_each_submission(self):
        monitor = RedditMonitor(_FakeFeed([_sub("1"), _sub("2"), _sub("3")]))
        assert [p.external_id for p in monitor.collect()] == ["1", "2", "3"]

    def test_dedupes_by_external_id(self):
        monitor = RedditMonitor(_FakeFeed([_sub("a"), _sub("a"), _sub("b")]))
        assert [p.external_id for p in monitor.collect()] == ["a", "b"]

    def test_respects_max_posts_cap(self):
        feed = _FakeFeed([_sub(str(n)) for n in range(5)])
        monitor = RedditMonitor(feed, max_posts=3)
        assert len(list(monitor.collect())) == 3

    def test_skips_unusable_submissions(self):
        monitor = RedditMonitor(_FakeFeed([{"title": "no id"}, _sub("7")]))
        assert [p.external_id for p in monitor.collect()] == ["7"]

    def test_closes_feed_when_done(self):
        feed = _FakeFeed([_sub("1")])
        list(RedditMonitor(feed).collect())
        assert feed.closed is True

    def test_blocked_feed_raises_and_closes(self):
        feed = _FakeFeed([], raises=MonitorBlocked("private subreddit"))
        with pytest.raises(MonitorBlocked):
            list(RedditMonitor(feed).collect())
        assert feed.closed is True