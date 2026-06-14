"""Facebook collector: scroll a logged-in group feed into RawPosts.

The pure, browser-free half of the Facebook monitor. The fragile DOM work lives
behind the :class:`FeedDriver` protocol -- this module never imports Playwright --
so the bug-prone logic (in-run dedup, the post cap, scroll-until-dry, human-paced
delays, block detection) is unit-tested on the local venv against a fake driver,
the same way :mod:`app.monitors.facebook_parser` keeps field mapping testable. The
concrete Playwright driver lives in app.monitors.facebook_browser (integration
only) and is assembled with this monitor by the Celery scrape task.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable, Iterator
from typing import ClassVar, Protocol

from app.models import Platform
from app.monitors.base import Monitor, MonitorBlocked
from app.monitors.facebook_parser import ScrapedPost, to_raw_post
from app.schemas.raw_post import RawPost

# Pacing/anti-ban defaults. The Celery factory overrides these from Settings, so
# this module stays free of the (Docker-only) config import and unit-testable.
DEFAULT_MAX_POSTS = 40
DEFAULT_MIN_DELAY_MS = 2500
DEFAULT_MAX_DELAY_MS = 7000
DEFAULT_MAX_EMPTY_SCROLLS = 3


class FeedDriver(Protocol):
    """The browser operations FacebookMonitor needs (impl: Playwright)."""

    def read_posts(self) -> list[ScrapedPost]:
        """Return the posts currently rendered in the feed."""
        ...

    def scroll(self) -> None:
        """Scroll the feed to load older posts."""
        ...

    def is_blocked(self) -> bool:
        """True if a login/checkpoint wall is showing instead of the feed."""
        ...

    def close(self) -> None:
        """Release the browser/context."""
        ...


class FacebookMonitor(Monitor):
    """Collect a Facebook group feed through an injected FeedDriver.

    ``collect`` scrolls until it has ``max_posts`` posts, the feed stops yielding
    anything new for ``max_empty_scrolls`` consecutive reads, or the platform
    blocks us. It dedups within the run (a feed re-renders the same nodes as you
    scroll), paces each scroll with a random human-like delay, and raises
    :class:`MonitorBlocked` on a login/checkpoint wall so run_monitor records
    BLOCKED and the account can be cooled down.
    """

    platform: ClassVar[Platform] = Platform.FACEBOOK

    def __init__(
        self,
        driver: FeedDriver,
        *,
        group_id: str | None = None,
        source_id: int | None = None,
        max_posts: int = DEFAULT_MAX_POSTS,
        min_delay_ms: int = DEFAULT_MIN_DELAY_MS,
        max_delay_ms: int = DEFAULT_MAX_DELAY_MS,
        max_empty_scrolls: int = DEFAULT_MAX_EMPTY_SCROLLS,
        sleep: Callable[[float], None] = time.sleep,
        rng: random.Random | None = None,
    ) -> None:
        super().__init__(source_id=source_id)
        self._driver = driver
        self._group_id = group_id
        self._max_posts = max_posts
        self._min_delay_ms = min_delay_ms
        self._max_delay_ms = max_delay_ms
        self._max_empty_scrolls = max_empty_scrolls
        self._sleep = sleep
        self._rng = rng or random.Random()  # noqa: S311 - pacing jitter, not security

    def collect(self) -> Iterator[RawPost]:
        seen: set[str] = set()
        yielded = 0
        empty_scrolls = 0
        try:
            while True:
                if self._driver.is_blocked():
                    raise MonitorBlocked("facebook feed blocked (login/checkpoint wall)")
                new_in_batch = 0
                for element in self._driver.read_posts():
                    external_id = str(element.get("external_id") or "").strip()
                    if not external_id or external_id in seen:
                        continue
                    seen.add(external_id)
                    post = to_raw_post(element, group_id=self._group_id, source_id=self.source_id)
                    if post is None:
                        continue
                    new_in_batch += 1
                    yield post
                    yielded += 1
                    if yielded >= self._max_posts:
                        return
                if new_in_batch == 0:
                    empty_scrolls += 1
                    if empty_scrolls >= self._max_empty_scrolls:
                        return
                else:
                    empty_scrolls = 0
                self._sleep(self._delay_seconds())
                self._driver.scroll()
        finally:
            self._driver.close()

    def _delay_seconds(self) -> float:
        return self._rng.uniform(self._min_delay_ms, self._max_delay_ms) / 1000.0