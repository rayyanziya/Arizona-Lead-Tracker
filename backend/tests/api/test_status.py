"""API tests for the config-status and scoring-test diagnostics endpoints."""

from __future__ import annotations

from types import SimpleNamespace

from app.api.status import get_anthropic_client
from app.main import app
from app.services.scoring import TOOL_NAME

_KEYS = {
    "scoring_configured",
    "reddit_configured",
    "x_configured",
    "facebook_session_present",
    "telegram_configured",
    "email_configured",
}


async def test_status_requires_auth(client):
    assert (await client.get("/status")).status_code == 401


async def test_status_returns_all_capability_booleans(auth):
    resp = await auth.client.get("/status", headers=auth.headers)
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == _KEYS
    assert all(isinstance(v, bool) for v in body.values())


def _fake_anthropic(*, is_buyer=True, confidence=9, reason="Explicit buyer intent."):
    """A client matching AnthropicLike whose create() returns one forced tool_use."""
    block = SimpleNamespace(
        type="tool_use",
        name=TOOL_NAME,
        input={"is_buyer": is_buyer, "confidence": confidence, "reason": reason},
    )
    response = SimpleNamespace(content=[block])
    return SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: response))


async def test_test_score_requires_auth(client):
    resp = await client.post("/status/test-score", json={"body": "anything"})
    assert resp.status_code == 401


async def test_test_score_returns_the_models_assessment(auth):
    app.dependency_overrides[get_anthropic_client] = lambda: _fake_anthropic(
        is_buyer=True, confidence=9, reason="Asking for a developer to build a POS."
    )
    try:
        resp = await auth.client.post(
            "/status/test-score",
            headers=auth.headers,
            json={"title": "Need an app", "body": "Looking for a developer to build our POS."},
        )
    finally:
        app.dependency_overrides.pop(get_anthropic_client, None)
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_buyer"] is True
    assert body["confidence"] == 9
    assert body["reason"] == "Asking for a developer to build a POS."
    assert body["model"]  # echoes which model scored it


async def test_test_score_503_when_scoring_unconfigured(auth, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "anthropic_api_key", "")
    resp = await auth.client.post(
        "/status/test-score", headers=auth.headers, json={"body": "Need a CRM built for us."}
    )
    assert resp.status_code == 503


async def test_test_score_rejects_blank_body(auth):
    resp = await auth.client.post("/status/test-score", headers=auth.headers, json={"body": "  "})
    assert resp.status_code == 422