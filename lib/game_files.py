"""Resolve a single game's on-disk files for the granular Backup & Restore manager.

Pilot scope (ROMs + games): the ROM file/folder and its ES-DE box art. Add-ons (per-game texture
packs / mods / cheats) and the other backup categories layer on top later. Read-only; every returned
path is an ABSOLUTE realpath that exists on disk.

Reuses: es_gamelist.rom_paths (raw gamelist <path>), es_collections.rom_root (the ES-DE ROM dir),
es_gamelist.media_for (per-game downloaded media). Nothing here parses ROM internals.

KNOWN follow-ups (documented, not yet handled here):
  - A .desktop launcher entry (ps3) resolves best-effort to its referenced game FOLDER; if that can't be
    found we fall back to the .desktop file itself (a later pass will parse the Exec target precisely).
  - The nested per-system ROM symlink (~/ROMs/ps2 -> ~/Emulation/roms/ps2) is followed to the REAL path
    here (so backup reads the true file); recreating that symlink on restore to a fresh device is the
    restore layer's job (mirror deck-cloud.sh's @<relpath> symlink manifest).
"""
from __future__ import annotations

import glob as _glob
import os
import re as _re
from pathlib import Path

from . import es_collections, es_gamelist


def _system_rom_dir(system: str) -> Path:
    """The ES-DE ROM directory for one system (follows the ~/ROMs -> SD/internal symlink)."""
    return es_collections.rom_root() / system


def _desktop_target(desktop: Path) -> Path | None:
    """Best-effort: the game FOLDER a .desktop launcher points at (ps3-style, e.g. Exec=... rpcs3
    "/.../<Game> [TITLEID]/PS3_GAME/USRDIR/EBOOT.BIN"). The referenced path is QUOTED and contains
    spaces, so extract quoted absolute paths, then walk up to the direct child of the ROM dir. Compares
    REALPATHS so the ~/ROMs/<sys> -> internal symlink doesn't defeat the match. None if not found."""
    try:
        text = desktop.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    romreal = os.path.realpath(desktop.parent)
    # quoted absolute paths (may contain spaces) first, then any bare absolute token as a fallback
    cands = _re.findall(r'"(/[^"]+)"', text) + _re.findall(r'(?<!")(/\S+)', text)
    for tok in cands:
        p = Path(tok)
        # PSN/installed title (rpcs3): .../dev_hdd0/game/<TITLEID>/USRDIR/EBOOT.BIN -> the <TITLEID>
        # folder (the installed game data - the disc-game equivalent), which lives OUTSIDE the ROM dir.
        parts = p.parts
        if "dev_hdd0" in parts:
            i = parts.index("dev_hdd0")
            if i + 2 < len(parts) and parts[i + 1] == "game":
                cont = Path(*parts[: i + 3])
                if cont.is_dir():
                    return cont
        while p != p.parent:
            # the game is the direct child of the ROM dir the launcher path descends from - a FOLDER
            # (extracted PS3_GAME) or a FILE (a .iso). Skip a token that never passes through the ROM
            # dir (e.g. the emulator AppImage path).
            if os.path.realpath(p.parent) == romreal and p.exists():
                return p
            p = p.parent
    return None


def resolve_rom(system: str, stem: str) -> list[str]:
    """Absolute realpath(s) of a game's ROM. Single-file ROM -> [the file]. Folder-per-game -> [the
    folder]. .desktop launcher -> [the referenced game folder] (best-effort) else [the .desktop].
    Empty list when nothing is found."""
    raw = es_gamelist.rom_paths(system).get((stem or "").lower())
    romdir = _system_rom_dir(system)
    cand: Path | None = None
    if raw:
        cand = Path(raw) if os.path.isabs(raw) else (romdir / raw)
    if cand is None or not cand.exists():
        cand = None
        try:                                     # fallback: a real ROM file named exactly <stem>.<ext>
            for p in sorted(romdir.glob(_glob.escape(stem) + ".*")):
                if p.name == stem + p.suffix and p.suffix.lower() != ".desktop" and p.is_file():
                    cand = p
                    break
        except OSError:
            pass
    if cand is None or not cand.exists():
        return []
    if cand.suffix.lower() == ".desktop":
        folder = _desktop_target(cand)
        return [os.path.realpath(folder if folder else cand)]
    return [os.path.realpath(cand)]


def resolve_boxart(system: str, stem: str) -> dict:
    """{media-kind: absolute path} for a game's ES-DE downloaded media that actually exists (drops the
    None kinds media_for returns). 'covers' is the tile thumbnail; the rest ride along for backup."""
    return {k: v for k, v in es_gamelist.media_for(system, stem).items() if v}


def has_boxart(system: str, stem: str) -> bool:
    """Whether the game has a cover (the tile/thumbnail), cheaply."""
    return bool(es_gamelist.media_for(system, stem).get("covers"))
