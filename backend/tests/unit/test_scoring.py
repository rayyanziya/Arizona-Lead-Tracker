"""Unit tests for the Claude buyer/seller scoring service.

No network, no API key: a fake Anthropic-shaped client returns a canned
tool_use block, and a dict-backed fake stands in for the score cache. We pin the
forced-tool request shape, the structured-output parsing (including clamping a
misbehaving confidence), the threshold decision, and the content_hash cache --
crucially that the cached *score* is reused across tenants while each tenant's
*threshold* is applied independently.
"""

from __future__ import annotations

import pytest

from app.services.scoring import (
    TOOL_NAME,
    Score,
    ScoringError,
    build_request,
    decide,
    parse_score,
    score_post,
)

pytestmark = pytest.mark.unit


# --- Anthropic-shaped fakes ------------------------------------------------
class _ToolUse:
    type = "tool_use"

    def __init__(self, name, data):
        self.name = name
        self.input = data


class _Text:
    type = "text"

    def __init__(self, text):
        self.text = text


class _Response:
    def __init__(self, content):
        self.content = content


class FakeMessages:
    def __init__(self, payload, name=TOOL_NAME):
        self._payload = payload
        self._name = name
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        # A real forced-tool response leads with optional text, then the tool.
        return _Response([_Text("..."), _ToolUse(self._name, self._payload)])


class FakeClient:
    def __init__(self, payload, name=TOOL_NAME):
        self.messages = FakeMessages(payload, name)


def _payload(is_buyer=True, confidence=9, reason="asks for a custom POS app"):
    return {"is_buyer": is_buyer, "confidence": confidence, "reason": reason}


class TestBuildRequest:
    def test_forces_our_tool(self):
        req = build_request("t", "b", model="m")
        assert req["tool_choice"] == {"type": "tool", "name": TOOL_NAME}
        assert req["tools"][0]["name"] == TOOL_NAME

    def test_uses_given_model(self):
        assert build_request("t", "b", model="claude-test")["model"] == "claude-test"

    def test_includes_post_text(self):
        req = build_request("My title", "the body here", model="m")
        blob = str(req["messages"])
        assert "My title" in blob and "the body here" in blob

    def test_title_optional(self):
        req = build_request(None, "body only", model="m")
        assert "body only" in str(req["messages"])


class TestParseScore:
    def test_parses_well_formed_tool_use(self):
        score = parse_score(_Response([_ToolUse(TOOL_NAME, _payload())]))
        assert score == Score(is_buyer=True, confidence=9, reason="asks for a custom POS app")

    def test_clamps_out_of_range_confidence(self):
        def conf(c):
            return parse_score(_Response([_ToolUse(TOOL_NAME, _payload(confidence=c))])).confidence

        assert conf(99) == 10
        assert conf(0) == 1

    def test_raises_when_no_tool_use_block(self):
        with pytest.raises(ScoringError):
            parse_score(_Response([_Text("I refuse to use the tool")]))

    def test_raises_on_malformed_payload(self):
        with pytest.raises(ScoringError):
            parse_score(_Response([_ToolUse(TOOL_NAME, {"confidence": 5})]))  # missing is_buyer


class TestDecide:
    def test_buyer_at_or_above_threshold_notifies(self):
        assert decide(Score(True, 7, "r"), threshold=7) is True

    def test_buyer_below_threshold_does_not(self):
        assert decide(Score(True, 6, "r"), threshold=7) is False

    def test_non_buyer_never_notifies(self):
        assert decide(Score(False, 10, "r"), threshold=7) is False


class TestScorePost:
    def test_scores_and_decides_via_the_model(self):
        client = FakeClient(_payload(is_buyer=True, confidence=9))
        d = score_post(title="t", body="b", content_hash="h1", threshold=7, client=client)
        assert d.score.confidence == 9
        assert d.should_notify is True
        assert d.from_cache is False
        assert len(client.messages.calls) == 1

    def test_non_buyer_does_not_notify(self):
        client = FakeClient(_payload(is_buyer=False, confidence=8))
        d = score_post(title="t", body="b", content_hash="h", threshold=7, client=client)
        assert d.should_notify is False

    def test_caches_score_by_content_hash(self):
        client = FakeClient(_payload(confidence=9))
        cache = {}

        class C:
            def get(self, k):
                return cache.get(k)

            def set(self, k, v, ttl_seconds):
                cache[k] = v

        c = C()
        first = score_post(title="t", body="b", content_hash="dup", client=client, cache=c)
        second = score_post(title="t", body="b", content_hash="dup", client=client, cache=c)
        assert first.from_cache is False
        assert second.from_cache is True
        assert len(client.messages.calls) == 1  # model hit once, not twice

    def test_threshold_applied_after_cache_per_tenant(self):
        # Same cached score, two different tenant thresholds -> different decisions.
        client = FakeClient(_payload(is_buyer=True, confidence=8))
        cache = {}

        class C:
            def get(self, k):
                return cache.get(k)

            def set(self, k, v, ttl_seconds):
                cache[k] = v

        c = C()
        lenient = score_post(
            title="t", body="b", content_hash="x", threshold=7, client=client, cache=c
        )
        strict = score_post(
            title="t", body="b", content_hash="x", threshold=10, client=client, cache=c
        )
        assert lenient.should_notify is True
        assert strict.should_notify is False
        assert strict.from_cache is True
        assert len(client.messages.calls) == 1  # one model call shared across thresholds

    def test_works_without_cache(self):
        client = FakeClient(_payload(confidence=9))
        d1 = score_post(title="t", body="b", content_hash="h", client=client, cache=None)
        score_post(title="t", body="b", content_hash="h", client=client, cache=None)
        assert d1.should_notify is True
        assert len(client.messages.calls) == 2  # no cache -> model called each time