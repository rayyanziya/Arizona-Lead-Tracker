"""SMTP email notifier.

`from email.message import EmailMessage` resolves to the stdlib package (Python 3
uses absolute imports), not this module, despite the shared name.
"""

from __future__ import annotations

from email.message import EmailMessage

from app.notifiers.base import (
    EmailSender,
    LeadNotification,
    NotifyOutcome,
    NotifyTransportError,
    first_line,
    run_with_retry,
    snippet,
)


def format_email(lead: LeadNotification) -> tuple[str, str]:
    title = (lead.title or first_line(lead.body) or "(untitled)").strip()
    subject = f"[Lead] {lead.platform.title()} · {lead.score}/10 · {title[:60]}"
    lines = [
        f"New lead detected on {lead.platform.title()} (score {lead.score}/10).",
        "",
        f"Title: {title}",
    ]
    body_snip = snippet(lead.body)
    if body_snip:
        lines += ["", body_snip]
    if lead.author:
        lines.append(f"Author: {lead.author}")
    if lead.matched_terms:
        lines.append(f"Matched: {', '.join(lead.matched_terms)}")
    if lead.reason:
        lines.append(f"Why: {lead.reason}")
    lines += ["", f"Link: {lead.url}"]
    return subject, "\n".join(lines)


def build_message(lead: LeadNotification, *, recipient: str, from_addr: str) -> EmailMessage:
    subject, body = format_email(lead)
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = from_addr
    message["To"] = recipient
    message.set_content(body)
    return message


def send_email(
    lead: LeadNotification,
    *,
    recipient: str,
    from_addr: str,
    smtp: EmailSender,
    max_attempts: int = 3,
    base_wait: float = 0.5,
) -> NotifyOutcome:
    message = build_message(lead, recipient=recipient, from_addr=from_addr)

    def _attempt() -> None:
        try:
            smtp.send_message(message)
        except Exception as exc:  # SMTP/socket error -> retryable
            raise NotifyTransportError(str(exc)) from exc

    return run_with_retry(_attempt, max_attempts, base_wait)