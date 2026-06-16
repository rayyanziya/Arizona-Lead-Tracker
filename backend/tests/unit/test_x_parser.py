"""Unit tests for the pure X (Twitter) tweet -> RawPost transform.

Mirrors test_reddit_parser: no network, no API client -- just the field mapping
(URL building from handle+id, body-only text, @-handle normalization, ISO/epoch
timestamp parsing, junk rejection) exercised on the local venv. The X API driver
emits the XTweet dict shape this asserts.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.models import Platform
from app.monitors.x_parser import to_raw_post, to_raw_posts


def _tweet(**over):
    el = {
        "external_id": "1800000000000000001",
        "text": "Looking for a developer to build our POS app in Phoenix. DM me.",
        "author": "phx_buyer",
        "created_at": "2026-06-15T12:00:00.000Z",
    }
    el.update(over)
    return el


class TestToRawPost:
    def test_maps_core_fields(self):
        post = to_raw_post(_tweet())
        assert post is not None
        assert post.platform is Platform.X
        assert post.external_id == "1800000000000000001"
        # Tweets have no title; all text is the body.
        assert post.title is None
        assert post.body == "Looking for a developer to build our POS app in Phoenix. DM me."
        assert post.author == "phx_buyer"

    def test_builds_url_from_handle_and_id(self):
        post = to_raw_post(_tweet())
        assert post is not None
        assert post.url == "https://x.com/phx_buyer/status/1800000000000000001"

    def test_strips_leading_at_from_handle(self):
        post = to_raw_post(_tweet(author="@phx_buyer"))
        assert post is not None
        assert post.author == "phx_buyer"
        assert post.url == "https://x.com/phx_buyer/status/1800000000000000001"

    def test_uses_explicit_url_and_strips_query(self):
        post = to_raw_post(
            _tweet(url="https://x.com/phx_buyer/status/1800000000000000001?s=20&t=abc")
        )
        assert post is not None
        assert post.url == "https://x.com/phx_buyer/status/1800000000000000001"

    def test_falls_back_to_i_status_without_author(self):
        post = to_raw_post(_tweet(author=None))
        assert post is not None
        assert post.author is None
        assert post.url == "https://x.com/i/status/1800000000000000001"

    def test_parses_iso_created_at_to_aware_datetime(self):
        post = to_raw_post(_tweet(created_at="2026-06-15T12:00:00.000Z"))
        assert post is not None
        assert post.posted_at == datetime(2026, 6, 15, 12, 0, 0, tzinfo=UTC)

    def test_parses_epoch_created_at(self):
        post = to_raw_post(_tweet(created_at=1_700_000_000))
        assert post is not None
        assert post.posted_at == datetime.fromtimestamp(1_700_000_000, tz=UTC)

    def test_missing_external_id_dropped(self):
        assert to_raw_post(_tweet(external_id=None)) is None
        assert to_raw_post(_tweet(external_id="   ")) is None

    def test_blank_text_keeps_empty_body(self):
        post = to_raw_post(_tweet(text=""))
        assert post is not None
        assert post.body == ""

    def test_source_id_flows_through(self):
        post = to_raw_post(_tweet(), source_id=7)
        assert post is not None
        assert post.source_id == 7


class TestToRawPosts:
    def test_preserves_order_and_drops_junk(self):
        posts = to_raw_posts(
            [_tweet(external_id="1"), {"text": "no id"}, _tweet(external_id="2")]
        )
        assert [p.external_id for p in posts] == ["1", "2"]
