"""Unit tests for the RawPost collector DTO and its content hash.

RawPost is the single normalized shape every collector (Facebook/Reddit/X)
converges on before a post enters the dedup -> match -> score pipeline. Its
content_hash is the secondary dedup signal: stable across trivial edits, but it
changes when the real text changes (edit detection) and collides when two
different posts carry identical content (repost detection).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.base import Platform
from app.schemas.raw_post import RawPost, compute_content_hash

pytestmark = pytest.mark.unit


def _post(**overrides):
    base = {
        "platform": "facebook",
        "external_id": "post-123",
        "url": "https://facebook.com/groups/1/posts/123",
        "body": "Butuh aplikasi kasir untuk toko saya",
    }
    base.update(overrides)
    return RawPost(**base)


class TestRawPostNormalization:
    def test_string_platform_is_coerced_to_enum(self):
        assert _post().platform is Platform.FACEBOOK

    def test_optional_fields_default(self):
        p = _post()
        assert p.author is None
        assert p.title is None
        assert p.source_id is None
        assert p.posted_at is None

    def test_body_defaults_to_empty_string(self):
        # Title-only posts are valid (common on Facebook).
        p = RawPost(platform="facebook", external_id="x", url="http://x", title="hi")
        assert p.body == ""

    def test_blank_external_id_is_rejected(self):
        with pytest.raises(ValidationError):
            _post(external_id="   ")

    def test_blank_url_is_rejected(self):
        with pytest.raises(ValidationError):
            _post(url="")

    def test_external_id_and_url_are_trimmed(self):
        p = _post(external_id="  abc  ", url="  http://x  ")
        assert p.external_id == "abc"
        assert p.url == "http://x"

    def test_unknown_platform_is_rejected(self):
        with pytest.raises(ValidationError):
            _post(platform="linkedin")

    def test_extra_collector_fields_are_ignored(self):
        p = _post(reactions=42, permalink="http://x/perm")
        assert not hasattr(p, "reactions")

    def test_is_immutable(self):
        p = _post()
        with pytest.raises(ValidationError):
            p.body = "changed"


class TestContentHash:
    def test_is_sha256_hex(self):
        h = compute_content_hash("t", "b")
        assert len(h) == 64
        int(h, 16)  # must be hex-decodable

    def test_is_deterministic(self):
        assert compute_content_hash("t", "b") == compute_content_hash("t", "b")

    def test_changes_when_body_changes(self):
        # Edit detection: same post id, new content -> new hash.
        assert _post(body="one").content_hash != _post(body="two").content_hash

    def test_ignores_case_and_whitespace(self):
        # A capitalization / spacing tweak must not read as new content.
        a = _post(body="Butuh   APLIKASI kasir")
        b = _post(body="butuh aplikasi kasir")
        assert a.content_hash == b.content_hash

    def test_same_content_different_external_id_collides(self):
        # Repost detection: different id, identical content -> same hash.
        a = _post(external_id="1", body="same text")
        b = _post(external_id="2", body="same text")
        assert a.content_hash == b.content_hash

    def test_title_participates_in_hash(self):
        assert _post(title="A").content_hash != _post(title="B").content_hash

    def test_property_matches_helper(self):
        p = _post(title="Title", body="Body")
        assert p.content_hash == compute_content_hash("Title", "Body")

    def test_hash_is_serialized(self):
        # content_hash is a computed field, so it shows up in model_dump for logs.
        assert "content_hash" in _post().model_dump()