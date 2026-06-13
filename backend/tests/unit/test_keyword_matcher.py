"""Unit tests for the stage-1 keyword matcher (Bahasa Indonesia + English)."""

import pytest

from app.services.keyword_matcher import (
    Keyword,
    MatchType,
    match_keywords,
    matches_any,
    normalize,
)

pytestmark = pytest.mark.unit


class TestNormalize:
    def test_lowercases_and_collapses_whitespace(self):
        assert normalize("  Need   a  CRM ") == "need a crm"

    def test_strips_accents(self):
        # Accent-folding keeps matching robust across messy user input.
        assert normalize("Aplikàsi") == "aplikasi"


class TestPhraseMatch:
    def test_matches_indonesian_phrase(self):
        text = "Halo, saya butuh aplikasi kasir untuk toko saya"
        kws = [Keyword(term="aplikasi kasir", language="id")]
        hits = match_keywords(text, kws)
        assert len(hits) == 1
        assert hits[0].keyword.term == "aplikasi kasir"

    def test_matches_english_substring_case_insensitive(self):
        text = "Anyone know a good CRM for a small business?"
        assert matches_any(text, [Keyword(term="crm")])

    def test_no_match_returns_empty(self):
        assert match_keywords("just chatting about cats", [Keyword(term="erp")]) == []

    def test_empty_inputs_return_empty(self):
        assert match_keywords("", [Keyword(term="erp")]) == []
        assert match_keywords("need erp", []) == []


class TestExactMatch:
    def test_exact_matches_whole_word(self):
        kws = [Keyword(term="erp", match_type=MatchType.EXACT)]
        assert matches_any("we need an ERP system", kws)

    def test_exact_rejects_partial_word(self):
        # "erp" must NOT fire inside "superperp" or "erps".
        kws = [Keyword(term="erp", match_type=MatchType.EXACT)]
        assert not matches_any("the superperp thing", kws)
        assert not matches_any("erps are cool", kws)


class TestRegexMatch:
    def test_regex_match(self):
        kws = [Keyword(term=r"butuh\s+(aplikasi|sistem)", match_type=MatchType.REGEX)]
        assert matches_any("kami butuh sistem hris", kws)


class TestMultipleKeywords:
    def test_multiple_hits_across_languages(self):
        text = "Saya cari jasa pembuatan POS system untuk restoran"
        kws = [
            Keyword(term="jasa pembuatan", language="id"),
            Keyword(term="pos system", language="en"),
            Keyword(term="erp"),  # should not hit
        ]
        terms = {h.keyword.term for h in match_keywords(text, kws)}
        assert terms == {"jasa pembuatan", "pos system"}
