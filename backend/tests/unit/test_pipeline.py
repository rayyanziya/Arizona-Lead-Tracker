"""Unit tests for the process_post orchestrator (dedup -> match -> score -> notify).

In-memory SQLite is the DB; a fake Anthropic-shaped client supplies the score;
enqueue is a recorder. No Celery, Redis, or network. The orchestrator is the
integration seam where a bug means a missed lead or a double alert, so the tests
lean on behavior: what got stored, what got enqueued, and idempotency on
re-processing.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models import (
    Base,
    Match,
    MatchStatus,
    Notification,
    NotificationChannel,
    NotificationSetting,
    Post,
    Tenant,
)
from app.schemas.raw_post import RawPost
from app.services import keyword_matcher as km
from app.services.scoring import TOOL_NAME
from app.tasks.pipeline import PipelineStatus, process_post

pytestmark = pytest.mark.unit


# --- Anthropic-shaped fake -------------------------------------------------
class _ToolUse:
    type = "tool_use"

    def __init__(self, name, data):
        self.name = name
        self.input = data


class _Resp:
    def __init__(self, content):
        self.content = content


class FakeClient:
    def __init__(self, is_buyer=True, confidence=9, reason="asks to build a POS"):
        self.payload = {"is_buyer": is_buyer, "confidence": confidence, "reason": reason}
        self.calls = 0
        self.messages = self

    def create(self, **kwargs):
        self.calls += 1
        return _Resp([_ToolUse(TOOL_NAME, self.payload)])


KEYWORDS = [km.Keyword(term="aplikasi kasir", language="id", match_type=km.MatchType.PHRASE, id=10)]


def _db() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = Session(engine)
    s.add(Tenant(id=1, name="Acme", slug="acme"))
    s.flush()
    return s


def _settings(s, *, telegram=None, email=None):
    if telegram is not None:
        s.add(NotificationSetting(
            tenant_id=1, channel=NotificationChannel.TELEGRAM, is_enabled=True,
            min_score=telegram, config={"target": "chat-1"},
        ))
    if email is not None:
        s.add(NotificationSetting(
            tenant_id=1, channel=NotificationChannel.EMAIL, is_enabled=True,
            min_score=email, config={"target": "me@x.com"},
        ))
    s.flush()


def _raw(body="Cari developer, butuh aplikasi kasir untuk toko.", external_id="p1") -> RawPost:
    return RawPost(platform="facebook", external_id=external_id, url="http://x/p1", body=body)


def _run(s, raw, client, enqueue, **kw):
    return process_post(s, 1, raw, keywords=KEYWORDS, client=client, enqueue=enqueue, **kw)


class TestProcessPost:
    def test_duplicate_is_short_circuited(self):
        s = _db()
        _settings(s, telegram=7)
        client = FakeClient()
        enq: list[int] = []
        _run(s, _raw(), client, enq.append)
        calls_after_first = client.calls
        result = _run(s, _raw(), client, enq.append)  # same post again
        assert result.status is PipelineStatus.DEDUPED
        assert client.calls == calls_after_first  # no re-scoring
        assert s.scalar(select(Match).where(Match.post_id.isnot(None)).limit(1)) is not None
        assert len(s.execute(select(Match)).scalars().all()) == 1
        assert len(enq) == 1  # enqueued once, not twice

    def test_no_keyword_match_stores_post_only(self):
        s = _db()
        _settings(s, telegram=7)
        client = FakeClient()
        enq: list[int] = []
        result = _run(s, _raw(body="just a cat photo, nothing relevant"), client, enq.append)
        assert result.status is PipelineStatus.NO_MATCH
        assert len(s.execute(select(Post)).scalars().all()) == 1  # deduped/stored
        assert s.execute(select(Match)).scalars().all() == []  # no match
        assert client.calls == 0  # never scored (no tokens spent)
        assert enq == []

    def test_buyer_above_threshold_notifies(self):
        s = _db()
        _settings(s, telegram=7)
        client = FakeClient(is_buyer=True, confidence=9)
        enq: list[int] = []
        result = _run(s, _raw(), client, enq.append)
        assert result.status is PipelineStatus.NOTIFIED
        match = s.execute(select(Match)).scalars().one()
        assert match.ai_score == 9
        assert match.ai_is_buyer is True
        assert match.status is MatchStatus.NOTIFIED
        notif = s.execute(select(Notification)).scalars().one()
        assert notif.channel is NotificationChannel.TELEGRAM
        assert notif.target == "chat-1"
        assert enq == [notif.id]

    def test_below_threshold_scored_not_notified(self):
        s = _db()
        _settings(s, telegram=9)
        client = FakeClient(is_buyer=True, confidence=7)
        enq: list[int] = []
        result = _run(s, _raw(), client, enq.append)
        assert result.status is PipelineStatus.SCORED
        match = s.execute(select(Match)).scalars().one()
        assert match.ai_score == 7
        assert match.status is MatchStatus.PENDING
        assert s.execute(select(Notification)).scalars().all() == []
        assert enq == []

    def test_non_buyer_not_notified(self):
        s = _db()
        _settings(s, telegram=7)
        client = FakeClient(is_buyer=False, confidence=10)
        enq: list[int] = []
        result = _run(s, _raw(), client, enq.append)
        assert result.status is PipelineStatus.SCORED
        assert s.execute(select(Notification)).scalars().all() == []
        assert enq == []

    def test_per_channel_thresholds(self):
        s = _db()
        _settings(s, telegram=7, email=9)  # score 8 clears telegram, not email
        client = FakeClient(is_buyer=True, confidence=8)
        enq: list[int] = []
        result = _run(s, _raw(), client, enq.append)
        assert result.status is PipelineStatus.NOTIFIED
        assert result.notified_channels == ("telegram",)
        notifs = s.execute(select(Notification)).scalars().all()
        assert len(notifs) == 1
        assert notifs[0].channel is NotificationChannel.TELEGRAM
        assert len(enq) == 1

    def test_match_records_terms_and_keyword_id(self):
        s = _db()
        _settings(s, telegram=7)
        client = FakeClient()
        _run(s, _raw(), client, [].append)
        match = s.execute(select(Match)).scalars().one()
        assert match.keyword_id == 10
        assert "aplikasi kasir" in match.matched_terms
        assert match.matched_term

    def test_reprocess_does_not_duplicate_notifications(self):
        s = _db()
        _settings(s, telegram=7)
        client = FakeClient()
        enq: list[int] = []
        _run(s, _raw(), client, enq.append)
        _run(s, _raw(), client, enq.append)  # second pass: deduped
        assert len(s.execute(select(Notification)).scalars().all()) == 1
        assert len(enq) == 1