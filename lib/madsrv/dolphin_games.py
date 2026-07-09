"""dolphinpg_gc.games / dolphinpg_wii.games -- the per-game browser lists for GameCube and Wii.

Lists each ES-DE system's ROMs, resolves each to its Dolphin 6-char GameID (lib.dolphin_gameids, via
dolphin-tool + cache), and returns the per-game picker payload the fork browser consumes:
`{titleid, name, stem, override, summary, hide}`. `titleid` = the GameID (the identity every per-game
settings/codes page keys off). `override` = a user `GameSettings/<ID>.ini` exists. `hide` omits the
`dolphin_ar` / `dolphin_gecko` leaf for a game that has no such codes (built-in DB + user), giving the
requested dynamic code sections. Media/name come from ES-DE (by rom stem). Two systems -> two
namespaces (separate GameCube + Wii lists), both driving the SAME per-game settings/codes backends
(which key off the GameID, not the system).
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from .. import dolphin_gameids as gids
from .. import es_gamelist, esde_settings
from . import cfgutil
from . import dolphin_codes_cmds as codes
from .rpc import method

_ROMDIR_RE = re.compile(r'name="ROMDirectory"\s+value="([^"]*)"')
# A real override line: a `key = value` OR a `$Code Name` (enabled/disabled code) -- NOT a bare
# section header, blank, or comment. So an emptied override file (all Inherit) no longer badges,
# while a file customised by codes alone still does.
_CONTENT_RE = re.compile(r'(?m)^[ \t]*(\$|[^\[\s;#])')


def _has_override(gid: str) -> bool:
    t = cfgutil.read_text(gids.user_ini(gid))
    return bool(t and _CONTENT_RE.search(t))


def _rom_root() -> Path:
    """ES-DE's ROM directory (the ROMDirectory setting; empty -> ES-DE's ~/ROMs default)."""
    try:
        m = _ROMDIR_RE.search(esde_settings.SETTINGS.read_text(encoding="utf-8", errors="replace"))
        raw = (m.group(1).strip() if m else "")
    except OSError:
        raw = ""
    if raw:
        return Path(os.path.expanduser(os.path.expandvars(raw)))
    return Path.home() / "ROMs"


def _roms(system: str) -> list[Path]:
    # Recursive (rglob): ES-DE scans ROM subfolders (per-game / multi-disc dirs) and the C++ browser
    # resolves media with getFilesRecursive, so the game list must match.
    d = _rom_root() / system
    try:
        return sorted(p for p in d.rglob("*")
                      if p.is_file() and p.suffix.lower() in gids.EXTS)
    except OSError:
        return []


def _listing(system: str) -> dict:
    roms = _roms(system)
    names = es_gamelist.titles(system)                    # {stem.lower(): name}
    resolved = gids.gameids(roms)                         # {abspath: gameid|None}
    games, seen = [], set()
    for p in roms:
        gid = resolved.get(str(p))
        if not gid or gid in seen:                        # unresolvable disc, or a duplicate GameID
            continue
        seen.add(gid)
        stem = p.stem
        override = _has_override(gid)
        row = {"titleid": gid, "name": names.get(stem.lower()) or stem, "stem": stem,
               "override": override, "summary": "Custom settings" if override else ""}
        hide = [ns for ns, sec in (("dolphin_ar", "ActionReplay"), ("dolphin_gecko", "Gecko"))
                if not codes.has_codes(gid, sec)]
        if hide:
            row["hide"] = hide
        games.append(row)
    games.sort(key=lambda g: g["name"].lower())
    note = ("" if games else
            f"No {system.upper()} games found. Add ROMs to your '{system}' folder and scrape them "
            "in ES-DE, then reopen this page.")
    return {"games": games, "system": system, "note": note}


@method("dolphinpg_gc.games", slow=True)
def _gc_games(params):
    return _listing("gc")


@method("dolphinpg_wii.games", slow=True)
def _wii_games(params):
    return _listing("wii")
