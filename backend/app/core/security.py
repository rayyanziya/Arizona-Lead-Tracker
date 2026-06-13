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
from functools import lru_cache

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
