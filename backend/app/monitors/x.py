"""X (Twitter) collector: a tweet search/timeline into RawPosts.

The pure, API-client-free half of the X monitor. Like the Reddit collector there
is no DOM and no anti-ban pacing -- the official API paginates and rate-limits us
-- so this collector's only job is to dedup within the run, cap the count, and
convert each tweet. The fragile network half lives behind the :class:`TweetFeed`
protocol (impl: :mod:`app.monitors.x_client`), so this module never imports an API
client and is unit-tested on the local venv. Mirrors :class:`RedditMonitor`.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, ClassVar, Protocol

from app.models import Platform
from app.monitors.base import Monitor
from app.monitors.x_parser import to_raw_post
from app.schemas.raw_post import RawPost

# Overridden from Settings by the Celery factory, so this module stays free of the
# (Docker-only) config import and unit-testable.
DEFAULT_MAX_POSTS = 40


class TweetFeed(Protocol):
    """The search/timeline operations XMonitor needs (impl: the X API client)."""

    def fetch(self) -> list[dict[str, Any]]:
        """Return recent tweets as plain dicts (newest first).

        May raise :class:`app.monitors.base.MonitorBlocked` when the account is
        rate-limited or the query is rejected, so run_monitor records BLOCKED.
        """
        ...

    def close(self) -> None:
        """Release any underlying client resources."""
        ...


class XMonitor(Monitor):
    """Collect X tweets through an injected TweetFeed.

    ``collect`` reads one batch, dedups within the run by ``external_id``
    (defensive -- a search can repeat a tweet across pages), converts each tweet,
    and stops at ``max_posts``. A :class:`MonitorBlocked` from the feed propagates
    so run_monitor records BLOCKED and the account can be cooled down.
    """

    platform: ClassVar[Platform] = Platform.X

    def __init__(
        self,
        feed: TweetFeed,
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
