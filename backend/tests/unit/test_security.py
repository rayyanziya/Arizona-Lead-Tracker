"""Unit tests for password hashing/verification (stdlib PBKDF2-HMAC-SHA256)."""

import pytest

from app.core.security import hash_password, verify_password

pytestmark = pytest.mark.unit


def test_hash_then_verify_roundtrip():
    encoded = hash_password("s3cret-pass")
    assert verify_password("s3cret-pass", encoded)


def test_verify_rejects_wrong_password():
    encoded = hash_password("correct horse")
    assert not verify_password("battery staple", encoded)


def test_hash_is_salted_and_nondeterministic():
    # Same input, different salt -> different stored hash each time.
    assert hash_password("same") != hash_password("same")


def test_verify_rejects_malformed_hash():
    assert not verify_password("x", "not-a-valid-hash")
    assert not verify_password("x", "")
