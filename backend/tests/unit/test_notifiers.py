"""Unit tests for the notification channels (Telegram + email).

No real network or SMTP: the HTTP client and the SMTP sender are injected and
faked. Retries use tenacity with base_wait=0 so the flaky-then-succeeds and
always-fails paths run instantly. Formatting is pure and asserted directly,
including HTML escaping of untrusted post text in the Telegram message.
"""

from __future__ import annotations

import pytest

from app.notifiers.base import LeadNotification, snippet
from app.notifiers.email import build_message, format_email, send_email
from app.notifiers.telegram import TELEGRAM_API, build_payload, format_telegram, send_telegram

pytestmark = pytest.mark.unit


def _lead(**overrides) -> LeadNotification:
    base = {
        "platform": "facebook",
        "url": "https://fb.com/groups/1/posts/9",
        "score": 9,
        "title": "Butuh aplikasi kasir",
        "body": "Cari developer untuk bikin POS toko saya.",
        "author": "Budi",
        "reason": "explicitly asks to build a POS",
        "matched_terms": ("aplikasi kasir", "pos"),
    }
    base.update(overrides)
    return LeadNotification(**base)


# --- transport fakes -------------------------------------------------------
class _Resp:
    def __init__(self, status):
        self.status_code = status
        self.text = "ok" if status == 200 else "err"


class FakeHttp:
    def __init__(self, fail_times=0, status=200):
        self.fail_times = fail_times
        self.status = status
        self.calls: list[dict] = []

    def post(self, url, json=None, timeout=None):
        self.calls.append({"url": url, "json": json, "timeout": timeout})
        if len(self.calls) <= self.fail_times:
            raise ConnectionError("network down")
        return _Resp(self.status)


class FakeSmtp:
    def __init__(self, fail_times=0):
        self.fail_times = fail_times
        self.attempts = 0
        self.sent: list = []

    def send_message(self, message):
        self.attempts += 1
        if self.attempts <= self.fail_times:
            raise OSError("smtp unavailable")
        self.sent.append(message)


class TestSnippet:
    def test_short_text_is_unchanged(self):
        assert snippet("hello world") == "hello world"

    def test_collapses_whitespace(self):
        assert snippet("a   b\n\nc") == "a b c"

    def test_long_text_is_truncated_with_ellipsis(self):
        out = snippet("word " * 200, limit=280)
        assert out.endswith("…")
        assert len(out) <= 281


class TestFormatTelegram:
    def test_includes_platform_score_and_link(self):
        out = format_telegram(_lead())
        assert "Facebook" in out
        assert "9/10" in out
        assert "https://fb.com/groups/1/posts/9" in out

    def test_escapes_untrusted_html(self):
        out = format_telegram(_lead(title="<script>alert(1)</script>"))
        assert "&lt;script&gt;" in out
        assert "<script>" not in out

    def test_includes_matched_terms_and_reason(self):
        out = format_telegram(_lead())
        assert "aplikasi kasir" in out
        assert "explicitly asks to build a POS" in out

    def test_build_payload_targets_chat_with_html(self):
        payload = build_payload(_lead(), chat_id="12345")
        assert payload["chat_id"] == "12345"
        assert payload["parse_mode"] == "HTML"
        assert payload["text"] == format_telegram(_lead())


class TestSendTelegram:
    def _send(self, http, **kw):
        return send_telegram(
            _lead(), token="TOK", chat_id="CHAT", http=http, base_wait=0, **kw
        )

    def test_happy_path_posts_once(self):
        http = FakeHttp()
        out = self._send(http)
        assert out.ok is True
        assert len(http.calls) == 1
        assert "botTOK/sendMessage" in http.calls[0]["url"]
        assert http.calls[0]["url"].startswith(TELEGRAM_API)
        assert http.calls[0]["json"]["chat_id"] == "CHAT"

    def test_retries_then_succeeds(self):
        http = FakeHttp(fail_times=2)
        out = self._send(http, max_attempts=3)
        assert out.ok is True
        assert len(http.calls) == 3

    def test_gives_up_after_max_attempts(self):
        http = FakeHttp(fail_times=99)
        out = self._send(http, max_attempts=3)
        assert out.ok is False
        assert out.detail
        assert len(http.calls) == 3

    def test_non_2xx_status_is_a_failure(self):
        http = FakeHttp(status=500)
        out = self._send(http, max_attempts=2)
        assert out.ok is False
        assert len(http.calls) == 2


class TestFormatEmail:
    def test_subject_carries_platform_and_score(self):
        subject, _ = format_email(_lead())
        assert "Facebook" in subject
        assert "9/10" in subject

    def test_body_carries_link_snippet_and_reason(self):
        _, body = format_email(_lead())
        assert "https://fb.com/groups/1/posts/9" in body
        assert "POS toko" in body
        assert "explicitly asks to build a POS" in body
        assert "Budi" in body


class TestBuildMessage:
    def test_headers_and_body(self):
        msg = build_message(_lead(), recipient="me@here.com", from_addr="bot@there.com")
        assert msg["To"] == "me@here.com"
        assert msg["From"] == "bot@there.com"
        assert "9/10" in msg["Subject"]
        assert "https://fb.com/groups/1/posts/9" in msg.get_content()


class TestSendEmail:
    def _send(self, smtp, **kw):
        return send_email(
            _lead(), recipient="me@here.com", from_addr="bot@there.com",
            smtp=smtp, base_wait=0, **kw,
        )

    def test_happy_path_sends_once(self):
        smtp = FakeSmtp()
        out = self._send(smtp)
        assert out.ok is True
        assert smtp.attempts == 1
        assert len(smtp.sent) == 1

    def test_retries_then_succeeds(self):
        smtp = FakeSmtp(fail_times=2)
        out = self._send(smtp, max_attempts=3)
        assert out.ok is True
        assert smtp.attempts == 3

    def test_gives_up_after_max_attempts(self):
        smtp = FakeSmtp(fail_times=99)
        out = self._send(smtp, max_attempts=3)
        assert out.ok is False
        assert smtp.attempts == 3
        assert out.detail