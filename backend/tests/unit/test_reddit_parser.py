"""Unit tests for the pure Reddit submission -> RawPost transform.

Mirrors test_facebook_parser: no PRAW, no network -- just the field mapping
(URL building, title/body split, timestamp parsing, junk rejection) exercised on
the local venv. The PRAW driver emits the RedditSubmission dict shape this asserts.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.models import Platform
from app.monitors.reddit_parser import to_raw_post, to_raw_posts


def _sub(**over):
    el = {
        "external_id": "abc123",
        "title": "Looking for a contractor",
        "selftext": "Need help building an ADU in Phoenix",
        "author": "phx_user",
        "permalink": "/r/Phoenix/comments/abc123/looking_for_a_contractor/",
        "created_utc": 1_700_000_000,
    }
    el.update(over)
    return el


class TestToRawPost:
    def test_maps_core_fields(self):
        post = to_raw_post(_sub())
        assert post is not None
        assert post.platform is Platform.REDDIT
        assert post.external_id == "abc123"
        assert post.title == "Looking for a contractor"
        assert post.body == "Need help building an ADU in Phoenix"
        assert post.author == "phx_user"

    def test_builds_url_from_relative_permalink(self):
        post = to_raw_post(_sub())
        assert post is not None
        assert post.url == (
            "https://www.reddit.com/r/Phoenix/comments/abc123/looking_for_a_contractor/"
        )

    def test_keeps_absolute_permalink_and_strips_query(self):
        post = to_raw_post(
            _sub(permalink="https://www.reddit.com/r/x/comments/abc123/t/?utm_source=x")
        )
        assert post is not None
        assert post.url == "https://www.reddit.com/r/x/comments/abc123/t/"

    def test_falls_back_to_shortlink_without_permalink(self):
        post = to_raw_post(_sub(permalink=None))
        assert post is not None
        assert post.url == "https://redd.it/abc123"

    def test_parses_created_utc_to_aware_datetime(self):
        post = to_raw_post(_sub(created_utc=1_700_000_000))
        assert post is not None
        assert post.posted_at == datetime.fromtimestamp(1_700_000_000, tz=UTC)

    def test_link_post_has_empty_body(self):
        post = to_raw_post(_sub(selftext=""))
        assert post is not None
        assert post.body == ""

    def test_missing_external_id_dropped(self):
        assert to_raw_post(_sub(external_id=None)) is None
        assert to_raw_post(_sub(external_id="   ")) is None

    def test_no_url_derivable_dropped(self):
        # blank permalink and blank id-derived link is impossible here, but an
        # element with neither a permalink nor an id cannot build a url.
        assert to_raw_post({"external_id": "", "permalink": ""}) is None

    def test_deleted_author_becomes_none(self):
        post = to_raw_post(_sub(author=None))
        assert post is not None
        assert post.author is None

    def test_source_id_flows_through(self):
        post = to_raw_post(_sub(), source_id=9)
        assert post is not None
        assert post.source_id == 9


class TestToRawPosts:
    def test_preserves_order_and_drops_junk(self):
        posts = to_raw_posts(
            [_sub(external_id="1"), {"title": "no id"}, _sub(external_id="2")]
        )
        assert [p.external_id for p in posts] == ["1", "2"]