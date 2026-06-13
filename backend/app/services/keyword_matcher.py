"""Stage-1 keyword filter (Bahasa Indonesia + English).

This is the cheap pre-filter that runs before any Claude scoring: only posts
that hit a tenant keyword are worth spending tokens on. Matching is
accent- and case-insensitive and supports three match types:

  * PHRASE  -- substring match (default)
  * EXACT   -- whole-word match (won't fire inside a larger word)
  * REGEX   -- a tenant-supplied regular expression
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import Enum


class MatchType(str, Enum):
    EXACT = "exact"
    PHRASE = "phrase"
    REGEX = "regex"


@dataclass(frozen=True)
class Keyword:
    term: str
    language: str = "any"  # "id" | "en" | "any"
    match_type: MatchType = MatchType.PHRASE
    id: int | None = None


@dataclass(frozen=True)
class KeywordHit:
    keyword: Keyword
    matched_text: str
    start: int
    end: int


def normalize(text: str) -> str:
    """Lowercase, strip combining accents, and collapse whitespace."""
    decomposed = unicodedata.normalize("NFKD", text)
    without_accents = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", without_accents).strip().lower()


def _compile(keyword: Keyword) -> re.Pattern[str]:
    if keyword.match_type is MatchType.REGEX:
        return re.compile(keyword.term, re.IGNORECASE)
    term = re.escape(normalize(keyword.term))
    if keyword.match_type is MatchType.EXACT:
        # \w boundaries so "erp" does not match inside "superperp" / "erps".
        return re.compile(rf"(?<!\w){term}(?!\w)", re.IGNORECASE)
    return re.compile(term, re.IGNORECASE)


def match_keywords(text: str, keywords: list[Keyword]) -> list[KeywordHit]:
    """Return every keyword hit found in *text*. Empty list -> post filtered out."""
    if not text or not keywords:
        return []
    haystack = normalize(text)
    hits: list[KeywordHit] = []
    for keyword in keywords:
        for match in _compile(keyword).finditer(haystack):
            hits.append(
                KeywordHit(
                    keyword=keyword,
                    matched_text=match.group(0),
                    start=match.start(),
                    end=match.end(),
                )
            )
    return hits


def matches_any(text: str, keywords: list[Keyword]) -> bool:
    return bool(match_keywords(text, keywords))
