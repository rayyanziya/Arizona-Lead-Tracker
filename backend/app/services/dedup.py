"""Deduplication engine: never alert the same post twice.

Two layers, with Postgres as the source of truth and Redis as a pure
optimization:

  1. Redis fast-path (optional). An exact (tenant, platform, external_id) key
     whose value still equals the post's content_hash means "seen, unchanged",
     so we skip the database entirely. Missing or stale entries simply fall
     through; correctness never depends on the cache, so the system degrades
     gracefully when Redis is down (pass cache=None).

  2. Postgres authority. A get-or-create against the
     UNIQUE(tenant_id, platform, external_id) constraint. This is deliberately a
     portable SELECT-then-INSERT guarded by the unique constraint rather than a
     Postgres-only INSERT .. ON CONFLICT: it behaves identically on SQLite (so
     the logic is unit-testable without a database server) and stays
     concurrency-safe on Postgres because the constraint is the final arbiter --
     a racing inserter that loses gets an IntegrityError and is reconciled to a
     duplicate via a savepoint rollback.

content_hash (a normalized SHA-256 of title+body, see app.schemas.raw_post)
turns "seen this id" into something richer:
  * same external_id, same hash      -> DUPLICATE (no-op)
  * same external_id, different hash -> EDITED   (row refreshed in place)
  * different external_id, same hash -> REPOST   (recorded, not re-alerted)
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Post
from app.schemas.raw_post import RawPost

# Lifetime of a Redis fast-path entry. Longer than any realistic re-scrape
# interval, short enough to bound memory; the DB stays authoritative, so this
# only trades cache memory for DB round-trips.
CACHE_TTL_SECONDS = 7 * 24 * 3600


class DedupStatus(str, enum.Enum):
    NEW = "new"
    DUPLICATE = "duplicate"
    EDITED = "edited"
    REPOST = "repost"


class DedupCache(Protocol):
    """Structural type for the injected cache (a thin Redis adapter in prod)."""

    def get(self, key: str) -> str | None: ...
    def set(self, key: str, value: str, ttl_seconds: int) -> None: ...


@dataclass(frozen=True)
class DedupResult:
    status: DedupStatus
    post: Post | None  # the row this resolved to; None only on a pure cache hit

    @property
    def is_new(self) -> bool:
        """True only when the pipeline should go on to score + notify."""
        return self.status is DedupStatus.NEW


def cache_key(tenant_id: int, post: RawPost) -> str:
    return f"dedup:{tenant_id}:{post.platform.value}:{post.external_id}"


def register(
    session: Session,
    tenant_id: int,
    post: RawPost,
    *,
    cache: DedupCache | None = None,
) -> DedupResult:
    """Classify *post* against what we've already seen and record it.

    The caller owns the transaction (we flush, never commit), so a failed run
    rolls back cleanly inside the surrounding session_scope().
    """
    content_hash = post.content_hash

    # --- Layer 1: Redis fast-path -------------------------------------------
    if cache is not None and cache.get(cache_key(tenant_id, post)) == content_hash:
        return DedupResult(DedupStatus.DUPLICATE, None)

    # --- Layer 2: Postgres authority ----------------------------------------
    existing = session.execute(
        select(Post).where(
            Post.tenant_id == tenant_id,
            Post.platform == post.platform,
            Post.external_id == post.external_id,
        )
    ).scalar_one_or_none()

    if existing is not None:
        if existing.content_hash == content_hash:
            status = DedupStatus.DUPLICATE
        else:
            existing.title = post.title
            existing.body = post.body
            existing.url = post.url
            existing.content_hash = content_hash
            session.flush()
            status = DedupStatus.EDITED
        resolved = existing
    else:
        # No row for this external_id. A content_hash twin under a *different*
        # external_id (same tenant + platform) means the text was reposted.
        twin = session.execute(
            select(Post.id).where(
                Post.tenant_id == tenant_id,
                Post.platform == post.platform,
                Post.content_hash == content_hash,
            )
        ).first()
        resolved = _insert(session, tenant_id, post, content_hash)
        status = DedupStatus.REPOST if twin is not None else DedupStatus.NEW

    # --- Refresh the fast-path so the next identical scrape skips the DB -----
    if cache is not None:
        cache.set(cache_key(tenant_id, post), content_hash, CACHE_TTL_SECONDS)

    return DedupResult(status, resolved)


def _insert(session: Session, tenant_id: int, post: RawPost, content_hash: str) -> Post:
    row = Post(
        tenant_id=tenant_id,
        platform=post.platform,
        external_id=post.external_id,
        source_id=post.source_id,
        url=post.url,
        author=post.author,
        title=post.title,
        body=post.body,
        content_hash=content_hash,
        posted_at=post.posted_at,
    )
    try:
        with session.begin_nested():  # SAVEPOINT: isolates the insert
            session.add(row)
            session.flush()
    except IntegrityError:  # pragma: no cover - only under concurrent inserters
        # A racing worker won the unique constraint between our SELECT and
        # INSERT. The savepoint rolled back; adopt the row that won.
        row = session.execute(
            select(Post).where(
                Post.tenant_id == tenant_id,
                Post.platform == post.platform,
                Post.external_id == post.external_id,
            )
        ).scalar_one()
    return row