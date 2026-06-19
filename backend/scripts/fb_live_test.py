"""One-off live diagnostic for the Facebook collector (run inside worker-browser).

Purpose: after capturing a session, prove two things that only a real run can tell us,
WITHOUT going through Celery/beat/the DB:

  1. Did the captured session authenticate?  (is_blocked -> login/checkpoint wall)
  2. Do the BEST-EFFORT DOM selectors in facebook_browser.py still match live Facebook?
     (raw <article> count vs. posts we actually extracted)

It builds a PlaywrightFeedDriver exactly the way the production task
``app.tasks.jobs.scrape_browser_source`` does (same Settings: session dir, headless,
locale, timezone, proxy), but targets a group passed on the CLI so no MonitoredSource
row is needed.

USAGE (from the repo root, stack already up):
    docker compose exec worker-browser python -m scripts.fb_live_test \
        "https://www.facebook.com/groups/<group-id-or-slug>"

    # options:
    #   --scrolls N   how many scroll passes to load posts (default 3)
    #   --samples N   how many extracted posts to print (default 5)

Read the output:
  * "BLOCKED" -> the session is not logged in (capture again / different account).
  * raw articles > 0 but extracted == 0 -> the feed loaded but our inner selectors
    drifted; tune _EXTRACT_JS in app/monitors/facebook_browser.py.
  * raw articles == 0 -> feed did not render (wrong group, private group the account
    can't see, or the outer feed/article selector drifted).
  * extracted > 0 -> selectors work; the collector is good to go.
"""

from __future__ import annotations

import argparse
import sys

# These selectors mirror the structure facebook_browser.py keys on; used only to tell
# "feed didn't load" apart from "our inner extraction selectors drifted".
_FEED_COUNT_JS = r"""
() => ({
  feeds: document.querySelectorAll('div[role="feed"]').length,
  articles: document.querySelectorAll('div[role="feed"] div[role="article"]').length,
  url: location.href,
})
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Live Facebook collector diagnostic.")
    parser.add_argument("group", help="group URL, relative path, bare id, or slug")
    parser.add_argument("--scrolls", type=int, default=3, help="scroll passes (default 3)")
    parser.add_argument("--samples", type=int, default=5, help="posts to print (default 5)")
    args = parser.parse_args(argv)

    from app.core.config import settings
    from app.monitors.facebook_browser import PlaywrightFeedDriver
    from app.services.facebook_group import facebook_group_id

    group_id = facebook_group_id(args.group)
    print(f"target group input : {args.group!r}")
    print(f"parsed group id    : {group_id!r}")
    print(f"session dir        : {settings.browser_session_dir}")
    print(f"headless           : {settings.browser_headless}")
    print("-" * 60)

    driver = PlaywrightFeedDriver(
        args.group,
        session_dir=settings.browser_session_dir,
        headless=settings.browser_headless,
        locale=settings.browser_locale,
        timezone=settings.browser_timezone,
    )

    try:
        # is_blocked() triggers _ensure_started: loads the encrypted session, launches
        # Chromium, navigates to the group. A FileNotFoundError here = no session yet.
        try:
            blocked = driver.is_blocked()
        except FileNotFoundError as exc:
            print(f"NO SESSION: {exc}")
            print("Run capture-fb.ps1 on the host first, then retry.")
            return 2

        if blocked:
            print("RESULT: BLOCKED -- a login/checkpoint wall is showing, not the feed.")
            print("The captured session is not authenticated for this group.")
            print("Re-capture with capture-fb.ps1 (and confirm the account is a member).")
            return 1

        print("auth               : OK (no login/checkpoint wall detected)")

        # Let lazy-loaded posts attach: scroll a few times, reading after each pass.
        seen: dict[str, object] = {}
        dom = {"feeds": 0, "articles": 0, "url": ""}
        for i in range(max(1, args.scrolls)):
            dom = driver._page.evaluate(_FEED_COUNT_JS)  # noqa: SLF001 - diagnostic only
            for post in driver.read_posts():
                seen.setdefault(post["external_id"], post)
            print(
                f"scroll {i + 1}/{args.scrolls}: role=feed nodes={dom['feeds']} "
                f"articles={dom['articles']} extracted_unique={len(seen)}"
            )
            driver.scroll()

        print("-" * 60)
        print(f"page url           : {dom['url']}")
        print(f"raw <article> nodes: {dom['articles']} (last pass)")
        print(f"extracted posts    : {len(seen)} unique")
        print("-" * 60)

        if dom["articles"] == 0:
            print("DIAGNOSIS: feed/article selectors found nothing. Either the feed did not")
            print("render (wrong/private group, account not a member) or the OUTER selector")
            print('div[role="feed"] div[role="article"] drifted. Check the page url above.')
            return 3
        if not seen:
            print("DIAGNOSIS: feed rendered but 0 posts extracted -> the INNER selectors in")
            print("app/monitors/facebook_browser.py (_EXTRACT_JS) drifted. Needs tuning.")
            return 4

        print("DIAGNOSIS: selectors work. Sample extracted posts:")
        for post in list(seen.values())[: args.samples]:
            text = (post.get("text") or "").replace("\n", " ").strip()
            print(f"\n  id        : {post.get('external_id')}")
            print(f"  author    : {post.get('author')}")
            print(f"  timestamp : {post.get('timestamp')}")
            print(f"  permalink : {post.get('permalink')}")
            print(f"  text      : {text[:160]}{'...' if len(text) > 160 else ''}")
        print("\nRESULT: PASS -- the Facebook collector is working end to end.")
        return 0
    finally:
        driver.close()


if __name__ == "__main__":
    sys.exit(main())
