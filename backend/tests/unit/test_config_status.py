"""Pure tests for the config-status mapping.

The dashboard needs to tell an operator whether the system can actually collect
and score -- "I added a source, why no leads?" is almost always a missing
Anthropic key or missing Reddit credentials. config_status() turns Settings (plus
a filesystem check the caller does) into plain booleans, so it is pure and tested
here with a fake settings object -- no env, no real Settings, no filesystem.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.config_status import config_status

pytestmark = pytest.mark.unit


def _settings(**overrides):
    base = {
        "anthropic_api_key": "",
        "reddit_client_id": "",
        "reddit_client_secret": "",
        "telegram_bot_token": "",
        "smtp_host": "mailpit",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _status(**overrides):
    return config_status(_settings(**overrides), facebook_session_present=False)


class TestScoring:
    def test_scoring_configured_when_anthropic_key_present(self):
        assert _status(anthropic_api_key="sk-xxx").scoring_configured

    def test_scoring_not_configured_when_blank(self):
        assert not _status(anthropic_api_key="  ").scoring_configured


class TestReddit:
    def test_reddit_configured_needs_both_id_and_secret(self):
        assert _status(reddit_client_id="id", reddit_client_secret="secret").reddit_configured

    def test_reddit_not_configured_with_only_id(self):
        assert not _status(reddit_client_id="id", reddit_client_secret="").reddit_configured


class TestFacebookAndChannels:
    def test_facebook_session_present_is_passed_through(self):
        s = _settings()
        assert config_status(s, facebook_session_present=True).facebook_session_present
        assert not config_status(s, facebook_session_present=False).facebook_session_present

    def test_telegram_configured_when_token_present(self):
        assert _status(telegram_bot_token="t").telegram_configured

    def test_email_configured_tracks_smtp_host(self):
        assert _status(smtp_host="mailpit").email_configured
        assert not _status(smtp_host="").email_configured