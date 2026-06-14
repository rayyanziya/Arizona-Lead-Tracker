"""Security primitives: password hashing and at-rest secret encryption.

Password hashing uses PBKDF2-HMAC-SHA256 from the stdlib (zero extra deps, so
it is unit-testable without a build toolchain). Secret encryption uses Fernet
from ``cryptography`` and is imported lazily so modules that never touch
encryption stay importable even when the key is unset.
"""

import base64
import hashlib
import hmac
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from functools import lru_cache

import jwt

from app.core.config import settings

_PBKDF2_ALGO = "pbkdf2_sha256"
_PBKDF2_ROUNDS = 480_000
_SALT_BYTES = 16


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(_SALT_BYTES)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ROUNDS)
    return (
        f"{_PBKDF2_ALGO}${_PBKDF2_ROUNDS}$"
        f"{base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algo, rounds_s, salt_b64, hash_b64 = encoded.split("$")
        if algo != _PBKDF2_ALGO:
            return False
        rounds = int(rounds_s)
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
    except (ValueError, TypeError):
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, rounds)
    return hmac.compare_digest(dk, expected)


@lru_cache
def _fernet():  # pragma: no cover - thin wrapper around cryptography
    from cryptography.fernet import Fernet

    key = settings.app_encryption_key
    if not key:
        raise RuntimeError("APP_ENCRYPTION_KEY is not set; cannot encrypt/decrypt secrets")
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_secret(plaintext: str) -> str:
    """Encrypt a sensitive string (e.g. a platform session blob) for DB storage."""
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str) -> str:
    return _fernet().decrypt(ciphertext.encode()).decode()


# --- JWT access tokens (dashboard auth) -------------------------------------
# HS256 over the app secret: single backend, no key distribution, so a symmetric
# secret is the right trade-off. Claims carry the tenant so every request is
# scoped without a second DB lookup.
_JWT_ALGORITHM = "HS256"
DEFAULT_ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 12


class TokenError(Exception):
    """Raised when an access token is missing, malformed, expired, or forged."""


def _utcnow() -> datetime:
    return datetime.now(UTC)


def create_access_token(
    *,
    user_id: int,
    tenant_id: int,
    role: str,
    expires_minutes: int = DEFAULT_ACCESS_TOKEN_EXPIRE_MINUTES,
    now: Callable[[], datetime] = _utcnow,
) -> str:
    """Mint a signed access token. ``now`` is injected so expiry is testable."""
    issued = now()
    payload = {
        "sub": str(user_id),
        "tenant_id": tenant_id,
        "role": role,
        "iat": issued,
        "exp": issued + timedelta(minutes=expires_minutes),
    }
    return jwt.encode(payload, settings.app_secret_key, algorithm=_JWT_ALGORITHM)


def decode_access_token(token: str) -> dict:
    """Verify signature + expiry and return the claims, or raise TokenError."""
    try:
        return jwt.decode(token, settings.app_secret_key, algorithms=[_JWT_ALGORITHM])
    except jwt.PyJWTError as exc:
        raise TokenError(str(exc)) from exc
