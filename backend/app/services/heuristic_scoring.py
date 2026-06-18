"""Free, offline stage-2 scorer -- a drop-in for the Claude client.

When no Anthropic key/credits are configured we still want the pipeline to
qualify leads rather than fail. This module classifies buyer-vs-seller intent
with plain phrase matching (English + Bahasa Indonesia) -- no network, no spend.

It is deliberately shaped to satisfy ``scoring.AnthropicLike``: ``HeuristicClient``
exposes ``messages.create(**kwargs)`` and returns an object whose ``.content``
holds a single ``tool_use`` block matching ``scoring.TOOL_NAME``. So the existing
``score_post`` (caching, threshold, decide) and the pipeline use it unchanged --
``jobs.py`` simply injects this client instead of ``anthropic.Anthropic`` when no
key is set. Signal is coarser than Claude's, so we lean conservative (a single
seller cue cancels a buyer cue) to keep false alarms down.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.services.scoring import TOOL_NAME

# Author is looking to BUY/HIRE a custom-software solution.
BUYER_PHRASES: tuple[str, ...] = (
    # Bahasa Indonesia
    "butuh", "membutuhkan", "cari", "mencari", "nyari", "rekomendasi",
    "minta rekomendasi", "ada yang bisa buat", "ada yg bisa buat", "bisa buatkan",
    "mau bikin", "mau buat", "ingin buat", "ingin membuat", "pengen bikin",
    "tolong buatkan", "tolong buatin", "siapa yang bisa", "minta saran",
    # English
    "looking for", "need a", "need an", "i need", "anyone who can",
    "anyone can build", "recommend a", "recommendation for", "want to build",
    "want to make", "in need of", "who can develop", "who can build", "seeking",
    "build me", "is there anyone",
)

# Author is SELLING/advertising a service (or it is an ad/promo). Cancels buyer cues.
SELLER_PHRASES: tuple[str, ...] = (
    # Bahasa Indonesia
    "jasa pembuatan", "menerima jasa", "terima jasa", "open jasa", "jasa bikin",
    "menyediakan jasa", "kami menyediakan", "terima pesanan", "menerima pesanan",
    "promo", "diskon", "harga mulai", "mulai dari rp", "hubungi kami",
    "hubungi admin", "wa admin", "order sekarang", "portofolio kami", "jasa website",
    "jasa aplikasi",
    # English
    "we offer", "we provide", "our services", "our service", "contact us",
    "hire us", "dm for", "price starts", "starting at", "we build", "we develop",
    "our portfolio", "order now", "we specialize",
)


@dataclass(frozen=True)
class HeuristicResult:
    is_buyer: bool
    confidence: int  # 1-10
    reason: str


def _matches(text: str, phrases: tuple[str, ...]) -> list[str]:
    """Distinct phrases present in text, matched on word boundaries, case-folded."""
    hay = text.lower()
    found = []
    for phrase in phrases:
        # \b around the whole phrase; phrases may contain spaces, that is fine.
        if re.search(rf"\b{re.escape(phrase)}\b", hay):
            found.append(phrase)
    return found


def heuristic_score(title: str | None, body: str) -> HeuristicResult:
    """Classify buyer intent from post text alone. Pure; no I/O."""
    text = f"{title or ''}\n{body or ''}"
    buyers = _matches(text, BUYER_PHRASES)
    sellers = _matches(text, SELLER_PHRASES)
    b, s = len(buyers), len(sellers)

    if b == 0:
        conf = 2 if s else 1
        reason = (
            f"heuristic: no buyer signals; {s} seller signal(s)"
            if s
            else "heuristic: no buyer-intent signals found"
        )
        return HeuristicResult(False, conf, reason)

    if s >= b:
        return HeuristicResult(
            False,
            3,
            f"heuristic: {s} seller signal(s) outweigh {b} buyer signal(s)",
        )

    confidence = max(1, min(9, 5 + 2 * b - s))
    sample = ", ".join(buyers[:3])
    reason = f"heuristic: {b} buyer signal(s) ({sample})"
    if s:
        reason += f", {s} seller signal(s)"
    return HeuristicResult(True, confidence, reason)


# --- Anthropic-shaped adapter ----------------------------------------------
# These tiny shims let score_post()/parse_score() consume the heuristic exactly
# as they consume a real Claude tool_use response -- no special-casing upstream.
class _ToolUseBlock:
    type = "tool_use"

    def __init__(self, name: str, data: dict[str, Any]) -> None:
        self.name = name
        self.input = data


class _Response:
    def __init__(self, data: dict[str, Any]) -> None:
        self.content = [_ToolUseBlock(TOOL_NAME, data)]


def _text_from_request(kwargs: dict[str, Any]) -> str:
    """Recover the post text from the messages built by scoring.build_request."""
    messages = kwargs.get("messages") or []
    if messages and isinstance(messages[0], dict):
        return str(messages[0].get("content", ""))
    return ""


class _HeuristicMessages:
    def create(self, **kwargs: Any) -> _Response:
        result = _classify_text(_text_from_request(kwargs))
        return _Response(
            {
                "is_buyer": result.is_buyer,
                "confidence": result.confidence,
                "reason": result.reason,
            }
        )


class HeuristicClient:
    """Satisfies scoring.AnthropicLike; scores for free instead of calling Claude."""

    def __init__(self) -> None:
        self.messages = _HeuristicMessages()


def _classify_text(text: str) -> HeuristicResult:
    # build_request prefixes "Post to assess:\n\n"; that preamble is neutral to
    # the phrase lists, so we score the whole user message as-is.
    return heuristic_score(None, text)
