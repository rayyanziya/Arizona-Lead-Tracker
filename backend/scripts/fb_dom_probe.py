"""One-off live DOM probe for the Facebook feed (run inside worker-browser).

Purpose: when fb_live_test.py reports "feed rendered but 0 posts extracted" (the
INNER selectors in app/monitors/facebook_browser.py drifted), this dumps the ACTUAL
structure of each ``div[role="article"]`` node so the selectors can be rewritten
against reality instead of guesswork.

It builds a PlaywrightFeedDriver exactly like fb_live_test.py / the production task,
scrolls a few times, then prints, per article: every anchor href, the aria-label,
a text preview, and any time-ish elements with their attributes.

USAGE (from the repo root, stack up):
    docker compose exec worker-browser python -m scripts.fb_dom_probe \
        "https://www.facebook.com/groups/<group-id-or-slug>"

    # options:
    #   --scrolls N    scroll passes before dumping (default 3)
    #   --max N        max article nodes to dump (default 4)
    #   --html         also print a trimmed outerHTML snippet per node
"""

from __future__ import annotations

import argparse
import json
import sys

# Pull a debuggable shape from each article node. Kept verbose on purpose -- this is a
# throwaway diagnostic, not a production selector.
_PROBE_JS = r"""
(maxNodes) => {
  const articles = Array.from(
    document.querySelectorAll('div[role="feed"] div[role="article"]')
  ).slice(0, maxNodes);
  return articles.map((art, index) => {
    const hrefs = Array.from(art.querySelectorAll('a[href]'))
      .map(a => a.getAttribute('href'))
      .filter(Boolean)
      .slice(0, 25);
    const timeish = Array.from(art.querySelectorAll('abbr, time, a[role="link"]'))
      .slice(0, 8)
      .map(el => ({
        tag: el.tagName.toLowerCase(),
        utime: el.getAttribute('data-utime'),
        datetime: el.getAttribute('datetime'),
        aria: el.getAttribute('aria-label'),
        text: (el.textContent || '').trim().slice(0, 40),
      }));
    return {
      index,
      aria_label: art.getAttribute('aria-label'),
      hrefs,
      text_preview: (art.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 240),
      timeish,
      html: (art.outerHTML || '').slice(0, 1500),
    };
  });
}
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Live Facebook article DOM probe.")
    parser.add_argument("group", help="group URL, relative path, bare id, or slug")
    parser.add_argument("--scrolls", type=int, default=3, help="scroll passes (default 3)")
    parser.add_argument("--max", type=int, default=4, help="article nodes to dump (default 4)")
    parser.add_argument("--html", action="store_true", help="also print outerHTML snippets")
    args = parser.parse_args(argv)

    from app.core.config import settings
    from app.monitors.facebook_browser import PlaywrightFeedDriver

    driver = PlaywrightFeedDriver(
        args.group,
        session_dir=settings.browser_session_dir,
        headless=settings.browser_headless,
        locale=settings.browser_locale,
        timezone=settings.browser_timezone,
    )
    try:
        try:
            blocked = driver.is_blocked()
        except FileNotFoundError as exc:
            print(f"NO SESSION: {exc}")
            return 2
        if blocked:
            print("BLOCKED: login/checkpoint wall, not the feed.")
            return 1

        for _ in range(max(1, args.scrolls)):
            driver.scroll()

        nodes = driver._page.evaluate(_PROBE_JS, args.max)  # noqa: SLF001 - diagnostic only
        print(f"page url: {driver._page.url}")  # noqa: SLF001
        print(f"dumped {len(nodes)} article node(s)")
        for node in nodes:
            print("=" * 70)
            print(f"[article {node['index']}] aria_label={node['aria_label']!r}")
            print(f"  text_preview: {node['text_preview']!r}")
            print("  hrefs:")
            for href in node["hrefs"]:
                print(f"    - {href}")
            print("  timeish:")
            for t in node["timeish"]:
                print(f"    - {json.dumps(t)}")
            if args.html:
                print("  html (trimmed):")
                print("    " + node["html"].replace("\n", " "))
        return 0
    finally:
        driver.close()


if __name__ == "__main__":
    sys.exit(main())
