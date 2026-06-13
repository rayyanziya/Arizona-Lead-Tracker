"""Unit tests for the deduplication engine.

Runs on the local venv with in-memory SQLite (the DB authority) and a dict-backed
fake cache standing in for Redis. The two-layer contract:
  * Redis fast-path: an exact (tenant, platform, external_id) hit with an
    unchanged content_hash short-circuits before Postgres is touched.
  * Postgres authority: get-or-create against UNIQUE(tenant, platform,
    external_id); content_hash separates a true duplicate from an edit, and a
    content_hash twin under a different external_id is a repost.
Dedup must stay correct with no cache at all (Redis down -> cache=None).
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base, Post, Tenant
from app.schemas.raw_post import RawPost
from app.services.dedup import DedupStatus, cache_key, register

pytestmark = pytest.mark.unit


class FakeCache:
    """Minimal stand-in for the injected Redis dedup cache."""

    def __init__(self):
        self.store: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self.store.get(key)

    def set(self, key: str, value: str, ttl_seconds: int) -> None:
        self.store[key] = value


def _session(*tenant_ids: int) -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = Session(engine)
    for tid in tenant_ids or (1,):
        s.add(Tenant(id=tid, name=f"T{tid}", slug=f"t{tid}"))
    s.flush()
    return s


def _raw(**overrides) -> RawPost:
    base = {
        "platform": "facebook",
        "external_id": "p1",
        "url": "http://x/p1",
        "body": "butuh aplikasi kasir",
    }
    base.update(overrides)
    return RawPost(**base)


class TestPostgresAuthority:
    def test_new_post_is_inserted_and_marked_new(self):
        s = _session()
        res = register(s, 1, _raw())
        assert res.status is DedupStatus.NEW
        assert res.is_new is True
        row = s.query(Post).one()
        assert row.external_id == "p1"
        assert row.content_hash == _raw().content_hash
        assert row.tenant_id == 1

    def test_same_external_id_same_content_is_duplicate(self):
        s = _session()
        register(s, 1, _raw())
        res = register(s, 1, _raw())
        assert res.status is DedupStatus.DUPLICATE
        assert res.is_new is False
        assert s.query(Post).count() == 1

    def test_same_external_id_changed_content_is_edited(self):
        s = _session()
        register(s, 1, _raw(body="old text"))
        res = register(s, 1, _raw(body="new text"))
        assert res.status is DedupStatus.EDITED
        assert res.is_new is False
        row = s.query(Post).one()  # still exactly one row
        assert row.body == "new text"
        assert row.content_hash == _raw(body="new text").content_hash

    def test_repost_different_external_id_same_content_is_repost(self):
        s = _session()
        register(s, 1, _raw(external_id="a", body="same words"))
        res = register(s, 1, _raw(external_id="b", body="same words"))
        assert res.status is DedupStatus.REPOST
        assert res.is_new is False
        # the repost is still recorded (so we don't re-evaluate it forever)...
        assert s.query(Post).count() == 2

    def test_same_external_id_across_tenants_is_new_for_each(self):
        s = _session(1, 2)
        a = register(s, 1, _raw())
        b = register(s, 2, _raw())
        assert a.status is DedupStatus.NEW
        assert b.status is DedupStatus.NEW
        assert s.query(Post).count() == 2

    def test_works_without_a_cache(self):
        s = _session()
        assert register(s, 1, _raw(), cache=None).status is DedupStatus.NEW
        assert register(s, 1, _raw(), cache=None).status is DedupStatus.DUPLICATE


class TestRedisFastPath:
    def test_cache_hit_short_circuits_the_database(self):
        s = _session()
        cache = FakeCache()
        post = _raw()
        cache.set(cache_key(1, post), post.content_hash, 0)
        res = register(s, 1, post, cache=cache)
        assert res.status is DedupStatus.DUPLICATE
        assert s.query(Post).count() == 0  # DB never touched

    def test_new_post_populates_the_cache(self):
        s = _session()
        cache = FakeCache()
        post = _raw()
        register(s, 1, post, cache=cache)
        assert cache.get(cache_key(1, post)) == post.content_hash

    def test_stale_cache_hash_does_not_mask_an_edit(self):
        s = _session()
        cache = FakeCache()
        register(s, 1, _raw(body="old"), cache=cache)  # caches old hash
        res = register(s, 1, _raw(body="new"), cache=cache)  # cache hit, hash differs
        assert res.status is DedupStatus.EDITED
        assert cache.get(cache_key(1, _raw(body="new"))) == _raw(body="new").content_hash

    def test_cache_miss_falls_back_to_db_authority(self):
        s = _session()
        register(s, 1, _raw(), cache=None)  # in DB, not in cache
        cache = FakeCache()
        res = register(s, 1, _raw(), cache=cache)
        assert res.status is DedupStatus.DUPLICATE
        assert cache.get(cache_key(1, _raw())) == _raw().content_hash  # repopulated


class TestCacheKey:
    def test_key_is_scoped_by_tenant_platform_external_id(self):
        k1 = cache_key(1, _raw(external_id="x"))
        k2 = cache_key(2, _raw(external_id="x"))
        k3 = cache_key(1, _raw(external_id="y"))
        assert k1 != k2 and k1 != k3
        assert "facebook" in k1 and "x" in k1