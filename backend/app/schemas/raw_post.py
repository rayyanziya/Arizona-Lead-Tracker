"""Normalized collector DTO shared by every monitor.

Facebook, Reddit, and X each return wildly different payloads. They all converge
on :class:`RawPost` before anything enters the dedup -> match -> score pipeline,
so the downstream stages only ever see one shape. Validation rejects junk
(blank ``external_id`` / ``url``) at the boundary, where the data is least
trustworthy.

``content_hash`` is the *secondary* dedup signal. The primary anchor is the DB
key ``UNIQUE(tenant_id, platform, external_id)``; the hash adds edit/repost
awareness on top:
  * same ``external_id``, changed hash  -> the post was edited
  * different ``external_id``, same hash -> the same content was reposted
Because it normalizes case, accents, and whitespace, cosmetic edits hash
identically and do not masquerade as new content.
"""

from __future__ import annotations

import hashlib
from datetime import datetime

from pydantic import BaseModel, ConfigDict, computed_field, field_validator

from app.models.base import Platform
from app.services.keyword_matcher import normalize


def compute_content_hash(title: str | None, body: str | None) -> str:
    """Return a stable SHA-256 hex digest over the post's normalized text.

    Reuses the project's canonical :func:`normalize` (lowercase, accent-fold,
    whitespace-collapse) so the hash is invariant to cosmetic edits but moves
    when the actual wording changes.
    """
    canonical = normalize(f"{title or ''}\n{body or ''}")
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class RawPost(BaseModel):
    """A single post in the one shape the pipeline understands.

    Immutable: collectors build it, the pipeline reads it, nobody mutates it
    mid-flight. Unknown keys from a collector payload are ignored rather than
    rejected, so a scraper can pass through extra debug fields harmlessly.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    platform: Platform
    external_id: str
    url: str
    author: str | None = None
    title: str | None = None
    body: str = ""
    posted_at: datetime | None = None
    source_id: int | None = None

    @field_validator("external_id", "url")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("must not be blank")
        return cleaned

    @computed_field  # type: ignore[prop-decorator]
    @property
    def content_hash(self) -> str:
        return compute_content_hash(self.title, self.body)