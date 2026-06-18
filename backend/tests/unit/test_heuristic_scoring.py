"""Unit tests for the free, offline heuristic scorer.

No network, no API key. We pin the buyer/seller classification, the conservative
seller-cancels-buyer rule, the confidence band, and -- crucially -- that
``HeuristicClient`` is a drop-in for the Anthropic client: feeding a
``scoring.build_request`` payload through it yields a response that
``scoring.parse_score`` accepts, so ``score_post`` works unchanged with no key.
"""

from __future__ import annotations

import pytest

from app.services.heuristic_scoring import HeuristicClient, heuristic_score
from app.services.scoring import build_request, parse_score, score_post

pytestmark = pytest.mark.unit


def test_clear_buyer_is_flagged_and_clears_default_threshold():
    r = heuristic_score(None, "Halo, saya butuh aplikasi kasir untuk toko saya")
    assert r.is_buyer is True
    assert r.confidence >= 7  # default NotificationSetting.min_score
    assert "buyer" in r.reason.lower()


def test_english_buyer_phrase_is_flagged():
    r = heuristic_score("Need a CRM", "Looking for a developer who can build a CRM")
    assert r.is_buyer is True
    assert r.confidence >= 7


def test_seller_advert_is_not_a_buyer():
    r = heuristic_score(None, "Jasa pembuatan aplikasi, hubungi kami untuk promo!")
    assert r.is_buyer is False
    assert r.confidence <= 3


def test_seller_cue_cancels_a_lone_buyer_cue():
    # "cari" (buyer) but also "jasa pembuatan" (seller) -> conservative: not a buyer.
    r = heuristic_score(None, "Jasa pembuatan website, cari yang murah? Order now")
    assert r.is_buyer is False


def test_off_topic_post_has_no_signal():
    r = heuristic_score(None, "Selamat pagi semuanya, cuaca cerah hari ini")
    assert r.is_buyer is False
    assert r.confidence == 1


def test_matching_is_case_insensitive():
    r = heuristic_score(None, "LOOKING FOR someone to build me an ERP")
    assert r.is_buyer is True


def test_client_is_drop_in_for_anthropic_via_score_post():
    """The whole point: score_post runs unchanged against the heuristic client."""
    client = HeuristicClient()
    decision = score_post(
        title=None,
        body="Saya butuh aplikasi inventory, ada rekomendasi vendor?",
        content_hash="hash-buyer-1",
        threshold=7,
        client=client,
        cache=None,
    )
    assert decision.score.is_buyer is True
    assert decision.should_notify is True


def test_client_response_parses_as_a_tool_use_block():
    client = HeuristicClient()
    req = build_request(None, "Jasa pembuatan aplikasi murah, DM for price", model="x")
    resp = client.messages.create(**req)
    score = parse_score(resp)  # must not raise; shape matches a real tool_use
    assert score.is_buyer is False
