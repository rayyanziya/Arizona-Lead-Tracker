"""Unit tests for the JWT access-token primitives in app.core.security.

Pure crypto/encoding -- no DB, no network. ``now`` is injected so token expiry is
exercised deterministically (a token minted in the past is decoded against the
real clock and must be rejected).
"""

from __future__ import annotations

from datetime import UTC, datetime

import jwt
import pytest

from app.core.security import (
    TokenError,
    create_access_token,
    decode_access_token,
)


def test_round_trip_preserves_identity_claims():
    token = create_access_token(user_id=7, tenant_id=3, role="owner")
    claims = decode_access_token(token)
    assert claims["sub"] == "7"
    assert claims["tenant_id"] == 3
    assert claims["role"] == "owner"


def test_includes_iat_and_exp():
    claims = decode_access_token(create_access_token(user_id=1, tenant_id=1, role="member"))
    assert "iat" in claims
    assert "exp" in claims
    assert claims["exp"] > claims["iat"]


def test_expires_minutes_sets_the_window():
    # exp - iat equals the window regardless of the clock, so use the real now
    # (a future-dated iat would trip pyjwt's not-yet-valid check).
    token = create_access_token(user_id=1, tenant_id=1, role="member", expires_minutes=15)
    claims = decode_access_token(token)
    assert claims["exp"] - claims["iat"] == 15 * 60


def test_expired_token_rejected():
    past = datetime(2020, 1, 1, tzinfo=UTC)
    token = create_access_token(
        user_id=1, tenant_id=1, role="member", expires_minutes=30, now=lambda: past
    )
    with pytest.raises(TokenError):
        decode_access_token(token)


def test_tampered_token_rejected():
    token = create_access_token(user_id=1, tenant_id=1, role="member")
    with pytest.raises(TokenError):
        decode_access_token(token + "tamper")


def test_garbage_string_rejected():
    with pytest.raises(TokenError):
        decode_access_token("not.a.jwt")


def test_wrong_signing_key_rejected():
    payload = {
        "sub": "1",
        "tenant_id": 1,
        "role": "member",
        "iat": datetime(2020, 1, 1, tzinfo=UTC),
        "exp": datetime(2099, 1, 1, tzinfo=UTC),
    }
    # >=32 bytes to avoid pyjwt's short-key warning; the point is the key differs.
    forged = jwt.encode(payload, "a-totally-different-secret-key-32bytes!!", algorithm="HS256")
    with pytest.raises(TokenError):
        decode_access_token(forged)