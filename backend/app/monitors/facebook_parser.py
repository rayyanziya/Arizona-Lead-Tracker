"""Pure Facebook extraction: scraped element dicts -> normalized RawPost.

The Playwright collector (:mod:`app.monitors.facebook`) does the fragile,
side-effecting half -- drive a logged-in browser, scroll the group feed, read DOM
nodes -- and emits each post as a plain ``dict`` (the :class:`ScrapedPost` shape).
This module is the pure other half: it turns those dicts into the canonical
:class:`RawPost` the pipeline understands, with a clean post URL, a timezone-aware
``posted_at``, and unusable elements dropped.

Splitting the pure transform out is deliberate -- it imports no browser, so the
error-prone field mapping (URL building, timestamp parsing, junk rejection) is
unit-tested on the local venv while only the DOM scraping stays integration-only.
Faithful conversion is the contract: filtering (keyword match, scoring) happens
downstream, so even a caption-less post is converted rather than silently dropped.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any, TypedDict

from pydantic import ValidationError

from app.models import Platform
from app.schemas.raw_post import RawPost

FB_BASE = "https://www.facebook.com"


class ScrapedPost(TypedDict, total=False):
    """One post as emitted by the Facebook DOM scraper (every key optional).

    ``external_id`` plus a derivable URL are the only hard requirements; an
    element missing either is dropped. ``timestamp`` may be unix seconds, an
    ISO-8601 string, or absent.
    """

    external_id: str | None
    text: str | None
    author: str | None
    permalink: str | None
    timestamp: int | float | str | None


def to_raw_posts(
    elements: Iterable[Mapping[str, Any]],
    *,
    group_id: str | None = None,
    source_id: int | None = None,
) -> list[RawPost]:
    """Convert a batch of scraped elements, preserving order and dropping junk."""
    posts: list[RawPost] = []
    for element in elements:
        post = to_raw_post(element, group_id=group_id, source_id=source_id)
        if post is not None:
            posts.append(post)
    return posts


def to_raw_post(
    element: Mapping[str, Any],
    *,
    group_id: str | None = None,
    source_id: int | None = None,
) -> RawPost | None:
    """Convert one scraped element to a RawPost, or None if it is unusable.

    Unusable means no ``external_id``, or no URL we can build (neither a
    ``permalink`` nor a ``group_id`` to synthesize one from).
    """
    external_id = str(element.get("external_id") or "").strip()
    if not external_id:
        return None

    url = _build_url(element.get("permalink"), group_id, external_id)
    if url is None:
        return None

    try:
        return RawPost(
            platform=Platform.FACEBOOK,
            external_id=external_id,
            url=url,
            author=_clean_author(element.get("author")),
            body=_clean_body(element.get("text")),
            posted_at=_parse_timestamp(element.get("timestamp")),
            source_id=source_id,
        )
    except ValidationError:  # pragma: no cover
        return None


def _build_url(permalink: object, group_id: str | None, external_id: str) -> str | None:
    if isinstance(permalink, str) and permalink.strip():
        p = permalink.strip()
        if p.startswith(("http://", "https://")):
            url = p
        elif p.startswith("/"):
            url = FB_BASE + p
        else:
            url = f"{FB_BASE}/{p}"
        return _strip_query(url)
    if group_id and external_id:
        return f"{FB_BASE}/groups/{group_id}/posts/{external_id}/"
    return None


def _strip_query(url: str) -> str:
    for sep in ("?", "#"):
        cut = url.find(sep)
        if cut != -1:
            url = url[:cut]
    return url


def _clean_author(author: object) -> str | None:
    if isinstance(author, str) and author.strip():
        return author.strip()
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