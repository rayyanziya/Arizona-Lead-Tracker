"""Telegram Bot API notifier."""

from __future__ import annotations

from app.notifiers.base import (
    HttpClient,
    LeadNotification,
    NotifyOutcome,
    NotifyTransportError,
    escape_html,
    first_line,
    run_with_retry,
    snippet,
)

TELEGRAM_API = "https://api.telegram.org"
DEFAULT_TIMEOUT = 10.0


def format_telegram(lead: LeadNotification) -> str:
    title = (lead.title or first_line(lead.body) or "(untitled)").strip()
    lines = [
        f"🎯 New lead · {lead.platform.title()} · score {lead.score}/10",
        f"<b>{escape_html(title)}</b>",
    ]
    body_snip = snippet(lead.body)
    if body_snip and body_snip != title:
        lines.append(escape_html(body_snip))
    if lead.author:
        lines.append(f"👤 {escape_html(lead.author)}")
    if lead.matched_terms:
        lines.append("🧩 " + escape_html(", ".join(lead.matched_terms)))
    if lead.reason:
        lines.append(f"🤖 {escape_html(lead.reason)}")
    lines.append(f"🔗 {lead.url}")
    return "\n".join(lines)


def build_payload(lead: LeadNotification, chat_id: str) -> dict:
    return {
        "chat_id": chat_id,
        "text": format_telegram(lead),
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }


def send_telegram(
    lead: LeadNotification,
    *,
    token: str,
    chat_id: str,
    http: HttpClient,
    max_attempts: int = 3,
    base_wait: float = 0.5,
    timeout: float = DEFAULT_TIMEOUT,
) -> NotifyOutcome:
    url = f"{TELEGRAM_API}/bot{token}/sendMessage"
    payload = build_payload(lead, chat_id)

    def _attempt() -> None:
        try:
            resp = http.post(url, json=payload, timeout=timeout)
        except Exception as exc:  # network/client error -> retryable
            raise NotifyTransportError(str(exc)) from exc
        status = getattr(resp, "status_code", 0)
        if not 200 <= status < 300:
            raise NotifyTransportError(f"telegram HTTP {status}")

    return run_with_retry(_attempt, max_attempts, base_wait)