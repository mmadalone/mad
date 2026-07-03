"""Shared Switch game name/list resolver for the per-game settings pickers (eden / ryujinx /
citron).

Builds a {titleid -> friendly name} map of the user's CURRENT Switch library, then `listing()`
turns it into the [{titleid,name,override}] picker payload.

Inclusion (which games appear) = the ROMs the user actually has:
  1. ROMs in ~/ROMs/switch whose filename carries a `[TITLEID]` tag.
  2. Citron's game scan (custom_metadata.json) -- resolves the titleids of ROMs whose FILENAME
     lacks a `[TITLEID]` tag. Citron re-scans the ROM dir and keeps an accurate list, so this
     matches the count the emulators themselves show. (Empty if Citron isn't installed -> tagged
     ROMs only.)
Names (best first): Ryujinx per-game metadata title, then the cleaned ROM filename, then the titleid.

DELIBERATELY NOT inclusion sources: Ryujinx's per-game metadata dir and Eden's custom/*.ini. Both
ACCUMULATE and never prune, so they list games removed long ago -- the old bloat that made a 6-11
game library show as 40+. They are used here only as NAME sources for games already included.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

_RYUJINX_GAMES = Path.home() / ".config/Ryujinx/games"
_CITRON_METADATA = Path.home() / ".config/citron/custom_metadata/custom_metadata.json"
_ROMS = Path.home() / "ROMs/switch"
_TITLEID_RE = re.compile(r"\[([0-9A-Fa-f]{16})\]")
_TAG_RE = re.compile(r"\s*[\[(][^\])]*[\])]")        # strip [..] / (..) filename tags
_ROM_EXTS = {".nsp", ".xci", ".nsz", ".xcz"}         # Switch ROM containers (case-insensitive)


def _rom_name(p: Path) -> str:
    return _TAG_RE.sub("", p.stem).strip() or p.stem


def _citron_library() -> set:
    """Current-library titleids from Citron's scan (custom_metadata.json). program_id is stored
    with the leading zero stripped, so zero-pad to 16. Best-effort: empty if absent/unreadable."""
    out = set()
    try:
        meta = json.loads(_CITRON_METADATA.read_text(encoding="utf-8"))
        entries = meta.get("entries") if isinstance(meta, dict) else None
        for e in (entries or []):
            if not isinstance(e, dict):
                continue                              # a malformed entry must not crash the SHARED resolver
            pid = str(e.get("program_id") or "").lower()
            if pid:
                tid = pid.zfill(16)
                if len(tid) == 16:
                    out.add(tid)
    except (OSError, ValueError, AttributeError, TypeError):
        return set()
    return out


def _library() -> dict:
    """titleid (lowercase) -> {"name":..., "stem":...} for the user's current Switch library (see
    module doc). stem = the ROM filename stem (ES-DE FileData getStem parity: tags kept,
    case-preserved) for the media browser; "" when the titleid came ONLY from Citron's untagged
    scan (no ROM filename to key media off here -> that row shows info without media)."""
    rom_name: dict[str, str] = {}
    rom_stem: dict[str, str] = {}
    try:
        for rom in _ROMS.iterdir():
            if rom.suffix.lower() not in _ROM_EXTS:
                continue
            m = _TITLEID_RE.search(rom.name)
            if m:
                tid = m.group(1).lower()
                rom_name[tid] = _rom_name(rom)
                rom_stem[tid] = rom.stem      # full stem incl. tags -> matches ES-DE FileData getStem
    except OSError:
        pass
    ids = set(rom_name) | _citron_library()
    ryu_name: dict[str, str] = {}
    try:
        for meta in _RYUJINX_GAMES.glob("*/gui/metadata.json"):
            tid = meta.parent.parent.name.lower()
            if tid not in ids:                        # name source only -- never adds a game
                continue
            try:
                title = (json.loads(meta.read_text(encoding="utf-8")) or {}).get("title")
            except (OSError, ValueError):
                title = None
            if title:
                ryu_name[tid] = title
    except OSError:
        pass
    return {tid: {"name": (ryu_name.get(tid) or rom_name.get(tid) or tid.upper()),
                  "stem": rom_stem.get(tid, "")}
            for tid in ids}


def names() -> dict:
    """titleid (lowercase) -> friendly name for the user's current Switch library (see module doc)."""
    return {tid: info["name"] for tid, info in _library().items()}


def listing(override_fn, summary_fn=None) -> list:
    """[{titleid,name,stem,override[,summary]}] sorted by name. override_fn(titleid)->bool marks
    which games already carry a per-emulator override; optional summary_fn(titleid)->str is the
    media browser's info-panel line ("" == all default)."""
    items = []
    for tid, info in _library().items():
        row = {"titleid": tid, "name": info["name"], "stem": info["stem"],
               "override": bool(override_fn(tid))}
        if summary_fn is not None:
            row["summary"] = summary_fn(tid) or ""
        items.append(row)
    items.sort(key=lambda g: g["name"].lower())
    return items
