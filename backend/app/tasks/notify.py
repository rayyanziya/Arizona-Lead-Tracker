"""The notify worker: physically deliver one PENDING Notification.

Split from the scoring pipeline on purpose -- a slow or blocked channel must not
stall scoring, so process_post only enqueues Notification ids and this runs them
separately (one Celery task per notification, retryable in isolation).

deliver() is idempotent twice over: the row only exists because of
UNIQUE(tenant, match, channel), and an already-SENT row is skipped so a Celery
retry after a successful-but-unacked send never double-alerts. Channel senders
are injected, so deliver() unit-tests without HTTP/SMTP; build_senders() wires
the production Telegram/email senders.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models import Notification, NotificationChannel, NotificationStatus
from app.notifiers.base import EmailSender, HttpClient, LeadNotification, NotifyOutcome
from app.notifiers.email import send_email
from app.notifiers.telegram import send_telegram

# A channel sender takes the rendered lead and the row's target (chat id / email)
# and performs the send.
Sender = Callable[[LeadNotification, "str | None"], NotifyOutcome]


def _utcnow() -> datetime:
    return datetime.now(UTC)


def deliver(
    session: Session,
    notification_id: int,
    *,
    senders: dict[NotificationChannel, Sender],
    now: Callable[[], datetime] = _utcnow,
) -> NotifyOutcome:
    """Send one notification and record the outcome. Caller owns the transaction."""
    notif = session.get(Notification, notification_id)
    if notif is None:
        return NotifyOutcome(ok=False, detail=f"notification {notification_id} not found")
    if notif.status is NotificationStatus.SENT:
        return NotifyOutcome(ok=True, detail="already sent")  # idempotent retry guard

    sender = senders.get(notif.channel)
    if sender is None:
        notif.status = NotificationStatus.FAILED
        notif.error = f"no sender for channel {notif.channel.value}"
        session.flush()
        return NotifyOutcome(ok=False, detail=notif.error)

    outcome = sender(_build_lead(notif), notif.target)
    if outcome.ok:
        notif.status = NotificationStatus.SENT
        notif.sent_at = now()
        notif.error = None
    else:
        notif.status = NotificationStatus.FAILED
        notif.error = outcome.detail
    session.flush()
    return outcome


def _build_lead(notif: Notification) -> LeadNotification:
    match = notif.match
    post = match.post
    return LeadNotification(
        platform=post.platform.value,
        url=post.url,
        score=match.ai_score or 0,
        title=post.title,
        body=post.body,
        author=post.author,
        reason=match.ai_reason,
        matched_terms=tuple(match.matched_terms or ()),
    )


def build_senders(
    *,
    telegram_token: str,
    telegram_default_chat: str,
    http: HttpClient,
    smtp_sender: EmailSender,
    smtp_from: str,
) -> dict[NotificationChannel, Sender]:
    """Bind the production channel senders (called by the Celery worker)."""

    def _telegram(lead: LeadNotification, target: str | None) -> NotifyOutcome:
        return send_telegram(
            lead, token=telegram_token, chat_id=target or telegram_default_chat, http=http
        )

    def _email(lead: LeadNotification, target: str | None) -> NotifyOutcome:
        return send_email(lead, recipient=target or "", from_addr=smtp_from, smtp=smtp_sender)

    return {
        NotificationChannel.TELEGRAM: _telegram,
        NotificationChannel.EMAIL: _email,
    }