"""Unit tests for encrypted browser-session persistence.

encrypt/decrypt are injected with reversible (base64) fakes so the file-handling
and round-trip contract is verified without the (locally absent) cryptography
dependency or a configured Fernet key.
"""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from app.monitors.fb_session import load_session, save_session, session_path


def _enc(plaintext: str) -> str:
    return "ENC::" + base64.b64encode(plaintext.encode()).decode()


def _dec(ciphertext: str) -> str:
    assert ciphertext.startswith("ENC::")
    return base64.b64decode(ciphertext[len("ENC::") :]).decode()


class TestSaveSession:
    def test_writes_ciphertext_not_plaintext(self, tmp_path):
        path = tmp_path / "sessions" / "fb.bin"
        out = save_session('{"cookies": []}', path, encrypt=_enc)
        assert out == path
        on_disk = path.read_text(encoding="utf-8")
        assert "cookies" not in on_disk  # stored encrypted, not as raw json
        assert _dec(on_disk) == '{"cookies": []}'  # but still recoverable

    def test_creates_parent_directories(self, tmp_path):
        path = tmp_path / "a" / "b" / "fb.bin"
        save_session("state", path, encrypt=_enc)
        assert path.exists()


class TestLoadSession:
    def test_round_trips_through_save(self, tmp_path):
        path = tmp_path / "fb.bin"
        save_session('{"cookies": [1]}', path, encrypt=_enc)
        assert load_session(path, decrypt=_dec) == '{"cookies": [1]}'

    def test_accepts_str_paths(self, tmp_path):
        path = tmp_path / "fb.bin"
        save_session("data", str(path), encrypt=_enc)
        assert load_session(str(path), decrypt=_dec) == "data"

    def test_missing_session_raises_clear_error(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="capture_fb_session"):
            load_session(tmp_path / "nope.bin", decrypt=_dec)


class TestSessionPath:
    def test_defaults_to_facebook_account_under_dir(self):
        assert session_path("/app/.sessions") == Path("/app/.sessions/facebook.session")

    def test_account_is_used_in_filename(self):
        assert session_path("/app/.sessions", account="x") == Path("/app/.sessions/x.session")

    def test_accepts_path_dir_and_keeps_it_as_parent(self, tmp_path):
        out = session_path(tmp_path)
        assert out.parent == tmp_path
        assert out.name == "facebook.session"