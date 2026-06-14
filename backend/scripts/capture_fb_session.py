"""Assisted one-time capture of a logged-in Facebook browser session.

Opens a *headful* Chromium, lets you log in by hand -- including 2FA and any
checkpoint screen -- then serializes Playwright's ``storage_state`` and writes it
ENCRYPTED (Fernet, via :func:`app.monitors.fb_session.save_session`) to the
canonical path under ``settings.browser_session_dir``. The browser worker later
decrypts it with ``load_session`` and scrapes without re-authenticating.

Playwright and an interactive display are required, so this is NOT unit-tested and
never runs on the local venv. Run it from the browser worker image:

    make capture-fb
    # docker compose run --rm -it worker-browser python -m scripts.capture_fb_session

Login is detected by polling for Facebook's ``c_user`` cookie, which is only set
once you are fully authenticated -- so detection never depends on a brittle page
selector. If the cookie is not seen before --timeout seconds, you are asked to
confirm before anything is written.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

LOGIN_URL = "https://www.facebook.com/login"
LOGGED_IN_COOKIE = "c_user"


def _is_logged_in(context) -> bool:
    """True once Facebook has set the post-auth ``c_user`` cookie."""
    return any(c.get("name") == LOGGED_IN_COOKIE and c.get("value") for c in context.cookies())


def _wait_for_login(
    context,
    *,
    timeout_s: float,
    poll_s: float = 2.0,
    sleep=time.sleep,
    now=time.monotonic,
) -> bool:
    """Poll until logged in or the deadline passes; return whether login was seen."""
    deadline = now() + timeout_s
    while now() < deadline:
        if _is_logged_in(context):
            return True
        sleep(poll_s)
    return _is_logged_in(context)


def capture(
    *,
    session_dir: str | Path,
    account: str = "facebook",
    headless: bool = False,
    timeout_s: float = 300.0,
    log=print,
    confirm=input,
) -> Path:
    """Drive an assisted login and persist the encrypted session. Returns the path."""
    from playwright.sync_api import sync_playwright

    from app.monitors.fb_session import save_session, session_path

    target = session_path(session_dir, account)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        context = browser.new_context()
        page = context.new_page()
        page.goto(LOGIN_URL)
        log(f"Browser open. Log in as the scraping account for '{account}'.")
        log("Complete any 2FA / checkpoint, then leave the browser on a logged-in page.")
        log(f"Waiting up to {int(timeout_s)}s for login ...")

        if _wait_for_login(context, timeout_s=timeout_s):
            log(f"Detected login ('{LOGGED_IN_COOKIE}' cookie present).")
        else:
            log(f"No '{LOGGED_IN_COOKIE}' cookie after {int(timeout_s)}s -- login not confirmed.")
            if confirm("Save the current session anyway? [y/N] ").strip().lower() != "y":
                browser.close()
                raise SystemExit("Aborted: no session written.")

        state_json = json.dumps(context.storage_state())
        out = save_session(state_json, target)
        browser.close()

    log(f"Saved encrypted session -> {out}")
    return out


def main(argv: list[str] | None = None) -> int:
    from app.core.config import settings

    parser = argparse.ArgumentParser(description="Capture an encrypted Facebook session.")
    parser.add_argument("--account", default="facebook", help="session name (default: facebook)")
    parser.add_argument("--session-dir", default=settings.browser_session_dir)
    parser.add_argument("--timeout", type=float, default=300.0, help="login wait seconds")
    parser.add_argument(
        "--headless",
        action="store_true",
        help="run without a window (debugging only; assisted login needs a display)",
    )
    args = parser.parse_args(argv)
    capture(
        session_dir=args.session_dir,
        account=args.account,
        headless=args.headless,
        timeout_s=args.timeout,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())