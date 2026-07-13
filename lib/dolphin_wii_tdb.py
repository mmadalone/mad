"""GameTDB Classic-Controller capability for Wii games.

A Wii game is "Classic Controller capable" iff GameTDB's `wiitdb.xml` lists an
`<input><control type="classiccontroller"/>` for its 6-char GameID. We cache only the DERIVED set
of CC-capable GameIDs (`cc_ids.json`), never the ~10 MB XML. A bundled copy ships in
`data/gametdb/cc_ids.json` so the feature works offline out of the box; `refresh()` re-derives it
from a fresh download.

Gating is FAIL-CLOSED (Miquel's choice): unknown / unresolvable / offline / parse-fail -> NOT CC.
A homebrew or ROM-hack keeps the base game's first 4 GameID chars (system + game code + region) but
changes the maker code (`SMNE01` -> `SMNE03` / `SMNEXD`). So an uncatalogued hack inherits CC ONLY
from its RETAIL sibling (the `...01` entry): the prefix fallback fires only when `<prefix>01` is
itself CC-capable. That deliberately does NOT flip a whole family on one CC member -- e.g. New Super
Mario Bros. Wii (`SMNE01`) is not CC, so its ~130 hacks are not auto-flipped; the few that DO add a
Classic Controller (`SMNE03`, `SMNE40`, ...) are catalogued in GameTDB by exact id and match directly.
A manual `[backends.dolphin].cc_overrides` allowlist force-enables anything else.

ROM -> 6-char GameID reuses `lib/dolphin_gameids` (dolphin-tool, path+mtime cached).
"""
from __future__ import annotations

import io
import json
import os
import re
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from lib import dolphin_gameids
from lib.policy import load_merged

WIITDB_URL = "https://www.gametdb.com/wiitdb.zip?LANG=EN"
_CACHE = Path.home() / ".local/share/mad/gametdb/cc_ids.json"      # user cache (refreshable)
_BUNDLED = Path(__file__).resolve().parent.parent / "data/gametdb/cc_ids.json"   # ships in the repo
_ID_RE = re.compile(r"[A-Z0-9]{6}\Z")     # \Z (not $): reject a trailing newline
_MIN_CC_IDS = 500                          # sanity floor: a real parse has hundreds; below = truncated

_LOCK = threading.Lock()              # guards the lazy globals (MAD browser + launch may race)
_ids: set[str] | None = None          # lazy per-process (None = not loaded)
_retail_prefixes: set[str] | None = None    # {id[:4] for CC ids whose maker code is "01" = retail}
_meta: dict = {}


# --------------------------------------------------------------------------- parse / refresh
def _parse_cc_ids(source, strict: bool = False) -> set[str]:
    """The set of 6-char GameIDs whose `<input>` lists a `classiccontroller` control. `source` is a
    filename or a binary file object. Clears the ROOT after each <game> so memory stays bounded on
    the ~10 MB file. With `strict=True` a malformed/truncated document RE-RAISES (so refresh rejects
    a partial parse instead of caching it); the default swallows and returns what parsed."""
    ids: set[str] = set()
    root = None
    try:
        for ev, elem in ET.iterparse(source, events=("start", "end")):
            if root is None:                          # first "start" event is the document root
                root = elem
                continue
            if ev == "end" and elem.tag == "game":
                gid = (elem.findtext("id") or "").strip()
                if _ID_RE.match(gid):
                    inp = elem.find("input")
                    if inp is not None and any(
                            c.get("type") == "classiccontroller" for c in inp.findall("control")):
                        ids.add(gid)
                root.clear()                          # drop processed <game> shells (bounds memory)
    except (ET.ParseError, OSError, ValueError):
        if strict:
            raise
    return ids


def refresh(force: bool = False, logger=None) -> bool:
    """Download wiitdb.zip, re-derive the CC-capable id set, and atomically replace the user cache.
    Returns True on success. Best-effort: any failure leaves the existing cache untouched and
    returns False. `force` is accepted for API symmetry (there is no throttle here)."""
    try:
        req = urllib.request.Request(WIITDB_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=60) as r:
            blob = r.read()
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            name = next((n for n in z.namelist() if n.endswith("wiitdb.xml")), None)
            if not name:
                return False
            with z.open(name) as fh:
                ids = _parse_cc_ids(fh, strict=True)   # truncated/malformed -> raises -> caught below
        if len(ids) < _MIN_CC_IDS:        # a valid GameTDB file has hundreds; fewer = truncated parse
            if logger:
                logger.warning(f"dolphin_wii_tdb: refresh parsed only {len(ids)} ids; keeping old cache")
            return False
        _write_cache(ids)
        _reset()
        if logger:
            logger.info(f"dolphin_wii_tdb: refreshed, {len(ids)} CC-capable ids")
        return True
    except Exception as ex:               # network, zip, IO, parse -- never propagate
        if logger:
            logger.warning(f"dolphin_wii_tdb: refresh failed: {ex!r}")
        return False


def _write_cache(ids: set[str]) -> None:
    payload = {"generated": int(time.time()), "source": WIITDB_URL, "ids": sorted(ids)}
    _CACHE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _CACHE.with_suffix(f".tmp.{os.getpid()}")       # pid-unique: concurrent refresh can't collide
    tmp.write_text(json.dumps(payload))
    tmp.replace(_CACHE)


# --------------------------------------------------------------------------- load / lookup
def _load() -> dict:
    """The freshest available id payload: the user cache if present, else the bundled copy, else empty."""
    for p in (_CACHE, _BUNDLED):
        try:
            d = json.loads(p.read_text())
            if isinstance(d, dict) and isinstance(d.get("ids"), list):
                return d
        except (OSError, ValueError):
            continue
    return {"generated": 0, "source": "", "ids": []}


def _ensure() -> None:
    """Populate the lazy globals once, under the lock. `_ids` is assigned LAST (after its dependents)
    so a reader that sees `_ids is not None` -- even outside the lock -- always sees a built
    `_retail_prefixes`/`_meta` (mirrors dolphin_gameids's cache discipline)."""
    global _ids, _retail_prefixes, _meta
    if _ids is not None:
        return
    with _LOCK:
        if _ids is not None:                          # double-check inside the lock
            return
        d = _load()
        ids = {i for i in d.get("ids", []) if isinstance(i, str) and _ID_RE.match(i)}
        # A hack inherits CC only from its RETAIL sibling: keep prefixes whose "01" (retail) entry is CC.
        _retail_prefixes = {i[:4] for i in ids if i[4:6] == "01"}
        _meta = {"generated": int(d.get("generated") or 0), "source": str(d.get("source") or "")}
        _ids = ids                                    # guard assigned last


def _reset() -> None:
    """Drop the in-process cache so the next lookup reloads (used after refresh + by tests)."""
    global _ids, _retail_prefixes, _meta
    with _LOCK:
        _ids = None
        _retail_prefixes = None
        _meta = {}


def _overrides() -> set[str]:
    try:
        be = (load_merged().get("backends") or {}).get("dolphin")
        ov = be.get("cc_overrides") if isinstance(be, dict) else None
        return {str(x).upper() for x in ov} if isinstance(ov, list) else set()
    except Exception:
        return set()


def _resolve(rom_or_id) -> str | None:
    """A bare 6-char GameID passes through; anything else is treated as a ROM path -> dolphin-tool."""
    s = str(rom_or_id).strip()
    if _ID_RE.match(s):
        return s
    return dolphin_gameids.gameid(s)


def is_cc_capable(rom_or_id) -> bool:
    """True iff this ROM/GameID supports a Classic Controller (fail-closed on any uncertainty).
    Direct membership, then the 4-char base-game prefix (rescues hacks), then the manual override."""
    gid = _resolve(rom_or_id)
    if not gid:
        return False
    _ensure()
    if gid in _ids or gid[:4] in _retail_prefixes:
        return True
    return gid in _overrides()


def cc_capable_games(roms: list) -> dict:
    """{rom_path: bool} for many ROMs (batch GameID resolve, then membership) -- for the browser."""
    resolved = dolphin_gameids.gameids(roms)              # {abspath: gid|None}
    _ensure()
    ov = _overrides()
    return {rom: bool(gid) and (gid in _ids or gid[:4] in _retail_prefixes or gid in ov)
            for rom, gid in resolved.items()}


def status() -> dict:
    """{available, count, age_days} for the UI note / refresh nudge (age_days None if never dated)."""
    _ensure()
    gen = _meta.get("generated", 0)
    age = int((time.time() - gen) / 86400) if gen else None
    return {"available": bool(_ids), "count": len(_ids or ()), "age_days": age}
