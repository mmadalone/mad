"""
SteamGridDB fallback for the steam media tools — used only when Steam's own
local/CDN art is unavailable (e.g. brand-new releases with no library capsule).

Key handling (NEVER persisted — it's a secret):
  1. $SGDB_API_KEY / $STEAMGRIDDB_API_KEY env var, else
  2. one interactive getpass prompt (only if stdin is a TTY), else
  3. None → SGDB fallback is silently skipped.
The key is held in memory for the process lifetime only.

Lookups are by Steam appid (Steam games) or by name autocomplete (non-Steam).
Art kinds map to SGDB endpoints: cover→grids(portrait), fanart→heroes, marquee→logos.
"""
from __future__ import annotations

import os
import sys
import json
import getpass
import urllib.parse
import urllib.request
import urllib.error

_key = None
_asked = False
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


def _game_id(name):
    d = _api("search/autocomplete/" + urllib.parse.quote(name))
    g = (d or {}).get("data") or []
    return g[0]["id"] if g else None


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
        gid = _game_id(name)
        if gid:
            d = _api(f"{ep}/game/{gid}?{q}")
            g = (d or {}).get("data") or []
            if g:
                return g[0]["url"]
    return None


if __name__ == "__main__":   # quick manual test: SGDB_API_KEY=... python3 lib/sgdb.py 2114740
    aid = int(sys.argv[1]) if len(sys.argv) > 1 else 2114740
    print("key available:", available())
    for k in ENDPOINT:
        print(f"  {k}:", art_url(k, appid=aid))
