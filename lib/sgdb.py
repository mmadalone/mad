"""
SteamGridDB fallback for the steam media tools — used only when Steam's own
local/CDN art is unavailable (e.g. brand-new releases with no library capsule).

Key handling (NEVER persisted BY THIS MODULE — it's a secret):
  1. $SGDB_API_KEY / $STEAMGRIDDB_API_KEY env var, else
  2. ~/.claude/tokens/steamgriddb.md (the tokens dir, mode 0700, alongside the
     GitHub PAT and ScreenScraper creds), else
  3. one interactive getpass prompt (only if stdin is a TTY), else
  4. None → SGDB fallback is silently skipped.
The key is held in memory for the process lifetime only.

Lookups are by Steam appid (Steam games) or by name autocomplete (non-Steam).
Art kinds map to SGDB endpoints: cover→grids(portrait), fanart→heroes, marquee→logos.

NAME LOOKUPS ARE STRICT ON PURPOSE. /search/autocomplete is fuzzy and ALWAYS
returns something, so taking data[0] silently yields a different game's art.
Measured against the OpenBOR collection (2026-08-01):

    "The Punisher and Nick Fury" -> "The Punisher"        (1993 Capcom arcade)
    "Showdown Revenge"           -> "Samurai Shodown IV"
    "Ultimate Double Dragon"     -> "Battletoads/Double Dragon"

All three would have produced confident, completely wrong covers. So a candidate is
accepted only when its NORMALISED name is >= MATCH_THRESHOLD similar to the query.
Returning nothing beats returning the wrong game.

Cloudflare fronts this API and 403s Python's default urllib User-Agent, hence the
explicit UA on every request. Symptom if that regresses: every lookup "finds
nothing" while the same URL works fine under curl.
"""
from __future__ import annotations

import difflib
import os
import re
import sys
import json
import getpass
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path

_key = None
_asked = False
KEY_FILE = Path.home() / ".claude/tokens/steamgriddb.md"
MATCH_THRESHOLD = 0.85
_API = "https://www.steamgriddb.com/api/v2/"
ENDPOINT = {
    "cover": ("grids", "dimensions=600x900,342x482,660x930&types=static"),
    "fanart": ("heroes", "dimensions=1920x620,3840x1240,1600x650&types=static"),
    "marquee": ("logos", "types=static"),
}


def get_key():
    """Resolve the SGDB API key once (env → prompt → None). Cached in memory."""
    global _key, _asked
    if _key:
        return _key
    _key = os.environ.get("SGDB_API_KEY") or os.environ.get("STEAMGRIDDB_API_KEY")
    if _key:
        return _key
    try:
        # The tokens dir holds .md files, and siblings there carry labels
        # ("usr: ...", "pwd: ..."), so pick the first token that actually LOOKS
        # like a key rather than blindly taking word 0 — that way adding a
        # heading or a comment line later cannot silently break the lookup.
        m = re.search(r"\b[A-Za-z0-9_-]{20,}\b", KEY_FILE.read_text())
        if m:
            _key = m.group(0)
            return _key
    except OSError:
        pass
    _key = None
    if not _asked:
        _asked = True
        if sys.stdin and sys.stdin.isatty():
            try:
                _key = getpass.getpass(
                    "SteamGridDB API key for art fallback (Enter to skip): ").strip() or None
            except (EOFError, KeyboardInterrupt):
                _key = None
    return _key


def available():
    return bool(get_key())


def _api(path):
    k = get_key()
    if not k:
        return None
    try:
        req = urllib.request.Request(
            _API + path, headers={"Authorization": f"Bearer {k}", "User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            d = json.load(r)
        return d if d.get("success") else None
    except Exception:
        return None


_NOISE = re.compile(
    r"\b(the|a|an|of|and|openbor|remake|edition|final|demo|ver|version|"
    r"definitive|complete|full|game|v?\d+(\.\d+)*)\b")


def norm_title(s):
    """Normalise a title for comparison: drop bracketed notes, filler words, punctuation."""
    s = (s or "").lower()
    s = re.sub(r"\(.*?\)|\[.*?\]", " ", s)          # "(OpenBOR)", "[v1.01]"
    s = _NOISE.sub(" ", s)
    return re.sub(r"[^a-z0-9]", "", s)


def similarity(a, b):
    """0..1 similarity of two game titles, ignoring punctuation/version/filler words.

    Shared with lib/launchbox.py so both art sources judge a candidate the same way.
    """
    return difflib.SequenceMatcher(None, norm_title(a), norm_title(b)).ratio()


def find_game(queries, threshold=MATCH_THRESHOLD):
    """Best-matching SGDB game for one or more title spellings.

    `queries` may be a string or an ordered list of spellings to try. Returns
    (game_id, matched_name, score, query) or None when nothing clears `threshold`.
    See the module docstring for why this is strict rather than "first result wins".
    """
    if isinstance(queries, str):
        queries = [queries]
    best = None
    for q in queries:
        if not q:
            continue
        d = _api("search/autocomplete/" + urllib.parse.quote(q))
        for cand in ((d or {}).get("data") or [])[:10]:
            score = similarity(q, cand.get("name"))
            if best is None or score > best[2]:
                best = (cand.get("id"), cand.get("name"), score, q)
    if best and best[0] and best[2] >= threshold:
        return best
    return None


def _game_id(name):
    hit = find_game(name)
    return hit[0] if hit else None


def art_items(kind, gid, filtered=True):
    """All assets of `kind` for a game id, newest-API order, junk removed.

    `filtered=False` drops the dimension/type query string. The curated dimension
    filters suit Steam titles with proper capsule art; obscure fan games often have
    a single odd-sized upload that the filter would exclude entirely, so callers
    that would rather rank locally can turn it off.
    """
    if kind not in ENDPOINT:
        return []
    ep, q = ENDPOINT[kind]
    d = _api(f"{ep}/game/{gid}" + (f"?{q}" if filtered else ""))
    return [a for a in ((d or {}).get("data") or [])
            if a.get("url") and not a.get("nsfw") and not a.get("humor")]


def best_item(kind, gid):
    """Best single asset for `kind`: try the curated dimensions, else rank locally.

    Ranking prefers the right SHAPE before raw size — ES-DE draws a cover as boxart,
    so a 2:3 grid beats a square or wide one even when the square one is bigger.
    """
    items = art_items(kind, gid, filtered=True) or art_items(kind, gid, filtered=False)
    if not items:
        return None

    def score(a):
        w, h = a.get("width") or 0, a.get("height") or 0
        shape = 0
        if kind == "cover" and h:
            shape = 1 if abs((w / h) - (2 / 3)) < 0.08 else 0
        elif kind == "fanart" and h:
            shape = 1 if (w / h) > 2.0 else 0
        return (shape, w * h)

    return max(items, key=score)


def art_url(kind, appid=None, name=None):
    """First SGDB art URL of `kind` for a Steam appid (preferred) or game name.
    Returns None if no key / no result."""
    if kind not in ENDPOINT or not available():
        return None
    ep, q = ENDPOINT[kind]
    if appid:
        d = _api(f"{ep}/steam/{appid}?{q}")
        g = (d or {}).get("data") or []
        if g:
            return g[0]["url"]
    if name:
        gid = _game_id(name)          # strict match — see module docstring
        if gid:
            item = best_item(kind, gid)
            if item:
                return item["url"]
    return None


if __name__ == "__main__":   # quick manual test: SGDB_API_KEY=... python3 lib/sgdb.py 2114740
    aid = int(sys.argv[1]) if len(sys.argv) > 1 else 2114740
    print("key available:", available())
    for k in ENDPOINT:
        print(f"  {k}:", art_url(k, appid=aid))
