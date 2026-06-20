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
  const all = Array.from(document.querySelectorAll('div[role="feed"] div[role="article"]'));
  // Top-level post = an article with no article ancestor (comments are nested articles).
  const topLevel = all.filter(a => !a.parentElement.closest('div[role="article"]'));
  const nonEmpty = topLevel.filter(a => (a.innerText || '').trim().length > 20);
  const postLink = (art) => {
    for (const a of art.querySelectorAll('a[href]')) {
      const href = a.getAttribute('href') || '';
      if (/\/(posts|permalink)\/\d+/.test(href) && !href.includes('comment_id')) return a.href;
    }
    return null;
  };
  const hasCommentLink = (art) => Array.from(art.querySelectorAll('a[href]'))
    .some(a => (a.getAttribute('href') || '').includes('comment_id'));
  const authorName = (art) => {
    // First visible (non aria-hidden) link to a user/profile with real text.
    for (const a of art.querySelectorAll('a[href*="/user/"], h2 a, h3 a, strong a')) {
      if (a.getAttribute('aria-hidden') === 'true') continue;
      const t = (a.textContent || '').trim();
      if (t) return t;
    }
    return null;
  };
  const message = (art) => {
    const m = art.querySelector('div[data-ad-preview="message"]');
    if (m) return (m.innerText || '').trim();
    let best = '';
    for (const d of art.querySelectorAll('div[dir="auto"]')) {
      const t = (d.innerText || '').trim();
      if (t.length > best.length) best = t;
    }
    return best;
  };
  return {
    counts: { all: all.length, topLevel: topLevel.length, nonEmptyTopLevel: nonEmpty.length },
    posts: nonEmpty.slice(0, maxNodes).map(art => ({
      aria_label: art.getAttribute('aria-label'),
      post_link: postLink(art),
      has_comment_link: hasCommentLink(art),
      author: authorName(art),
      message: message(art).slice(0, 160),
      text_preview: (art.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 200),
    })),
  };
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

        result = driver._page.evaluate(_PROBE_JS, args.max)  # noqa: SLF001 - diagnostic only
        print(f"page url: {driver._page.url}")  # noqa: SLF001
        print(f"counts: {json.dumps(result['counts'])}")
        posts = result["posts"]
        print(f"dumped {len(posts)} top-level post node(s)")
        for i, node in enumerate(posts):
            print("=" * 70)
            print(f"[post {i}] aria_label={node['aria_label']!r}")
            print(f"  post_link : {node['post_link']}")
            print(f"  comment_link_present: {node['has_comment_link']}")
            print(f"  author    : {node['author']!r}")
            print(f"  message   : {node['message']!r}")
            print(f"  text_prev : {node['text_preview']!r}")
        return 0
    finally:
        driver.close()


if __name__ == "__main__":
    sys.exit(main())
