"""Pure Reddit extraction: submission dicts -> normalized RawPost.

The PRAW driver (:mod:`app.monitors.reddit_client`) does the side-effecting half
-- authenticate, list a subreddit, read PRAW ``Submission`` objects -- and emits
each as a plain ``dict`` (the :class:`RedditSubmission` shape). This module is the
pure other half: it turns those dicts into the canonical :class:`RawPost` the
pipeline understands, with a clean post URL, the title kept distinct from the
body, a timezone-aware ``posted_at``, and unusable elements dropped.

Splitting the transform out mirrors :mod:`app.monitors.facebook_parser`: it imports
no PRAW, so the error-prone field mapping is unit-tested on the local venv while
only the network listing stays integration-only. Faithful conversion is the
contract -- filtering (keyword match, scoring) happens downstream, so even a
body-less link post is converted rather than silently dropped.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any, TypedDict

from pydantic import ValidationError

from app.models import Platform
from app.schemas.raw_post import RawPost

REDDIT_BASE = "https://www.reddit.com"
SHORTLINK_BASE = "https://redd.it"


class RedditSubmission(TypedDict, total=False):
    """One submission as emitted by the PRAW driver (every key optional).

    ``external_id`` is the base-36 submission id. A ``permalink`` is preferred for
    the URL; when absent the id alone yields a ``redd.it`` short link, so the only
    hard requirement is the id. ``created_utc`` is unix seconds (PRAW gives a float).
    """

    external_id: str | None
    title: str | None
    selftext: str | None
    author: str | None
    permalink: str | None
    created_utc: int | float | str | None


def to_raw_posts(
    elements: Iterable[Mapping[str, Any]],
    *,
    source_id: int | None = None,
) -> list[RawPost]:
    """Convert a batch of submissions, preserving order and dropping junk."""
    posts: list[RawPost] = []
    for element in elements:
        post = to_raw_post(element, source_id=source_id)
        if post is not None:
            posts.append(post)
    return posts


def to_raw_post(
    element: Mapping[str, Any],
    *,
    source_id: int | None = None,
) -> RawPost | None:
    """Convert one submission to a RawPost, or None if it is unusable.

    Unusable means no ``external_id`` (without it we can build neither the dedup
    key nor a URL).
    """
    external_id = str(element.get("external_id") or "").strip()
    if not external_id:
        return None

    url = _build_url(element.get("permalink"), external_id)
    if url is None:
        return None

    try:
        return RawPost(
            platform=Platform.REDDIT,
            external_id=external_id,
            url=url,
            author=_clean_text(element.get("author")),
            title=_clean_text(element.get("title")),
            body=_clean_body(element.get("selftext")),
            posted_at=_parse_timestamp(element.get("created_utc")),
            source_id=source_id,
        )
    except ValidationError:  # pragma: no cover
        return None


def _build_url(permalink: object, external_id: str) -> str | None:
    if isinstance(permalink, str) and permalink.strip():
        p = permalink.strip()
        if p.startswith(("http://", "https://")):
            url = p
        elif p.startswith("/"):
            url = REDDIT_BASE + p
        else:
            url = f"{REDDIT_BASE}/{p}"
        return _strip_query(url)
    return f"{SHORTLINK_BASE}/{external_id}"


def _strip_query(url: str) -> str:
    for sep in ("?", "#"):
        cut = url.find(sep)
        if cut != -1:
            url = url[:cut]
    return url


def _clean_text(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _clean_body(text: object) -> str:
    return text.strip() if isinstance(text, str) else ""


def _parse_timestamp(value: object) -> datetime | None:
    # bool is an int subclass; never treat True/False as epoch seconds.
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        try:
            return datetime.fromtimestamp(value, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        for candidate in (s, s.replace("Z", "+00:00")):
            try:
                dt = datetime.fromisoformat(candidate)
            except ValueError:
                continue
            return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
    return None