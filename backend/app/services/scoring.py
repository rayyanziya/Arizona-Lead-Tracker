"""Stage-2 AI filter: is this post author a buyer, and how sure are we?

Keyword matching (stage 1) is cheap but noisy -- it fires on sellers advertising
the very services we sell. This stage spends Claude tokens only on posts that
already matched, and asks one question: is the AUTHOR looking to buy/hire custom
software, with what confidence (1-10)? We notify only when is_buyer AND
confidence >= the tenant's threshold (NotificationSetting.min_score, default 7).

Design for testability and thrift:
  * The Anthropic client is injected (Protocol-typed), so units run against a
    fake -- no API key, no network. The module never imports `anthropic`.
  * Structured output is forced via a single tool (tool_choice), so we parse a
    typed dict, never free-form prose.
  * The model score is cached by content_hash (tenant-agnostic: the post text
    alone decides buyer-vs-seller). The tenant threshold is applied AFTER the
    cache, so one model call is shared across tenants and reposts.
"""

from __future__ import annotations

import enum  # noqa: F401  (kept for parity with sibling services; harmless)
import json
from dataclasses import dataclass
from typing import Any, Protocol

DEFAULT_MODEL = "claude-haiku-4-5"
DEFAULT_THRESHOLD = 7
DEFAULT_MAX_TOKENS = 512
SCORE_CACHE_TTL_SECONDS = 30 * 24 * 3600

TOOL_NAME = "record_lead_assessment"

SYSTEM_PROMPT = (
    "You are a lead-qualification classifier for a custom-software agency. You "
    "read one social-media post (Bahasa Indonesia or English) and decide whether "
    "the AUTHOR is looking to BUY or HIRE custom software services -- e.g. ERP, "
    "HRIS, CRM, POS systems, inventory or attendance apps, business apps, or "
    "websites.\n\n"
    "Set is_buyer=true ONLY when the author themselves wants such a solution "
    "built, or is asking for a developer/vendor recommendation. Set is_buyer=false "
    "for sellers, agencies or freelancers advertising their services, job seekers, "
    "course or promo posts, and anything off-topic. When unsure, prefer false -- a "
    "false alarm wastes an outreach.\n\n"
    "confidence (1-10) is how sure you are this is a genuine buyer with real "
    "intent: 1-3 vague or unlikely, 4-6 possible, 7-8 likely, 9-10 explicit. "
    "Always answer by calling the record_lead_assessment tool."
)

SCORING_TOOL: dict[str, Any] = {
    "name": TOOL_NAME,
    "description": (
        "Record whether the post author is looking to buy or hire custom software "
        "services, and how confident that assessment is."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "is_buyer": {
                "type": "boolean",
                "description": "True if the author wants to hire; false if selling or unrelated.",
            },
            "confidence": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10,
                "description": "Confidence (1-10) that this is a genuine buyer with real intent.",
            },
            "reason": {
                "type": "string",
                "description": "One concise sentence justifying the assessment.",
            },
        },
        "required": ["is_buyer", "confidence", "reason"],
    },
}


class ScoringError(RuntimeError):
    """The model response could not be parsed into a valid assessment."""


@dataclass(frozen=True)
class Score:
    is_buyer: bool
    confidence: int  # 1-10
    reason: str


@dataclass(frozen=True)
class ScoringDecision:
    score: Score
    should_notify: bool
    from_cache: bool = False


class MessagesLike(Protocol):
    def create(self, **kwargs: Any) -> Any: ...


class AnthropicLike(Protocol):
    """Structural type satisfied by `anthropic.Anthropic` (and our test fake)."""

    messages: MessagesLike


class ScoreCache(Protocol):
    def get(self, key: str) -> str | None: ...
    def set(self, key: str, value: str, ttl_seconds: int) -> None: ...


def build_request(
    title: str | None,
    body: str,
    *,
    model: str,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> dict[str, Any]:
    """Build the kwargs for client.messages.create(), forcing the scoring tool."""
    text = f"{title}\n\n{body}".strip() if title else (body or "").strip()
    return {
        "model": model,
        "max_tokens": max_tokens,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": f"Post to assess:\n\n{text}"}],
        "tools": [SCORING_TOOL],
        "tool_choice": {"type": "tool", "name": TOOL_NAME},
    }


def parse_score(response: Any) -> Score:
    """Extract and validate the forced tool_use block from a Messages response."""
    block = _tool_use_block(response)
    if block is None:
        raise ScoringError("model did not call the scoring tool")
    data = block.input
    try:
        is_buyer = bool(data["is_buyer"])
        confidence = int(data["confidence"])
        reason = str(data["reason"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ScoringError(f"malformed scoring payload: {data!r}") from exc
    confidence = max(1, min(10, confidence))  # tolerate a model that overshoots
    return Score(is_buyer=is_buyer, confidence=confidence, reason=reason)


def decide(score: Score, threshold: int) -> bool:
    """Notify only for a buyer whose confidence clears the tenant threshold."""
    return score.is_buyer and score.confidence >= threshold


def score_post(
    *,
    title: str | None,
    body: str,
    content_hash: str,
    threshold: int = DEFAULT_THRESHOLD,
    client: AnthropicLike,
    cache: ScoreCache | None = None,
    model: str = DEFAULT_MODEL,
) -> ScoringDecision:
    """Score a post (cached by content_hash) and apply the tenant threshold."""
    key = f"score:{content_hash}"
    if cache is not None:
        cached = cache.get(key)
        if cached is not None:
            score = _deserialize(cached)
            return ScoringDecision(score, decide(score, threshold), from_cache=True)

    response = client.messages.create(**build_request(title, body, model=model))
    score = parse_score(response)
    if cache is not None:
        cache.set(key, _serialize(score), SCORE_CACHE_TTL_SECONDS)
    return ScoringDecision(score, decide(score, threshold), from_cache=False)


def _tool_use_block(response: Any) -> Any | None:
    for block in getattr(response, "content", []) or []:
        if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == TOOL_NAME:
            return block
    return None


def _serialize(score: Score) -> str:
    return json.dumps(
        {"is_buyer": score.is_buyer, "confidence": score.confidence, "reason": score.reason}
    )


def _deserialize(blob: str) -> Score:
    data = json.loads(blob)
    return Score(is_buyer=data["is_buyer"], confidence=data["confidence"], reason=data["reason"])