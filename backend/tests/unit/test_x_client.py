"""Unit tests for the X API feed: query building, tweet mapping, block translation.

Unlike PrawSubmissionFeed (which imports PRAW at module top and is integration-only),
ApiTweetFeed imports tweepy lazily and translates blocking errors by exception class
name, so its pure surface runs on the local venv with an injected fake client -- no
tweepy, no network. Only the real client construction stays integration-only.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.monitors.base import MonitorBlocked
from app.monitors.x_client import ApiTweetFeed, build_query


class TestBuildQuery:
    def test_free_text_is_used_as_a_search_query(self):
        assert build_query("looking for a contractor in Phoenix") == (
            "looking for a contractor in Phoenix"
        )

    def test_hashtag_is_used_as_a_search_query(self):
        assert build_query("#phoenixleads") == "#phoenixleads"

    def test_at_handle_becomes_a_from_filter(self):
        assert build_query("@phx_buyer") == "from:phx_buyer"

    def test_x_profile_url_becomes_a_from_filter(self):
        assert build_query("https://x.com/phx_buyer") == "from:phx_buyer"

    def test_twitter_profile_url_becomes_a_from_filter(self):
        assert build_query("https://twitter.com/phx_buyer/") == "from:phx_buyer"

    def test_status_url_is_not_treated_as_a_profile(self):
        # A multi-segment status URL is not a profile, so it is left as a raw
        # query rather than being mangled into from:i.
        q = build_query("https://x.com/i/status/1800000000000000001")
        assert q is not None
        assert not q.startswith("from:")

    def test_blank_identifier_is_none(self):
        assert build_query("") is None
        assert build_query("   ") is None


def _tweet(*, id="1800000000000000001", text="Need a POS app built", author_id="u1",
           created_at=datetime(2026, 6, 15, 12, 0, 0, tzinfo=UTC)):
    return SimpleNamespace(id=id, text=text, author_id=author_id, created_at=created_at)


def _user(id="u1", username="phx_buyer"):
    return SimpleNamespace(id=id, username=username)


class TestToDict:
    def test_maps_tweet_with_resolved_handle(self):
        d = ApiTweetFeed._to_dict(_tweet(), {"u1": _user()})
        assert d["external_id"] == "1800000000000000001"
        assert d["text"] == "Need a POS app built"
        assert d["author"] == "phx_buyer"
        assert d["created_at"] == "2026-06-15T12:00:00+00:00"

    def test_missing_author_is_none(self):
        d = ApiTweetFeed._to_dict(_tweet(author_id="missing"), {"u1": _user()})
        assert d["author"] is None

    def test_missing_created_at_is_none(self):
        d = ApiTweetFeed._to_dict(_tweet(created_at=None), {"u1": _user()})
        assert d["created_at"] is None


class _FakeResponse:
    def __init__(self, data, users=None):
        self.data = data
        self.includes = {"users": users} if users else {}


class _FakeClient:
    def __init__(self, response=None, raises=None):
        self._response = response
        self._raises = raises
        self.calls: list[dict] = []

    def search_recent_tweets(self, **kwargs):
        self.calls.append(kwargs)
        if self._raises is not None:
            raise self._raises
        return self._response


class TooManyRequests(Exception):
    """Stands in for tweepy.TooManyRequests (matched by class name, so it must
    share the real class's name)."""


class TestFetch:
    def test_maps_response_data_and_includes(self):
        client = _FakeClient(_FakeResponse([_tweet()], [_user()]))
        feed = ApiTweetFeed("from:phx_buyer", bearer_token="t", client=client)
        rows = feed.fetch()
        assert [r["external_id"] for r in rows] == ["1800000000000000001"]
        assert rows[0]["author"] == "phx_buyer"

    def test_empty_response_yields_no_rows(self):
        feed = ApiTweetFeed("q", bearer_token="t", client=_FakeClient(_FakeResponse(None)))
        assert feed.fetch() == []

    def test_caps_max_results_into_the_api_range(self):
        client = _FakeClient(_FakeResponse([]))
        ApiTweetFeed("q", bearer_token="t", max_results=500, client=client).fetch()
        assert client.calls[0]["max_results"] == 100
        client2 = _FakeClient(_FakeResponse([]))
        ApiTweetFeed("q", bearer_token="t", max_results=3, client=client2).fetch()
        assert client2.calls[0]["max_results"] == 10

    def test_rate_limit_becomes_monitor_blocked(self):
        client = _FakeClient(raises=TooManyRequests("429"))
        feed = ApiTweetFeed("q", bearer_token="t", client=client)
        with pytest.raises(MonitorBlocked):
            feed.fetch()

    def test_close_drops_the_client(self):
        feed = ApiTweetFeed("q", bearer_token="t", client=_FakeClient(_FakeResponse([])))
        feed.close()
        assert feed._client is None
