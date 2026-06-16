"""Tests for the database seed: a pure, inspectable plan + an idempotent writer.

The plan tests need no DB; the writer tests run against in-memory SQLite with a
fake hasher injected, so neither Postgres nor the security/config stack is
imported here.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import (
    Base,
    Keyword,
    MonitoredSource,
    NotificationSetting,
    Tenant,
    User,
)
from scripts.seed import build_seed_plan, seed

pytestmark = pytest.mark.unit


def _fake_hash(password: str) -> str:
    return "hashed:" + password


def _fresh_session() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return Session(engine)


class TestSeedPlan:
    def test_has_both_indonesian_and_english_keywords(self):
        langs = {k.language for k in build_seed_plan().keywords}
        assert "id" in langs
        assert "en" in langs

    def test_threshold_is_seven_on_every_channel(self):
        notifications = build_seed_plan().notifications
        assert notifications
        assert all(n.min_score == 7 for n in notifications)

    def test_has_telegram_and_email_channels(self):
        channels = {n.channel for n in build_seed_plan().notifications}
        assert channels == {"telegram", "email"}

    def test_has_a_facebook_source(self):
        assert any(s.platform == "facebook" for s in build_seed_plan().sources)

    def test_has_a_reddit_source(self):
        assert any(s.platform == "reddit" for s in build_seed_plan().sources)

    def test_has_an_x_source(self):
        assert any(s.platform == "x" for s in build_seed_plan().sources)

    def test_x_source_identifier_yields_a_query(self):
        # The X placeholder must be a shape the collector accepts, so an operator
        # who edits it to a real handle gets a working source. build_query returns
        # None for input it can't turn into a recent-search query.
        from app.monitors.x_client import build_query

        x_sources = [s for s in build_seed_plan().sources if s.platform == "x"]
        assert x_sources
        assert all(build_query(s.identifier) is not None for s in x_sources)

    def test_user_email_is_accepted_by_the_login_validator(self):
        # Regression: the login route validates email with Pydantic EmailStr,
        # which rejects special-use TLDs like ".local". A seed user whose email
        # the login endpoint refuses can never sign in, so pin the seed email to
        # the very validator the API uses.
        from app.schemas.auth import LoginRequest

        plan = build_seed_plan()
        LoginRequest(email=plan.user_email, password=plan.user_password)


class TestSeedWriter:
    def test_seed_creates_expected_rows(self):
        plan = build_seed_plan()
        session = _fresh_session()
        created = seed(session, hasher=_fake_hash)
        session.commit()

        assert created["tenants"] == 1
        assert session.query(Tenant).count() == 1
        assert session.query(User).count() == 1
        assert session.query(Keyword).count() == len(plan.keywords)
        assert session.query(MonitoredSource).count() == len(plan.sources)
        assert session.query(NotificationSetting).count() == 2

    def test_user_password_is_hashed_not_plaintext(self):
        plan = build_seed_plan()
        session = _fresh_session()
        seed(session, hasher=_fake_hash)
        session.commit()
        user = session.query(User).one()
        assert user.hashed_password == "hashed:" + plan.user_password
        assert user.hashed_password != plan.user_password

    def test_seed_is_idempotent(self):
        plan = build_seed_plan()
        session = _fresh_session()
        seed(session, hasher=_fake_hash)
        session.commit()
        created_again = seed(session, hasher=_fake_hash)
        session.commit()

        assert all(count == 0 for count in created_again.values())
        assert session.query(Tenant).count() == 1
        assert session.query(Keyword).count() == len(plan.keywords)
        assert session.query(NotificationSetting).count() == 2
