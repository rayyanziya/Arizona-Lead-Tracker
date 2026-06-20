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
    // Skip skeleton/placeholder cards that have not hydrated yet.
    if ((art.innerText || '').trim().length < 1) continue;

    // Permalink -> post id. Prefer the post's OWN link (no comment_id); fall back to a
    // comment-story link so a "X commented on a post" card still resolves to its parent
    // post. The parser strips the ?comment_id query, leaving a clean post URL either way.
    let permalink = null, commentFallback = null;
    for (const a of art.querySelectorAll('a[href]')) {
      const href = a.getAttribute('href') || '';
      if (!/\/(posts|permalink)\/\d+/.test(href)) continue;
      if (href.includes('comment_id')) { if (!commentFallback) commentFallback = a.href; }
      else { permalink = a.href; break; }
    }
    if (!permalink) permalink = commentFallback;
    if (!permalink) continue;  // no stable post id -> let read_posts drop it

    // Author = first VISIBLE profile link with real text. The avatar link to the same
    // profile is aria-hidden, so skip it; this yields a clean name ("Jane Doe") rather
    // than the "Comment by Jane Doe 3 days ago" aria-label we used to grab.
    let author = null;
    for (const a of art.querySelectorAll('a[href*="/user/"], h2 a, h3 a, strong a')) {
      if (a.getAttribute('aria-hidden') === 'true') continue;
      const t = (a.textContent || '').trim();
      if (t) { author = t; break; }
    }

    // Message text: the dedicated post-body container if present, else the longest
    // dir=auto block (avoids grabbing a one-word UI label over the real content).
    let text = null;
    const msg = art.querySelector('div[data-ad-preview="message"]');
    if (msg) {
      text = msg.innerText;
    } else {
      let best = '';
      for (const d of art.querySelectorAll('div[dir="auto"]')) {
        const t = (d.innerText || '').trim();
        if (t.length > best.length) best = t;
      }
      text = best || null;
    }

    // Timestamp: keep the legacy data-utime path for any surface that still emits it;
    // modern group feeds render only relative text ("1d"), which has no epoch -> null.
    let timestamp = null;
    const t = art.querySelector('abbr[data-utime]');
    if (t && t.getAttribute('data-utime')) timestamp = Number(t.getAttribute('data-utime'));

    out.push({ permalink, author, text, timestamp });
  }
  return out;
}
"""

_LOGIN_HINTS = ("login", "checkpoint", "/login/")

# Anti-automation hardening. Facebook serves a skeleton-only feed (posts never hydrate)
# to browsers that announce themselves as automated. The two tells it keys on are the
# "HeadlessChrome" user-agent and navigator.webdriver === true; we neutralise both. The
# UA just drops "Headless" from the real one so it stays consistent with the Linux
# platform the container actually runs on. Verified against the live feed -- without
# this, role=article nodes render as empty placeholders.
_STEALTH_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
)
_STEALTH_INIT_JS = "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"


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
        launch_kwargs: dict = {
            "headless": self._headless,
            # Stops Chromium from exposing the AutomationControlled blink feature.
            "args": ["--disable-blink-features=AutomationControlled"],
        }
        if self._proxy:
            launch_kwargs["proxy"] = self._proxy
        self._browser = self._pw.chromium.launch(**launch_kwargs)
        ctx_kwargs: dict = {
            "storage_state": json.loads(state_json),
            "user_agent": _STEALTH_UA,  # drop the "HeadlessChrome" tell
        }
        if self._locale:
            ctx_kwargs["locale"] = self._locale
        if self._timezone:
            ctx_kwargs["timezone_id"] = self._timezone
        self._context = self._browser.new_context(**ctx_kwargs)
        # Must run before any navigation so navigator.webdriver is masked on first paint.
        self._context.add_init_script(_STEALTH_INIT_JS)
        self._page = self._context.new_page()
        self._page.goto(self._group_url, wait_until="domcontentloaded")
        self._started = True
        self._settle_feed()

    def _settle_feed(self) -> None:
        """Wait for the async feed to stream in real posts, not just skeleton cards.

        Facebook renders empty ``div[role="article"]`` placeholders immediately, then
        hydrates them over several seconds via background fetches. Extracting too early
        yields zero posts. We wait (bounded) until at least one article has real text,
        falling back to a fixed pause so a quiet group never hangs the run.
        """
        try:
            self._page.wait_for_function(
                "() => Array.from(document.querySelectorAll("
                "'div[role=\"feed\"] div[role=\"article\"]'))"
                ".some(a => (a.innerText || '').trim().length > 20)",
                timeout=20000,
            )
        except Exception:  # noqa: BLE001 - quiet/empty group or slow feed; fall through
            self._page.wait_for_timeout(4000)

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
        # FB streams newly-revealed posts in over a couple of seconds; 800ms was too
        # short and left freshly-attached articles as empty skeletons at extract time.
        self._page.wait_for_timeout(2500)

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