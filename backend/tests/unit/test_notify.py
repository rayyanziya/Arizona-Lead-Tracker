"""Unit tests for the notify worker (deliver) and sender wiring.

In-memory SQLite seeds a PENDING Notification with its Post + Match; channel
senders are faked. deliver() dispatches by channel, records SENT/FAILED, and is
idempotent on an already-SENT row (Celery may retry after a successful send).
build_senders() is checked for correct target -> chat_id / recipient wiring.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import (
    Base,
    Match,
    MatchStatus,
    Notification,
    NotificationChannel,
    NotificationStatus,
    Post,
    Tenant,
)
from app.notifiers.base import NotifyOutcome
from app.tasks.notify import build_senders, deliver

pytestmark = pytest.mark.unit

FIXED_NOW = datetime(2026, 6, 13, 12, 0, tzinfo=UTC)


class FakeSender:
    def __init__(self, outcome=None):
        self.outcome = outcome or NotifyOutcome(ok=True)
        self.calls: list = []

    def __call__(self, lead, target):
        self.calls.append((lead, target))
        return self.outcome


def _seed(
    *, status=NotificationStatus.PENDING, channel=NotificationChannel.TELEGRAM, target="chat-9"
):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = Session(engine)
    s.add(Tenant(id=1, name="Acme", slug="acme"))
    s.add(
        Post(
            id=1,
            tenant_id=1,
            platform="facebook",
            external_id="p1",
            url="http://x/p1",
            title="Butuh aplikasi kasir",
            body="Cari developer untuk POS toko.",
            author="Budi",
            content_hash="h1",
        )
    )
    s.add(
        Match(
            id=1,
            tenant_id=1,
            post_id=1,
            matched_term="aplikasi kasir",
            matched_terms=["aplikasi kasir"],
            ai_score=9,
            ai_is_buyer=True,
            ai_reason="asks to build a POS",
            status=MatchStatus.PENDING,
        )
    )
    s.add(
        Notification(id=1, tenant_id=1, match_id=1, channel=channel, status=status, target=target)
    )
    s.flush()
    return s


class TestDeliver:
    def test_delivers_and_marks_sent(self):
        s = _seed()
        tg = FakeSender()
        out = deliver(s, 1, senders={NotificationChannel.TELEGRAM: tg}, now=lambda: FIXED_NOW)
        assert out.ok is True
        notif = s.get(Notification, 1)
        assert notif.status is NotificationStatus.SENT
        # SQLite drops tz on DateTime(timezone=True); compare tz-tolerantly (aware on PG).
        assert notif.sent_at.replace(tzinfo=UTC) == FIXED_NOW
        assert len(tg.calls) == 1
        _, target = tg.calls[0]
        assert target == "chat-9"

    def test_builds_lead_from_post_and_match(self):
        s = _seed()
        tg = FakeSender()
        deliver(s, 1, senders={NotificationChannel.TELEGRAM: tg}, now=lambda: FIXED_NOW)
        lead, _ = tg.calls[0]
        assert lead.platform == "facebook"
        assert lead.url == "http://x/p1"
        assert lead.score == 9
        assert lead.author == "Budi"
        assert lead.reason == "asks to build a POS"
        assert lead.matched_terms == ("aplikasi kasir",)

    def test_failed_send_marks_failed_with_error(self):
        s = _seed()
        tg = FakeSender(NotifyOutcome(ok=False, detail="429 rate limited"))
        out = deliver(s, 1, senders={NotificationChannel.TELEGRAM: tg}, now=lambda: FIXED_NOW)
        assert out.ok is False
        notif = s.get(Notification, 1)
        assert notif.status is NotificationStatus.FAILED
        assert "429" in notif.error
        assert notif.sent_at is None

    def test_already_sent_is_idempotent(self):
        s = _seed(status=NotificationStatus.SENT)
        tg = FakeSender()
        out = deliver(s, 1, senders={NotificationChannel.TELEGRAM: tg}, now=lambda: FIXED_NOW)
        assert out.ok is True
        assert tg.calls == []  # not re-sent

    def test_unknown_notification_id(self):
        s = _seed()
        out = deliver(s, 999, senders={}, now=lambda: FIXED_NOW)
        assert out.ok is False
        assert "not found" in out.detail

    def test_no_sender_for_channel_marks_failed(self):
        s = _seed(channel=NotificationChannel.EMAIL)
        out = deliver(
            s, 1, senders={NotificationChannel.TELEGRAM: FakeSender()}, now=lambda: FIXED_NOW
        )
        assert out.ok is False
        assert s.get(Notification, 1).status is NotificationStatus.FAILED


class _Resp:
    status_code = 200


class FakeHttp:
    def __init__(self):
        self.calls: list = []

    def post(self, url, json=None, timeout=None):
        self.calls.append({"url": url, "json": json})
        return _Resp()


class FakeSmtp:
    def __init__(self):
        self.sent: list = []

    def send_message(self, message):
        self.sent.append(message)


def _lead():
    from app.notifiers.base import LeadNotification

    return LeadNotification(platform="facebook", url="http://x/1", score=9, body="hi")


class TestBuildSenders:
    def test_telegram_sender_uses_target_as_chat_id(self):
        http = FakeHttp()
        senders = build_senders(
            telegram_token="TOK",
            telegram_default_chat="default",
            http=http,
            smtp_sender=FakeSmtp(),
            smtp_from="bot@x",
        )
        out = senders[NotificationChannel.TELEGRAM](_lead(), "chat-9")
        assert out.ok is True
        assert http.calls[0]["json"]["chat_id"] == "chat-9"

    def test_telegram_sender_falls_back_to_default_chat(self):
        http = FakeHttp()
        senders = build_senders(
            telegram_token="TOK",
            telegram_default_chat="default",
            http=http,
            smtp_sender=FakeSmtp(),
            smtp_from="bot@x",
        )
        senders[NotificationChannel.TELEGRAM](_lead(), None)
        assert http.calls[0]["json"]["chat_id"] == "default"

    def test_email_sender_sends_to_target(self):
        smtp = FakeSmtp()
        senders = build_senders(
            telegram_token="TOK",
            telegram_default_chat="default",
            http=FakeHttp(),
            smtp_sender=smtp,
            smtp_from="bot@x",
        )
        out = senders[NotificationChannel.EMAIL](_lead(), "me@here.com")
        assert out.ok is True
        assert smtp.sent[0]["To"] == "me@here.com"
