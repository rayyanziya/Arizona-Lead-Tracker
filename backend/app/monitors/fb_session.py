"""Encrypted persistence for a captured browser session (Playwright storage_state).

A logged-in Facebook session is sensitive -- anyone with the storage_state JSON
can act as the account -- so it is encrypted at rest. capture_fb_session.py writes
it through :func:`save_session` and the Playwright driver reads it back with
:func:`load_session`.

The encrypt/decrypt functions are injected (defaulting to the Fernet helpers in
:mod:`app.core.security`) so the file-handling and round-trip contract unit-tests
without the cryptography dependency or a configured key.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from app.core.security import decrypt_secret, encrypt_secret


def session_path(session_dir: str | Path, account: str = "facebook") -> Path:
    """Canonical on-disk location of an encrypted session for ``account``.

    Both the capture script and the Playwright driver derive the path here, so
    they can never disagree on where a session lives. Defaults to the Facebook
    account because that is the first (and, in the MVP, only) browser collector.
    """
    return Path(session_dir) / f"{account}.session"


def save_session(
    state_json: str,
    path: str | Path,
    *,
    encrypt: Callable[[str], str] = encrypt_secret,
) -> Path:
    """Encrypt a storage_state JSON string and write it to ``path``.

    Parent directories are created as needed. Returns the written path.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(encrypt(state_json), encoding="utf-8")
    return target


def load_session(
    path: str | Path,
    *,
    decrypt: Callable[[str], str] = decrypt_secret,
) -> str:
    """Read and decrypt a stored storage_state JSON string.

    Raises :class:`FileNotFoundError` with a clear message when no session has
    been captured yet.
    """
    source = Path(path)
    if not source.exists():
        raise FileNotFoundError(f"no captured session at {source}; run capture_fb_session first")
    return decrypt(source.read_text(encoding="utf-8"))