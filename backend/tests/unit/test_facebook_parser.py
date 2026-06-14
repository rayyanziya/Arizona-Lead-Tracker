"""Unit tests for the Facebook parser: scraped element dicts -> RawPost.

The Playwright collector (facebook.py, step 2) extracts raw DOM nodes into plain
dicts; this pure module turns those dicts into the normalized RawPost the
pipeline consumes -- canonical post URL, parsed posted_at, malformed elements
dropped. Pure (no Playwright/browser), so the fragile extract-to-DTO logic is
unit-tested on the local venv; only the DOM scraping itself is integration-only.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.models import Platform
from app.monitors.facebook_parser import to_raw_post, to_raw_posts

pytestmark = pytest.mark.unit

FB = "https://www.facebook.com"


def _el(**overrides):
    base = {
        "external_id": "123",
        "text": "Butuh aplikasi kasir untuk toko",
        "author": "Budi",
        "permalink": "/groups/777/posts/123/",
        "timestamp": 1_700_000_000,
    }
    base.update(overrides)
    return base


class TestToRawPost:
    def test_maps_core_fields_onto_a_facebook_rawpost(self):
        p = to_raw_post(_el())
        assert p is not None
        assert p.platform is Platform.FACEBOOK
        assert p.external_id == "123"
        assert p.author == "Budi"
        assert p.body == "Butuh aplikasi kasir untuk toko"

    def test_relative_permalink_becomes_absolute(self):
        p = to_raw_post(_el(permalink="/groups/777/posts/123/"))
        assert p.url == f"{FB}/groups/777/posts/123/"

    def test_absolute_permalink_is_kept(self):
        p = to_raw_post(_el(permalink=f"{FB}/groups/777/posts/123/"))
        assert p.url == f"{FB}/groups/777/posts/123/"

    def test_permalink_without_scheme_gets_the_base_prefix(self):
        p = to_raw_post(_el(permalink="groups/777/posts/123/"))
        assert p.url == f"{FB}/groups/777/posts/123/"

    def test_tracking_query_and_fragment_are_stripped(self):
        p = to_raw_post(_el(permalink=f"{FB}/groups/777/posts/123/?__cft__[0]=abc#x"))
        assert p.url == f"{FB}/groups/777/posts/123/"

    def test_url_is_built_from_group_id_when_permalink_missing(self):
        p = to_raw_post(_el(permalink=None), group_id="777")
        assert p.url == f"{FB}/groups/777/posts/123/"

    def test_missing_external_id_is_dropped(self):
        assert to_raw_post(_el(external_id=None)) is None
        assert to_raw_post(_el(external_id="   ")) is None

    def test_undeterminable_url_is_dropped(self):
        # no permalink and no group_id -> cannot build a url
        assert to_raw_post(_el(permalink=None)) is None

    def test_blank_author_becomes_none(self):
        assert to_raw_post(_el(author="   ")).author is None
        assert to_raw_post(_el(author=None)).author is None

    def test_missing_text_defaults_to_empty_body(self):
        # caption-less posts still convert; filtering is downstream
        p = to_raw_post(_el(text=None))
        assert p is not None
        assert p.body == ""

    def test_source_id_is_propagated(self):
        assert to_raw_post(_el(), source_id=42).source_id == 42


class TestTimestamp:
    def test_unix_seconds_parse_to_utc(self):
        p = to_raw_post(_el(timestamp=1_700_000_000))
        assert p.posted_at == datetime.fromtimestamp(1_700_000_000, tz=UTC)

    def test_float_seconds_are_accepted(self):
        p = to_raw_post(_el(timestamp=1_700_000_000.0))
        assert p.posted_at == datetime.fromtimestamp(1_700_000_000, tz=UTC)

    def test_iso_with_offset_parses(self):
        p = to_raw_post(_el(timestamp="2026-03-04T05:06:07+00:00"))
        assert p.posted_at == datetime(2026, 3, 4, 5, 6, 7, tzinfo=UTC)

    def test_iso_with_z_parses_as_utc(self):
        p = to_raw_post(_el(timestamp="2026-03-04T05:06:07Z"))
        assert p.posted_at == datetime(2026, 3, 4, 5, 6, 7, tzinfo=UTC)

    def test_naive_iso_is_assumed_utc(self):
        p = to_raw_post(_el(timestamp="2026-03-04T05:06:07"))
        assert p.posted_at == datetime(2026, 3, 4, 5, 6, 7, tzinfo=UTC)

    def test_none_timestamp_is_none(self):
        assert to_raw_post(_el(timestamp=None)).posted_at is None

    def test_unparseable_timestamp_is_none(self):
        assert to_raw_post(_el(timestamp="2 hours ago")).posted_at is None
        assert to_raw_post(_el(timestamp="")).posted_at is None

    def test_overflowing_unix_timestamp_is_none(self):
        assert to_raw_post(_el(timestamp=10**20)).posted_at is None

    def test_bool_is_not_treated_as_unix_seconds(self):
        assert to_raw_post(_el(timestamp=True)).posted_at is None


class TestToRawPosts:
    def test_preserves_order(self):
        els = [_el(external_id="1"), _el(external_id="2"), _el(external_id="3")]
        out = to_raw_posts(els, group_id="777")
        assert [p.external_id for p in out] == ["1", "2", "3"]

    def test_skips_malformed_and_keeps_valid(self):
        els = [_el(external_id="1"), _el(external_id=None), _el(external_id="3")]
        out = to_raw_posts(els, group_id="777")
        assert [p.external_id for p in out] == ["1", "3"]

    def test_empty_input_yields_empty_list(self):
        assert to_raw_posts([], group_id="777") == []

    def test_group_id_and_source_id_apply_to_the_batch(self):
        out = to_raw_posts([_el(permalink=None)], group_id="777", source_id=9)
        assert out[0].url == f"{FB}/groups/777/posts/123/"
        assert out[0].source_id == 9