"""Pure tests for Facebook group-identifier parsing.

The dashboard lets an operator paste a group however they have it -- a full
desktop URL, a mobile URL, a bare numeric id, or a vanity slug. Both the API
(which validates a new source) and the scraper (which targets the group) must
agree on what a "group id" is, so the parser is pure and tested here with no
network and no Playwright.
"""

from __future__ import annotations

import pytest

from app.services.facebook_group import facebook_group_id

pytestmark = pytest.mark.unit


class TestAcceptsAndExtracts:
    def test_full_desktop_url_numeric(self):
        assert facebook_group_id("https://www.facebook.com/groups/123456") == "123456"

    def test_full_desktop_url_vanity_slug(self):
        assert facebook_group_id("https://www.facebook.com/groups/phoenix.umkm") == "phoenix.umkm"

    def test_mobile_url(self):
        assert facebook_group_id("https://m.facebook.com/groups/789/") == "789"

    def test_url_without_scheme(self):
        assert facebook_group_id("facebook.com/groups/555") == "555"

    def test_relative_groups_path(self):
        assert facebook_group_id("groups/4242") == "4242"

    def test_strips_query_and_hash_and_trailing_path(self):
        assert facebook_group_id("https://facebook.com/groups/99/?ref=bookmarks#x") == "99"

    def test_bare_numeric_id(self):
        assert facebook_group_id("123456789") == "123456789"

    def test_bare_vanity_slug(self):
        assert facebook_group_id("phoenix-real-estate") == "phoenix-real-estate"

    def test_trims_surrounding_whitespace(self):
        assert facebook_group_id("  https://facebook.com/groups/77  ") == "77"


class TestRejects:
    def test_empty(self):
        assert facebook_group_id("") is None

    def test_whitespace_only(self):
        assert facebook_group_id("   ") is None

    def test_free_text_with_spaces(self):
        assert facebook_group_id("looking for a group") is None

    def test_a_non_group_facebook_url(self):
        assert facebook_group_id("https://facebook.com/somepage") is None

    def test_groups_url_with_empty_id(self):
        assert facebook_group_id("https://facebook.com/groups/") is None