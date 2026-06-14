"""Playwright FeedDriver for Facebook groups -- the side-effecting half.

Implements the ``FeedDriver`` protocol that :class:`app.monitors.facebook.FacebookMonitor`
drives: launch a logged-in Chromium from the encrypted ``storage_state``, open the
group feed, scroll it, read post nodes into :class:`ScrapedPost` dicts, and detect a
login / checkpoint wall. All pacing, dedup, and the per-run cap live in
``FacebookMonitor``; this module is deliberately NOT imported by
``app.monitors.__init__`` so the unit suite stays Playwright-free.

SELECTORS ARE BEST-EFFORT. Facebook's markup is obfuscated and shifts often, so the
DOM extraction (``_EXTRACT_JS``) and the block-detection selectors are isolated here
and should be verified against the live feed after the first capture+run. Everything
around them -- session loading, browser lifecycle, id parsing -- is stable.
"""

from __future__ import annotations

import json
import re

from app.monitors.facebook_parser import ScrapedPost
from app.monitors.fb_session import load_session, session_path

# Post id sits in the permalink: /groups/<gid>/posts/<id>/ or /permalink/<id>/.
_POST_ID_RE = re.compile(r"/(?:posts|permalink)/(\d+)")

# Run in the page to pull the raw fields the parser needs from each feed article.
# Returns plain objects; external_id is derived from `permalink` in Python.
# BEST-EFFORT -- verify selectors against the live DOM.
_EXTRACT_JS = r"""
() => {
  const out = [];
  const articles = document.querySelectorAll('div[role="feed"] div[role="article"]');
  for (const art of articles) {
    let permalink = null;
    const links = art.querySelectorAll('a[href*="/posts/"], a[href*="/permalink/"]');
    for (const a of links) {
      const href = a.getAttribute('href') || '';
      if (/\/(posts|permalink)\/\d+/.test(href)) { permalink = a.href; break; }
    }
    let author = art.getAttribute('aria-label');
    if (!author) {
      const al = art.querySelector('h3 a, h4 a, strong a');
      if (al) author = al.textContent;
    }
    let text = null;
    const msg = art.querySelector('div[data-ad-preview="message"], div[dir="auto"]');
    if (msg) text = msg.innerText;
    if (!text) text = art.innerText;
    let timestamp = null;
    const t = art.querySelector('abbr[data-utime]');
    if (t && t.getAttribute('data-utime')) timestamp = Number(t.getAttribute('data-utime'));
    out.push({ permalink, author, text, timestamp });
  }
  return out;
}
"""

_LOGIN_HINTS = ("login", "checkpoint", "/login/")


class PlaywrightFeedDriver:
    """Drive a logged-in Chromium over one Facebook group feed.

    The browser is started lazily on first use (so constructing the driver is
    cheap and side-effect-free) and torn down in :meth:`close`. ``load`` is
    injected for testability; it defaults to the real encrypted-session reader.
    """

    def __init__(
        self,
        group_url: str,
        *,
        session_dir: str,
        account: str = "facebook",
        headless: bool = True,
        locale: str | None = None,
        timezone: str | None = None,
        proxy: dict | None = None,
        load=load_session,
    ) -> None:
        self._group_url = group_url
        self._session_dir = session_dir
        self._account = account
        self._headless = headless
        self._locale = locale
        self._timezone = timezone
        self._proxy = proxy
        self._load = load
        self._started = False
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None

    def _ensure_started(self) -> None:
        if self._started:
            return
        from playwright.sync_api import sync_playwright

        state_json = self._load(session_path(self._session_dir, self._account))
        self._pw = sync_playwright().start()
        launch_kwargs: dict = {"headless": self._headless}
        if self._proxy:
            launch_kwargs["proxy"] = self._proxy
        self._browser = self._pw.chromium.launch(**launch_kwargs)
        ctx_kwargs: dict = {"storage_state": json.loads(state_json)}
        if self._locale:
            ctx_kwargs["locale"] = self._locale
        if self._timezone:
            ctx_kwargs["timezone_id"] = self._timezone
        self._context = self._browser.new_context(**ctx_kwargs)
        self._page = self._context.new_page()
        self._page.goto(self._group_url, wait_until="domcontentloaded")
        self._started = True

    def read_posts(self) -> list[ScrapedPost]:
        self._ensure_started()
        posts: list[ScrapedPost] = []
        for item in self._page.evaluate(_EXTRACT_JS):
            permalink = item.get("permalink")
            match = _POST_ID_RE.search(permalink) if permalink else None
            if match is None:
                continue  # no stable id -> let the parser-less driver skip it
            posts.append(
                ScrapedPost(
                    external_id=match.group(1),
                    permalink=permalink,
                    text=item.get("text"),
                    author=item.get("author"),
                    timestamp=item.get("timestamp"),
                )
            )
        return posts

    def scroll(self) -> None:
        self._ensure_started()
        self._page.mouse.wheel(0, 3000)
        self._page.wait_for_timeout(800)  # let lazy-loaded posts attach

    def is_blocked(self) -> bool:
        self._ensure_started()
        url = (self._page.url or "").lower()
        if any(hint in url for hint in _LOGIN_HINTS):
            return True
        has_email = self._page.query_selector('input[name="email"]') is not None
        has_pass = self._page.query_selector('input[name="pass"]') is not None
        return has_email and has_pass

    def close(self) -> None:
        for resource in (self._context, self._browser):
            if resource is not None:
                try:
                    resource.close()
                except Exception:  # noqa: S110, BLE001 - best-effort teardown
                    pass
        if self._pw is not None:
            try:
                self._pw.stop()
            except Exception:  # noqa: S110, BLE001 - best-effort teardown
                pass
        self._started = False