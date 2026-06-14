"""PRAW-backed Reddit submission feed (integration only; not unit-tested).

The concrete :class:`~app.monitors.reddit.SubmissionFeed` for production: it owns
PRAW, so it is kept out of the unit suite (PRAW is Docker-only) the same way
:mod:`app.monitors.facebook_browser` keeps Playwright out. The Celery task assembles
this with :class:`~app.monitors.reddit.RedditMonitor`. The only fragile surface is
``_to_dict`` -- the PRAW ``Submission`` -> :class:`RedditSubmission` field mapping --
and forbidden/missing subreddits are translated to :class:`MonitorBlocked` so a
private or banned community cools the source down instead of erroring forever.
"""

from __future__ import annotations

from typing import Any

import praw
from prawcore.exceptions import Forbidden, NotFound, Redirect

from app.monitors.base import MonitorBlocked

# PRAW listing methods we allow a source to use; "new" is right for lead capture
# (most-recent submissions), the others are available for future tuning.
_LISTINGS = ("new", "hot", "rising", "top")


class PrawSubmissionFeed:
    """List a subreddit's submissions via PRAW, lazily creating a read-only client.

    The Reddit instance is built on first :meth:`fetch` (so constructing the feed
    is cheap and import-safe). Credentials come from Settings via the Celery task.
    """

    def __init__(
        self,
        subreddit: str,
        *,
        client_id: str,
        client_secret: str,
        user_agent: str,
        listing: str = "new",
        limit: int = 40,
        reddit: praw.Reddit | None = None,
    ) -> None:
        self._subreddit = subreddit
        self._client_id = client_id
        self._client_secret = client_secret
        self._user_agent = user_agent
        self._listing = listing if listing in _LISTINGS else "new"
        self._limit = limit
        self._reddit = reddit

    def _ensure_client(self) -> praw.Reddit:
        if self._reddit is None:
            self._reddit = praw.Reddit(
                client_id=self._client_id,
                client_secret=self._client_secret,
                user_agent=self._user_agent,
                check_for_updates=False,
            )
            self._reddit.read_only = True
        return self._reddit

    def fetch(self) -> list[dict[str, Any]]:
        reddit = self._ensure_client()
        try:
            subreddit = reddit.subreddit(self._subreddit)
            lister = getattr(subreddit, self._listing)
            return [self._to_dict(s) for s in lister(limit=self._limit)]
        except (Forbidden, NotFound, Redirect) as exc:
            raise MonitorBlocked(
                f"reddit subreddit r/{self._subreddit} unavailable: {exc}"
            ) from exc

    @staticmethod
    def _to_dict(submission: Any) -> dict[str, Any]:
        author = getattr(submission, "author", None)
        return {
            "external_id": submission.id,
            "title": submission.title,
            "selftext": getattr(submission, "selftext", "") or "",
            "author": str(author) if author is not None else None,
            "permalink": submission.permalink,
            "created_utc": submission.created_utc,
        }

    def close(self) -> None:
        # PRAW uses a requests session under the hood; nothing to close explicitly,
        # but the method satisfies the SubmissionFeed protocol's teardown contract.
        self._reddit = None