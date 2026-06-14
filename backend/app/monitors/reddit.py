"""Reddit collector: list a subreddit into RawPosts.

The pure, PRAW-free half of the Reddit monitor. Unlike the Facebook feed there is
no DOM, no scrolling, and no anti-ban pacing -- PRAW paginates a listing and the
official API rate-limits us, so this collector's only job is to dedup within the
run, cap the count, and convert each submission. The fragile network half lives
behind the :class:`SubmissionFeed` protocol (impl: :mod:`app.monitors.reddit_client`),
so this module never imports PRAW and is unit-tested on the local venv.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, ClassVar, Protocol

from app.models import Platform
from app.monitors.base import Monitor
from app.monitors.reddit_parser import to_raw_post
from app.schemas.raw_post import RawPost

# Overridden from Settings by the Celery factory, so this module stays free of the
# (Docker-only) config import and unit-testable.
DEFAULT_MAX_POSTS = 40


class SubmissionFeed(Protocol):
    """The listing operations RedditMonitor needs (impl: PRAW)."""

    def fetch(self) -> list[dict[str, Any]]:
        """Return recent submissions as plain dicts (newest first).

        May raise :class:`app.monitors.base.MonitorBlocked` when the subreddit is
        private/banned/forbidden, so run_monitor records BLOCKED.
        """
        ...

    def close(self) -> None:
        """Release any underlying client resources."""
        ...


class RedditMonitor(Monitor):
    """Collect a subreddit listing through an injected SubmissionFeed.

    ``collect`` reads one listing, dedups within the run by ``external_id``
    (defensive -- a listing can repeat a stickied post), converts each submission,
    and stops at ``max_posts``. A :class:`MonitorBlocked` from the feed propagates
    so run_monitor records BLOCKED and the account can be cooled down.
    """

    platform: ClassVar[Platform] = Platform.REDDIT

    def __init__(
        self,
        feed: SubmissionFeed,
        *,
        source_id: int | None = None,
        max_posts: int = DEFAULT_MAX_POSTS,
    ) -> None:
        super().__init__(source_id=source_id)
        self._feed = feed
        self._max_posts = max_posts

    def collect(self) -> Iterator[RawPost]:
        seen: set[str] = set()
        yielded = 0
        try:
            for element in self._feed.fetch():
                external_id = str(element.get("external_id") or "").strip()
                if not external_id or external_id in seen:
                    continue
                seen.add(external_id)
                post = to_raw_post(element, source_id=self.source_id)
                if post is None:
                    continue
                yield post
                yielded += 1
                if yielded >= self._max_posts:
                    return
        finally:
            self._feed.close()