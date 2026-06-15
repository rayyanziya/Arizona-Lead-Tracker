"""Parse a Facebook group identifier from whatever an operator pastes.

A group can be entered as a full desktop URL, a mobile URL, a relative
``groups/<id>`` path, a bare numeric id, or a vanity slug. This module turns any
of those into the canonical group token (or ``None`` when the input isn't a
group at all). Kept pure -- no network, no Playwright -- so the API can validate
a new source with the exact rule the scraper uses to target the group, and the
two can never drift.
"""

from __future__ import annotations

import re

# A group token inside a URL: everything after a /groups/ segment up to the next
# slash, query, hash, or whitespace. The (?:^|/) anchor also accepts a relative
# "groups/<id>" with no leading slash. Matches desktop, mobile, and relative forms.
_GROUPS_PATH = re.compile(r"(?:^|/)groups/([^/?#\s]+)")

# A bare id or vanity slug: digits, letters, dot, hyphen, underscore -- the
# characters Facebook allows in a group's numeric id or custom URL.
_BARE_TOKEN = re.compile(r"[\w.-]+")


def facebook_group_id(identifier: str | None) -> str | None:
    """Return the canonical group token, or ``None`` if not a Facebook group.

    >>> facebook_group_id("https://www.facebook.com/groups/123/?ref=x")
    '123'
    >>> facebook_group_id("phoenix-umkm")
    'phoenix-umkm'
    >>> facebook_group_id("looking for a group") is None
    True
    """
    text = (identifier or "").strip()
    if not text:
        return None

    in_url = _GROUPS_PATH.search(text)
    if in_url:
        return in_url.group(1)

    # No /groups/ segment: accept only a clean bare id/slug (no path, no spaces).
    if "/" not in text and _BARE_TOKEN.fullmatch(text):
        return text
    return None