"""Pure X (Twitter) extraction: tweet dicts -> normalized RawPost.

The X API driver (:mod:`app.monitors.x_client`) does the side-effecting half --
authenticate, search/poll, read tweet objects -- and emits each as a plain
``dict`` (the :class:`XTweet` shape). This module is the pure other half: it turns
those dicts into the canonical :class:`RawPost` the pipeline understands.

Mirrors :mod:`app.monitors.reddit_parser`, with two X-specific shape differences:
a tweet has no title (all its text is the body), and its canonical URL is built
from the author handle and tweet id (``x.com/<handle>/status/<id>``). It imports
no API client, so the error-prone field mapping is unit-tested on the local venv
while only the network search stays integration-only. Faithful conversion is the
contract -- filtering (keyword match, scoring) happens downstream.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any, TypedDict

from pydantic import ValidationError

from app.models import Platform
from app.schemas.raw_post import RawPost

X_BASE = "https://x.com"


class XTweet(TypedDict, total=False):
    """One tweet as emitted by the X API driver (every key optional).

    ``external_id`` is the numeric tweet id (a string -- ids exceed 53-bit floats).
    ``author`` is the handle, with or without a leading ``@``. A ``url`` is used
    verbatim when present; otherwise it is built from the handle and id, so the
    only hard requirement is the id. ``created_at`` is ISO 8601 (the API default)
    or unix seconds.
    """

    external_id: str | None
    text: str | None
    author: str | None
    url: str | None
    created_at: int | float | str | None


def to_raw_posts(
    elements: Iterable[Mapping[str, Any]],
    *,
    source_id: int | None = None,
) -> list[RawPost]:
    """Convert a batch of tweets, preserving order and dropping junk."""
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
    """Convert one tweet to a RawPost, or None if it is unusable.

    Unusable means no ``external_id`` (without it we can build neither the dedup
    key nor a URL).
    """
    external_id = str(element.get("external_id") or "").strip()
    if not external_id:
        return None

    handle = _clean_handle(element.get("author"))
    url = _build_url(element.get("url"), handle, external_id)

    try:
        return RawPost(
            platform=Platform.X,
            external_id=external_id,
            url=url,
            author=handle,
            title=None,  # tweets have no title; all text is the body
            body=_clean_body(element.get("text")),
            posted_at=_parse_timestamp(element.get("created_at")),
            source_id=source_id,
        )
    except ValidationError:  # pragma: no cover
        return None


def _build_url(url: object, handle: str | None, external_id: str) -> str:
    if isinstance(url, str) and url.strip():
        u = url.strip()
        if not u.startswith(("http://", "https://")):
            u = f"{X_BASE}/{u.lstrip('/')}"
        return _strip_query(u)
    # The /i/status/<id> form resolves without a handle, so a tweet whose author
    # is missing still gets a working canonical link.
    if handle:
        return f"{X_BASE}/{handle}/status/{external_id}"
    return f"{X_BASE}/i/status/{external_id}"


def _strip_query(url: str) -> str:
    for sep in ("?", "#"):
        cut = url.find(sep)
        if cut != -1:
            url = url[:cut]
    return url


def _clean_handle(value: object) -> str | None:
    if isinstance(value, str):
        cleaned = value.strip().lstrip("@").strip()
        if cleaned:
            return cleaned
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
