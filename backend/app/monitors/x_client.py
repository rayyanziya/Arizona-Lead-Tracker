"""tweepy-backed X (Twitter) tweet feed (the network half of the X collector).

The concrete :class:`~app.monitors.x.TweetFeed` for production: it owns the X API
client. Two deliberate choices keep it unit-testable on the local venv, unlike
:mod:`app.monitors.reddit_client` (which imports PRAW at module top and is
integration-only):

* tweepy is imported *lazily* (only when a real client is constructed), so the
  module imports without tweepy installed and tests inject a fake client.
* blocking errors are matched by exception *class name*, so the rate-limit ->
  :class:`MonitorBlocked` translation needs no tweepy import on the error path.

The Celery task assembles this with :class:`~app.monitors.x.XMonitor`. The pure
surfaces -- :func:`build_query` and :meth:`ApiTweetFeed._to_dict` -- carry the
fragile field mapping and are exercised by the unit suite.
"""

from __future__ import annotations

import re
from typing import Any

from app.monitors.base import MonitorBlocked

# X recent-search caps a single request at 100 results and floors it at 10.
_MIN_RESULTS = 10
_MAX_RESULTS = 100

# A handle is 1-15 word characters. A profile URL is a *single* path segment after
# the host, so a multi-segment status URL (x.com/i/status/<id>) is intentionally
# not matched and falls through to being treated as a raw search query.
_HANDLE = re.compile(r"[A-Za-z0-9_]{1,15}")
_PROFILE_URL = re.compile(
    r"^https?://(?:[\w.-]+\.)?(?:x|twitter)\.com/@?([A-Za-z0-9_]{1,15})/?$",
    re.IGNORECASE,
)

# tweepy raises these for auth/permission/rate-limit failures. Matched by name so
# the translation needs no tweepy import (keeping the module venv-importable).
_BLOCKING_ERROR_NAMES = frozenset({"TooManyRequests", "Unauthorized", "Forbidden"})


def build_query(identifier: str) -> str | None:
    """Turn a monitored-source identifier into an X recent-search query.

    A handle (``@name``) or a profile URL is narrowed to that account's tweets
    (``from:name``); anything else -- free text, a hashtag -- is used verbatim as
    a keyword search. Blank input yields ``None`` so the caller can skip the source.
    """
    text = (identifier or "").strip()
    if not text:
        return None
    profile = _PROFILE_URL.match(text)
    if profile:
        return f"from:{profile.group(1)}"
    if text.startswith("@") and _HANDLE.fullmatch(text[1:]):
        return f"from:{text[1:]}"
    return text


class ApiTweetFeed:
    """Search recent X tweets via tweepy, lazily creating an app-only client.

    The tweepy client is built on first :meth:`fetch` (so constructing the feed is
    cheap and import-safe). The bearer token comes from Settings via the Celery
    task. Auth/permission/rate-limit failures become :class:`MonitorBlocked` so the
    source cools down instead of erroring forever.
    """

    def __init__(
        self,
        query: str,
        *,
        bearer_token: str,
        max_results: int = 40,
        client: Any | None = None,
    ) -> None:
        self._query = query
        self._bearer_token = bearer_token
        self._max_results = max_results
        self._client = client

    def _ensure_client(self) -> Any:
        if self._client is None:
            import tweepy  # lazy: keeps the module importable without tweepy

            self._client = tweepy.Client(bearer_token=self._bearer_token)
        return self._client

    def fetch(self) -> list[dict[str, Any]]:
        client = self._ensure_client()
        capped = max(_MIN_RESULTS, min(self._max_results, _MAX_RESULTS))
        try:
            response = client.search_recent_tweets(
                query=self._query,
                max_results=capped,
                tweet_fields=["created_at", "author_id"],
                expansions=["author_id"],
                user_fields=["username"],
            )
        except Exception as exc:  # noqa: BLE001 -- re-raised unless it is a known block
            if type(exc).__name__ in _BLOCKING_ERROR_NAMES:
                raise MonitorBlocked(f"x search blocked ({type(exc).__name__}): {exc}") from exc
            raise
        tweets = response.data or []
        includes = getattr(response, "includes", None) or {}
        users_by_id = {u.id: u for u in includes.get("users", [])}
        return [self._to_dict(t, users_by_id) for t in tweets]

    @staticmethod
    def _to_dict(tweet: Any, users_by_id: dict[Any, Any]) -> dict[str, Any]:
        author_id = getattr(tweet, "author_id", None)
        user = users_by_id.get(author_id) if author_id is not None else None
        handle = getattr(user, "username", None) if user is not None else None
        created = getattr(tweet, "created_at", None)
        created_at = created.isoformat() if hasattr(created, "isoformat") else created
        return {
            "external_id": str(getattr(tweet, "id", "") or ""),
            "text": getattr(tweet, "text", "") or "",
            "author": handle,
            "created_at": created_at,
            "url": None,  # x_parser builds the canonical URL from handle + id
        }

    def close(self) -> None:
        # tweepy uses a requests session under the hood; nothing to close
        # explicitly, but this satisfies the TweetFeed protocol's teardown contract.
        self._client = None
