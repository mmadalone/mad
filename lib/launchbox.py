"""launchbox.py — art source for games the conventional scrapers do not carry.

The LaunchBox Games Database is the only public DB with a real OpenBOR platform
(id 139, ~400 community-curated entries), and unlike SteamGridDB it holds proper
BOXART for fan games: "Box - Front", "Clear Logo" and "Fanart - Background", which
map straight onto ES-DE's covers / marquees / fanart.

Used by openbor-fetch-media.py as the tier below SteamGridDB. Between them they
cover games that have no Steam shortcut and no entry in any ordinary scraper DB.

There is no public API, so this parses the site's own HTML. Two anchors it relies
on, both stable as of 2026-08-01:

  * the platform listing paginates with ``?page=N`` and yields
    ``href="/games/details/<id>-<slug>"`` (100 per page, empty page = end);
  * the per-game images page lists every asset as

        <a href="https://images.launchbox-app.com/<uuid>.<ext>"
           data-title="<Game> - <Type> Image (<Region>)"
           data-footer="1800 x 2550 JPEG, 1 MB...">

    so ``data-title`` gives the asset TYPE and ``data-footer`` its dimensions.

If LaunchBox restyles those pages this module degrades to "found nothing" rather
than to wrong art — callers treat an empty result as a miss and leave the game be.

Matching reuses lib.sgdb.similarity so both art sources apply the same strict
name gate, for the same reason: a fuzzy hit on the wrong game is worse than no art.
"""
from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

from . import sgdb

__all__ = ["catalogue", "find", "images", "best", "MATCH_THRESHOLD", "TYPE_MAP"]

BASE = "https://gamesdb.launchbox-app.com"
PLATFORM_OPENBOR = "139-openbor"
UA = "Mozilla/5.0 (X11; Linux x86_64)"
TIMEOUT = 30
MATCH_THRESHOLD = 0.90       # stricter than SGDB: this catalogue is full of
                             # same-franchise fan games that differ by one word
CACHE = Path.home() / ".cache/openbor-launchbox-catalogue.json"
CACHE_TTL = 14 * 24 * 3600

# LaunchBox asset type (as it appears in data-title) -> our art kind.
TYPE_MAP = {
    "Box - Front": "cover",
    "Clear Logo": "marquee",
    "Fanart - Background": "fanart",
}


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.read().decode("utf-8", "replace")
    except Exception:
        return ""


def catalogue(platform=PLATFORM_OPENBOR, refresh=False):
    """[(slug, display_name)] for every game on the platform. Cached on disk."""
    if not refresh and CACHE.is_file():
        try:
            blob = json.loads(CACHE.read_text())
            if blob.get("platform") == platform and \
                    time.time() - blob.get("fetched", 0) < CACHE_TTL:
                return [tuple(x) for x in blob["games"]]
        except (OSError, ValueError, KeyError):
            pass
    games, seen = [], set()
    for page in range(1, 40):                     # generous cap; loop exits on empty
        html = _get(f"{BASE}/platforms/games/{platform}?page={page}")
        found = re.findall(r'href="/games/details/([^"]+)"', html)
        if not found:
            break
        for slug in found:
            if slug in seen:
                continue
            seen.add(slug)
            # slug is "<id>-<kebab-name>"; recover a display name from the kebab part
            name = re.sub(r"^\d+-", "", slug).replace("-", " ")
            games.append((slug, name))
    if games:
        try:
            CACHE.parent.mkdir(parents=True, exist_ok=True)
            CACHE.write_text(json.dumps(
                {"platform": platform, "fetched": int(time.time()), "games": games}))
        except OSError:
            pass
    return games


def find(queries, threshold=MATCH_THRESHOLD, platform=PLATFORM_OPENBOR):
    """Best-matching slug for one or more title spellings, or None.

    Strict on purpose — see the module docstring and lib/sgdb.
    """
    if isinstance(queries, str):
        queries = [queries]
    cat = catalogue(platform)
    best_hit = None
    for q in queries:
        if not q:
            continue
        for slug, name in cat:
            score = sgdb.similarity(q, name)
            if best_hit is None or score > best_hit[2]:
                best_hit = (slug, name, score, q)
    if best_hit and best_hit[2] >= threshold:
        return best_hit
    return None


_IMG_RE = re.compile(
    r'href="(https://images\.launchbox-app\.com/[^"]+)"'
    r'[^>]*?data-title="([^"]*)"'
    r'(?:[^>]*?data-footer="([^"]*)")?',
    re.S)
_DIM_RE = re.compile(r"(\d+)\s*x\s*(\d+)")


def images(slug):
    """[{url, type, width, height}] for every asset on a game's images page."""
    html = _get(f"{BASE}/games/images/{slug}")
    out = []
    for url, title, footer in _IMG_RE.findall(html):
        # data-title looks like "<Game> - <Type> Image (<Region>)"
        m = re.search(r"-\s*([^-]*?)\s*Image\s*(?:\(|$)", title)
        kind = m.group(1).strip() if m else ""
        if not kind:
            continue
        # Recover the full "Box - Front" style label, which contains a hyphen too.
        for label in TYPE_MAP:
            if title.replace(" Image", "").endswith(label) or label in title:
                kind = label
                break
        d = _DIM_RE.search(footer or "")
        out.append({"url": url, "type": kind,
                    "width": int(d.group(1)) if d else 0,
                    "height": int(d.group(2)) if d else 0})
    return out


def best(slug, art_kind, items=None):
    """Largest asset matching `art_kind` ('cover' | 'marquee' | 'fanart'), or None."""
    wanted = [lb for lb, ours in TYPE_MAP.items() if ours == art_kind]
    if not wanted:
        return None
    items = images(slug) if items is None else items
    cands = [i for i in items if i["type"] in wanted]
    if not cands:
        return None
    return max(cands, key=lambda i: i["width"] * i["height"])
