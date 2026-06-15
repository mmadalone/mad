"""Shared Switch game name/list resolver for the per-game settings pickers.

Builds a {titleid -> friendly name} map from the best available sources, then
`listing()` turns it into the [{titleid,name,override}] payload both
`ryujinx.games` and `eden.games` return. Name sources, best first:
  1. Ryujinx per-game metadata `games/<tid>/gui/metadata.json` `"title"`.
  2. A ROM whose filename carries a `[TITLEID]` (display = cleaned filename).
  3. An Eden per-game ini `custom/<TID>.ini` (so Eden-only games still appear;
     name falls back to the titleid when unknown).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

_RYUJINX_GAMES = Path.home() / ".config/Ryujinx/games"
_EDEN_CUSTOM = Path.home() / ".config/eden/custom"
_ROMS = Path.home() / "ROMs/switch"
_TITLEID_RE = re.compile(r"\[([0-9A-Fa-f]{16})\]")
_TAG_RE = re.compile(r"\s*[\[(][^\])]*[\])]")        # strip [..] / (..) filename tags


def _rom_name(p: Path) -> str:
    return _TAG_RE.sub("", p.stem).strip() or p.stem


def names() -> dict:
    """titleid (lowercase) -> friendly name, merged across sources."""
    out: dict[str, str] = {}
    try:
        for meta in _RYUJINX_GAMES.glob("*/gui/metadata.json"):
            try:
                title = (json.loads(meta.read_text(encoding="utf-8")) or {}).get("title")
            except (OSError, ValueError):
                title = None
            if title:
                out[meta.parent.parent.name.lower()] = title
    except OSError:
        pass
    try:
        for rom in list(_ROMS.glob("*.nsp")) + list(_ROMS.glob("*.xci")):
            m = _TITLEID_RE.search(rom.name)
            if m:
                out.setdefault(m.group(1).lower(), _rom_name(rom))
    except OSError:
        pass
    try:                                              # surface Eden-only games too
        for ini in _EDEN_CUSTOM.glob("*.ini"):
            tid = ini.stem.lower()
            if len(tid) == 16:
                out.setdefault(tid, tid.upper())
    except OSError:
        pass
    return out


def listing(override_fn) -> list:
    """[{titleid,name,override}] sorted by name. override_fn(titleid)->bool marks
    which games already carry a per-emulator override."""
    items = [{"titleid": tid, "name": name, "override": bool(override_fn(tid))}
             for tid, name in names().items()]
    items.sort(key=lambda g: g["name"].lower())
    return items
