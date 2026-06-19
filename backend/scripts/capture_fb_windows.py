"""Standalone Windows-host capture of a logged-in Facebook browser session.

WHY THIS EXISTS (vs. ``capture_fb_session.py``):
    The Docker-based ``make capture-fb`` opens a *headful* Chromium inside the Linux
    ``worker-browser`` container, which has no display on Windows. This script runs the
    same assisted login directly on the Windows host -- where there IS a display -- and
    writes the encrypted session to ``backend/.sessions/facebook.session``. That path is
    bind-mounted into the container at ``/app/.sessions`` (see docker-compose.yml), so the
    browser worker reads it back with no changes.

DEPENDENCY-LIGHT BY DESIGN:
    It depends ONLY on ``playwright`` + ``cryptography`` and does NOT import the ``app``
    package (no pydantic-settings / asyncpg / pyjwt), so it installs cleanly on a stock
    host Python without the full backend toolchain. The encryption it produces is the
    exact Fernet format that ``app.monitors.fb_session.save_session`` writes and the worker
    reads: ``Fernet(APP_ENCRYPTION_KEY).encrypt(storage_state_json).decode()`` as UTF-8
    text at ``<session_dir>/facebook.session``. Keep those two facts in sync if the at-rest
    format ever changes.

USAGE (normally via the ``capture-fb.ps1`` wrapper, which sets up the venv for you):
    py -3.13 backend/scripts/capture_fb_windows.py
    py -3.13 backend/scripts/capture_fb_windows.py --account facebook --timeout 300

The key is read from the repo-root ``.env`` (the same file docker-compose loads), so the
host capture and the container decrypt agree automatically.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

LOGIN_URL = "https://www.facebook.com/login"
LOGGED_IN_COOKIE = "c_user"  # set by Facebook only once fully authenticated
ENV_KEY = "APP_ENCRYPTION_KEY"


def find_repo_root(start: Path) -> Path:
    """Walk up from ``start`` to the directory holding both ``.env`` and ``backend/``."""
    for candidate in (start, *start.parents):
        if (candidate / ".env").is_file() and (candidate / "backend").is_dir():
            return candidate
    # Fallback: this file is backend/scripts/capture_fb_windows.py -> root is parents[2].
    return start.parents[2]


def read_env_value(env_path: Path, name: str) -> str:
    """Minimal ``.env`` reader for a single KEY=VALUE (no extra deps).

    Handles surrounding quotes and ignores comments / blank lines. We only need one
    value, so a full dotenv parser would be overkill.
    """
    if not env_path.is_file():
        raise SystemExit(f"No .env at {env_path}; cannot read {name}.")
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip() == name:
            return value.strip().strip('"').strip("'")
    raise SystemExit(f"{name} not found in {env_path}.")


def _is_logged_in(context) -> bool:
    return any(c.get("name") == LOGGED_IN_COOKIE and c.get("value") for c in context.cookies())


def _wait_for_login(context, *, timeout_s: float, poll_s: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if _is_logged_in(context):
            return True
        time.sleep(poll_s)
    return _is_logged_in(context)


def capture(*, session_dir: Path, encryption_key: str, account: str, timeout_s: float) -> Path:
    """Drive an assisted headful login and write the encrypted session. Returns its path."""
    try:
        from cryptography.fernet import Fernet
    except ImportError:  # pragma: no cover - guidance, not logic
        raise SystemExit("Missing 'cryptography'. Install with: pip install cryptography")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:  # pragma: no cover - guidance, not logic
        raise SystemExit(
            "Missing 'playwright'. Install with: pip install playwright "
            "&& python -m playwright install chromium"
        )

    try:
        fernet = Fernet(encryption_key.encode())
    except Exception as exc:  # noqa: BLE001 - surface a clear, actionable message
        raise SystemExit(
            f"{ENV_KEY} is not a valid Fernet key (need 32 url-safe base64 bytes): {exc}"
        )

    session_dir.mkdir(parents=True, exist_ok=True)
    target = session_dir / f"{account}.session"

    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(headless=False)
        except Exception as exc:  # noqa: BLE001 - most common first-run failure
            raise SystemExit(
                f"Could not launch Chromium ({exc}).\n"
                "Run once: python -m playwright install chromium"
            )
        context = browser.new_context()
        page = context.new_page()
        page.goto(LOGIN_URL)
        print(f"\nBrowser open. Log in as the scraping account for '{account}'.")
        print("Complete any 2FA / checkpoint, then leave it on a logged-in page.")
        print(f"Waiting up to {int(timeout_s)}s for login (polling the '{LOGGED_IN_COOKIE}' cookie) ...")

        if _wait_for_login(context, timeout_s=timeout_s):
            print(f"Detected login ('{LOGGED_IN_COOKIE}' cookie present).")
        else:
            print(f"No '{LOGGED_IN_COOKIE}' cookie after {int(timeout_s)}s -- login not confirmed.")
            if input("Save the current session anyway? [y/N] ").strip().lower() != "y":
                browser.close()
                raise SystemExit("Aborted: no session written.")

        state_json = json.dumps(context.storage_state())
        target.write_text(fernet.encrypt(state_json.encode()).decode(), encoding="utf-8")
        browser.close()

    print(f"\nSaved encrypted session -> {target}")
    print("The browser worker will read this via the ./backend:/app bind mount. You're set.")
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Capture an encrypted Facebook session on Windows.")
    parser.add_argument("--account", default="facebook", help="session name (default: facebook)")
    parser.add_argument("--timeout", type=float, default=300.0, help="login wait seconds")
    parser.add_argument(
        "--session-dir",
        default=None,
        help="override output dir (default: <repo>/backend/.sessions)",
    )
    parser.add_argument(
        "--env-file",
        default=None,
        help=f"override .env path to read {ENV_KEY} from (default: <repo>/.env)",
    )
    args = parser.parse_args(argv)

    repo_root = find_repo_root(Path(__file__).resolve())
    env_path = Path(args.env_file) if args.env_file else repo_root / ".env"
    session_dir = (
        Path(args.session_dir) if args.session_dir else repo_root / "backend" / ".sessions"
    )

    key = read_env_value(env_path, ENV_KEY)
    if not key or key == "change-me-32-url-safe-base64-bytes":
        raise SystemExit(
            f"{ENV_KEY} in {env_path} is unset or still the placeholder. "
            "Set a real Fernet key (matching the one the worker uses) first."
        )

    capture(
        session_dir=session_dir,
        encryption_key=key,
        account=args.account,
        timeout_s=args.timeout,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
