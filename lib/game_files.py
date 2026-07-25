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


# ---- game-first cross-category assets (P2) ---------------------------------
# One game -> the backable assets the game-first UI ticks, grouped by kind. Each group's `files` are the
# concrete LIVE files (src read through any front-door symlink) with rel = "<category>/<path relative to
# that category's root>" - the restore key. Slice-1 saves/states cover RetroArch (raw-corename folder OR
# flat) + mGBA; media reuses the 11-kind resolver. cheats / textures / title-id land in a later slice.

# ES-DE systems mGBA handles as a STANDALONE emulator (saves under ~/Emulation/saves/mgba, flat by stem).
# Probed IN ADDITION to RetroArch so a save made by a standalone-mGBA launch is still found for a system
# whose default launcher is a RA core.
_MGBA_SYSTEMS = {"gb", "gbc", "gba", "sgb", "gbah", "gbch", "gbah2"}


def _asset_group(key: str, label: str, category: str, files: list) -> dict:
    files = [f for f in files if f]
    return {"key": key, "label": label, "category": category,
            "present": bool(files), "size": sum(f.get("size", 0) for f in files),
            "files": files}


def _glob_stem(dirpath: Path, stem: str) -> list:
    """Files named exactly <stem>.<anything> directly in dirpath (glob-escaped so brackets/parentheses in
    a ROM name are literal). Returns [] for a missing/unreadable dir. Follows a symlinked dir."""
    try:
        return sorted(p for p in dirpath.glob(_glob.escape(stem) + ".*") if p.is_file())
    except OSError:
        return []


def _rel_under(path: str, root: str, category: str) -> str | None:
    """rel = '<category>/<path relative to root>' using the LOGICAL path (root's front-door symlinks are
    KEPT, never realpath'd, so the restore side rebuilds the same live path). None if path is not under
    root (a '..' escape - never backed up)."""
    rel_after = os.path.relpath(path, root)
    if rel_after == os.curdir or rel_after.startswith(os.pardir + os.sep) or rel_after == os.pardir:
        return None
    return f"{category}/{rel_after}"


def _save_state_files(system: str, stem: str, systems, size_of) -> tuple:
    """(saves, states) live-file lists for one game. RetroArch (raw-corename folder AND the flat dir, when
    the game launches via a RA core) PLUS mGBA (flat, for the GB/GBA family) so a standalone-mGBA save is
    found even when the default launcher is a RA core; deduped by rel. NOTE: the FLAT RetroArch/mGBA dirs
    are keyed by ROM STEM ONLY (RetroArch's layout when save-sorting is off), so a flat save is inherently
    shared by any same-stem game across systems - that reflects how RetroArch stores it, not a
    mis-attribution. (A later slice can gate the flat probe on the RA sort-saves setting.)"""
    from . import es_systems, mad_paths, retroarch_cfg  # lazy: avoid import cycles at load
    saves_root = str(mad_paths.saves_root())
    corename = retroarch_cfg.save_corename(system, stem, systems)
    saves: list = []
    states: list = []

    def _collect(front: Path, category: str, out: list, corefolder: str | None):
        seen = {f["rel"] for f in out}
        dirs = ([front / corefolder] if corefolder else []) + [front]  # foldered first, then flat
        for d in dirs:
            for p in _glob_stem(d, stem):
                rel = _rel_under(str(p), saves_root, category)
                if not rel or rel in seen:
                    continue
                seen.add(rel)
                out.append({"src": str(p), "rel": rel, "kind": "file", "size": size_of(str(p))})

    if corename is not None:  # a RetroArch launch
        _collect(Path(saves_root) / "retroarch" / "saves", "saves", saves, corename)
        _collect(Path(saves_root) / "retroarch" / "states", "states", states, corename)
    if system in _MGBA_SYSTEMS:  # standalone mGBA (flat), in addition
        _collect(Path(saves_root) / "mgba" / "saves", "saves", saves, None)
        _collect(Path(saves_root) / "mgba" / "states", "states", states, None)
    return saves, states


def resolve_game_assets(system: str, stem: str, systems=None) -> list:
    """One game's backable assets, grouped by the tickable kind the game-first UI shows:
      [{key, label, category, present, size, files:[{src, rel, kind}]}]
    key/label: what the UI ticks; category: the manifest/rel namespace; files: the concrete live files.
    Groups (slice-1): ROM, Media (all 11 kinds), Save, Save state. Read-only; a group with no files comes
    back present=false so the UI can grey it."""
    from . import granular_backup as _gb  # lazy: granular_backup imports this module at load
    size_of = _gb._path_size
    groups = []

    # ROM - only a plain ROM under its own system dir (emulator data outside it isn't a plain ROM)
    rom_files: list = []
    paths = resolve_rom(system, stem)
    if paths:
        src = os.path.realpath(paths[0])
        sysdir = os.path.realpath(str(_system_rom_dir(system)))
        if _gb._within(src, sysdir):
            rel_rom = os.path.relpath(src, sysdir)
            rom_files = [{"src": src, "rel": f"roms/{system}/{rel_rom}",
                          "kind": "folder" if os.path.isdir(src) else "file", "size": size_of(src)}]
    groups.append(_asset_group("rom", "ROM", "roms", rom_files))

    # MEDIA - every downloaded-media kind that exists, as one tickable group
    from . import esde_settings
    media_root = str(esde_settings.media_root())
    media_files: list = []
    for _kind, path in es_gamelist.media_for(system, stem).items():
        if not path:
            continue
        rel = _rel_under(path, media_root, "media")
        if rel:
            media_files.append({"src": path, "rel": rel, "kind": "file", "size": size_of(path)})
    groups.append(_asset_group("media", "Media", "media", media_files))

    # SAVE + SAVE STATE (slice-1: RetroArch + mGBA)
    saves, states = _save_state_files(system, stem, systems, size_of)
    groups.append(_asset_group("saves", "Save", "saves", saves))
    groups.append(_asset_group("states", "Save state", "states", states))

    return groups
