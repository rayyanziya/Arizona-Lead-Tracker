"""Shared notifier primitives: the lead payload, formatting helpers, retry.

The two channels (Telegram, email) format and physically send a notification.
They are deliberately transport-pure: the HTTP client and SMTP sender are
injected, and the modules never touch the database. Idempotency ("never alert
the same lead twice on a channel") is enforced one layer up in the pipeline via
the Notification UNIQUE(tenant_id, match_id, channel) constraint -- the notifier
only reports whether the send succeeded.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Any, Protocol

from tenacity import (
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)


class NotifyTransportError(RuntimeError):
    """A retryable failure talking to a channel (network / HTTP / SMTP)."""


@dataclass(frozen=True)
class LeadNotification:
    """Everything a channel needs to render one lead alert."""

    platform: str
    url: str
    score: int
    title: str | None = None
    body: str = ""
    author: str | None = None
    reason: str | None = None
    matched_terms: tuple[str, ...] = ()


@dataclass(frozen=True)
class NotifyOutcome:
    ok: bool
    detail: str = ""


class HttpClient(Protocol):
    def post(self, url: str, *, json: dict[str, Any], timeout: float) -> Any: ...


class EmailSender(Protocol):
    def send_message(self, message: EmailMessage) -> None: ...


def snippet(text: str | None, limit: int = 280) -> str:
    """Whitespace-collapsed preview, truncated on a word boundary with an ellipsis."""
    collapsed = " ".join((text or "").split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[:limit].rsplit(" ", 1)[0] + "…"


def escape_html(text: str) -> str:
    """Escape &, <, > so untrusted post text is safe in Telegram HTML mode."""
    return html.escape(text, quote=False)


def first_line(text: str | None) -> str:
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


def run_with_retry(attempt, max_attempts: int, base_wait: float) -> NotifyOutcome:
    """Run a zero-arg send `attempt`, retrying transport errors with backoff.

    base_wait=0 disables real sleeping (used in tests). Returns ok=False with the
    last error detail once attempts are exhausted instead of raising, so the
    pipeline records a FAILED Notification without a try/except of its own.
    """
    retryer = Retrying(
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(multiplier=base_wait),
        retry=retry_if_exception_type(NotifyTransportError),
        reraise=True,
    )
    try:
        retryer(attempt)
        return NotifyOutcome(ok=True)
    except NotifyTransportError as exc:
        return NotifyOutcome(ok=False, detail=str(exc))