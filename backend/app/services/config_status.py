"""Derive a plain-boolean view of what the system is configured to do.

"I added a source but no leads appear" is almost always a missing Anthropic key
(nothing gets scored) or missing Reddit credentials (nothing gets collected).
This turns Settings -- plus a filesystem check the caller performs for the
Facebook session -- into booleans the dashboard can render, so an operator can
see at a glance whether collection and scoring will actually happen. Pure: no
env access, no filesystem, no network.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ConfigStatus:
    scoring_configured: bool  # Anthropic key set -> scraped posts get scored
    reddit_configured: bool  # Reddit API id + secret set -> Reddit can collect
    facebook_session_present: bool  # captured FB login session exists on disk
    telegram_configured: bool  # Telegram bot token set -> Telegram alerts
    email_configured: bool  # SMTP host set -> email alerts


def _has(value: Any) -> bool:
    return bool(value and str(value).strip())


def config_status(settings: Any, *, facebook_session_present: bool) -> ConfigStatus:
    """Map settings (+ the caller's FB-session check) to capability booleans."""
    return ConfigStatus(
        scoring_configured=_has(settings.anthropic_api_key),
        reddit_configured=_has(settings.reddit_client_id) and _has(settings.reddit_client_secret),
        facebook_session_present=facebook_session_present,
        telegram_configured=_has(settings.telegram_bot_token),
        email_configured=_has(settings.smtp_host),
    )