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


# ---- launcher / manifest / index ROM expansion -----------------------------------------------------------
# The ES-DE gamelist <path> for some systems is a tiny POINTER (a manifest, a launcher script, a .cue index,
# or a 0-byte marker) whose REAL game bytes live in a sibling folder / referenced track files / a romset in
# the same folder. resolve_rom() returns only that primary pointer (correct for launching + ID lookups);
# resolve_rom_files() returns the COMPLETE backable set (pointer + payload) so a per-game backup captures the
# actual game and the browse list shows its true size. Every payload path is confirmed WITHIN the per-system
# ROM dir (its realpath), so the shipped roms-category rel/containment/restore machinery handles it as-is.

# OpenBOR game-folder subdirs kept OUT of the ROM payload: Logs/ScreenShots are regenerable; Saves is a
# separate "Save" asset (see _openbor_save_group) so the two tick independently.
_OPENBOR_ROM_EXCLUDE = {"logs", "screenshots", "saves"}

# Extensions whose ES-DE <path> is a POINTER (manifest/launcher/index/marker) that resolve_rom_files expands
# to a real payload. The browse list uses this to decide when the cheap getsize is WRONG and a (bounded, few-
# games) expansion is needed - vs a plain file/folder-per-game ROM, which it must NOT walk (timeout risk).
LAUNCHER_EXTS = frozenset({".openbor", ".mugen", ".acgame", ".cue", ".game"})


def _kv_value(path: str, key: str) -> str | None:
    """First `<key>=<value>` (case-insensitive key, '#'-comment lines ignored) in a small manifest/INI, or
    None. Reads the OpenBOR .openbor DIR= and the pcsx2x6 .acgame subdir= launcher pointers."""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    kl = key.lower()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k.strip().lower() == kl:
            return v.strip()
    return None


def _cue_track_files(cue: str) -> list:
    """The existing track files a .cue references (FILE "name" ... lines; name relative to the cue's folder).
    A .cue is a tiny index; its .bin/.wav tracks are the real disc data (segacd etc.)."""
    try:
        text = Path(cue).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    parent = Path(cue).parent
    names = _re.findall(r'FILE\s+"([^"]+)"', text)          # quoted names (the common form)
    names += _re.findall(r'FILE\s+([^"\s]+)\s+\w+', text)    # + a bare unquoted single token
    out = []
    for name in names:
        trk = parent / name
        if trk.exists():
            out.append(trk)
    return out


def _mugen_game_dir(primary: str, stem: str) -> str:
    """The game FOLDER name a .mugen launcher points at (e.g. AvengersVsX-Men.mugen -> "AvX"). The launcher
    is `exec .../mugen.sh <mode> "<path>" [args...]`; the folder is the token after the mode. Falls back to
    the stem (many games' folder == stem). The top-level path component only (a win/native path may be
    subfolder/thing)."""
    import shlex
    try:
        text = Path(primary).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return stem
    for line in text.splitlines():
        if "mugen.sh" not in line:
            continue
        try:
            toks = shlex.split(line, comments=True)
        except ValueError:
            continue
        for i, t in enumerate(toks):
            if t in ("ikemen", "native", "win") and i + 1 < len(toks):
                # top-level path component; an ABSOLUTE arg (leading / or \) would split to "" -> fall back
                # to the stem rather than "" (which would make _rom_payload grab the whole system dir).
                comp = toks[i + 1].replace("\\", "/").split("/", 1)[0]
                return comp or stem
    return stem


def _rom_payload(system: str, ext: str, primary: str, stem: str) -> list:
    """Extra real-game paths beyond the primary pointer, for launcher/manifest/index systems. [] for a plain
    file ROM and for the folder-per-game systems (ps3/daphne/lindbergh), whose primary IS the whole game."""
    parent = Path(primary).parent
    out: list = []
    if ext == ".openbor":                               # manifest DIR=<game folder> (sibling via ~/OpenBor)
        game = parent / (_kv_value(primary, "DIR") or stem)
        try:
            out += [c for c in game.iterdir() if c.name.lower() not in _OPENBOR_ROM_EXCLUDE]
        except OSError:
            pass
    elif ext == ".mugen":                               # bash launcher; game folder = the launcher's path arg
        for cand in (_mugen_game_dir(primary, stem), stem):
            game = parent / cand
            if game.is_dir():
                out.append(game)
                break
        # NB: a <stem>.sh sibling is NOT included - on this library those are dead RetroPie/Wine-era
        # launchers (they point at a ~/.wine prefix that no longer exists); the game runs via ikemen-go.
    elif ext == ".acgame":                              # INI subdir=<game folder> (sibling; namco/pcsx2x6)
        game = parent / (_kv_value(primary, "subdir") or stem)
        if game.is_dir():
            out.append(game)
    elif ext == ".cue":                                 # index; the real data is the referenced track files
        out += _cue_track_files(primary)
    elif ext == ".game":                                # 0-byte marker; payload = the sibling romset (cannonball)
        try:
            out += [c for c in parent.iterdir() if c.suffix.lower() != ".game"]
        except OSError:
            pass
    return out


def resolve_rom_files(system: str, stem: str) -> list:
    """The COMPLETE backable ROM set for one game: [{path, kind}] (abs realpath; kind 'file'|'folder'). = the
    primary pointer/ROM PLUS, for launcher systems, its real game payload. Every path is confirmed WITHIN
    realpath(~/ROMs/<system>); a stray reference is dropped. A plain file/folder ROM -> [the ROM] unchanged."""
    from . import granular_backup as _gb
    roms = resolve_rom(system, stem)
    if not roms:
        return []
    primary = roms[0]
    sysdir = os.path.realpath(str(_system_rom_dir(system)))
    items: list = []
    seen: set = set()

    def _add(path) -> None:
        rp = os.path.realpath(str(path))
        if rp in seen or not os.path.exists(rp) or not _gb._within(rp, sysdir):
            return
        seen.add(rp)
        items.append({"path": rp, "kind": "folder" if os.path.isdir(rp) else "file"})

    _add(primary)
    for extra in _rom_payload(system, Path(primary).suffix.lower(), primary, stem):
        _add(extra)
    return items


def _openbor_save_group(system: str, stem: str, sysdir: str, size_of) -> dict:
    """OpenBOR keeps a game's saves in ~/OpenBor/<G>/Saves/ (INSIDE the ROM tree), so the 'Save' asset uses
    category 'roms' + the per-system anchor -> it restores to the exact live path. One folder-kind row."""
    from . import granular_backup as _gb
    roms = resolve_rom(system, stem)
    files: list = []
    if roms:
        game_dir = Path(roms[0]).parent / (_kv_value(roms[0], "DIR") or stem)
        saves_dir = os.path.realpath(str(game_dir / "Saves"))
        if os.path.isdir(saves_dir) and _gb._within(saves_dir, sysdir):
            # A capped walk can return 0 for a dir it never got to count - that dir is
            # PRESENT (size still counting), not absent (see resolve_game_assets).
            sz = size_of(saves_dir)
            if sz > 0 or saves_dir in getattr(size_of, "partial_srcs", ()):
                rel = os.path.relpath(saves_dir, sysdir)
                files = [{"src": saves_dir, "rel": f"roms/{system}/{rel}",
                          "kind": "folder", "size": sz}]
    return _asset_group("saves", "Saves", "roms", files)


def resolve_boxart(system: str, stem: str) -> dict:
    """{media-kind: absolute path} for a game's ES-DE downloaded media that actually exists (drops the
    None kinds media_for returns). 'covers' is the tile thumbnail; the rest ride along for backup."""
    return {k: v for k, v in es_gamelist.media_for(system, stem).items() if v}


def has_boxart(system: str, stem: str) -> bool:
    """Whether the game has a cover (the tile/thumbnail), cheaply."""
    return bool(es_gamelist.media_for(system, stem, kinds=("covers",)).get("covers"))


def cover_path(system: str, stem: str) -> str | None:
    """The tile art for one game: the box-art cover, else a fallback (miximage / screenshot / titlescreen)
    so games scraped without a cover (e.g. mugen) still show art. Globs ONE kind at a time and stops at the
    first hit, so a normally-covered game (fba = 1800+ games) stays a single glob = no perf regression."""
    for kind in ("covers", "miximages", "screenshots", "titlescreens"):
        hit = es_gamelist.media_for(system, stem, kinds=(kind,)).get(kind)
        if hit:
            return hit
    return None


def resolve_media_kinds(system: str, stem: str) -> list:
    """The PRESENT media kinds for one game (P4 per-kind DRILL): [{key,label,present,size,count}], one per
    media kind that media_for finds on disk, in the stable media_kinds() order. Backs the live game_media
    RPC so the UI can tick individual kinds (box art / marquee / video / ...) under the coarse 'media'."""
    from . import granular_backup as _gb  # lazy: granular_backup imports this module at load
    out = []
    for kind, path in es_gamelist.media_for(system, stem).items():
        if not path:
            continue
        out.append({"key": kind, "label": es_gamelist.media_kind_label(kind),
                    "present": True, "size": _gb._path_size(path), "count": 1})
    return out


# ---- game-first cross-category assets (P2) ---------------------------------
# One game -> the backable assets the game-first UI ticks, grouped by kind. Each group's `files` are the
# concrete LIVE files (src read through any front-door symlink) with rel = "<category>/<path relative to
# that category's root>" - the restore key. Slice-1 saves/states cover RetroArch (raw-corename folder OR
# flat) + mGBA; media reuses the 11-kind resolver. cheats / textures / title-id land in a later slice.

# ES-DE systems mGBA handles as a STANDALONE emulator (saves under ~/Emulation/saves/mgba, flat by stem).
# Probed IN ADDITION to RetroArch so a save made by a standalone-mGBA launch is still found for a system
# whose default launcher is a RA core.
_MGBA_SYSTEMS = {"gb", "gbc", "gba", "sgb", "gbah", "gbch", "gbah2"}


def _asset_group(key: str, label: str, category: str, files: list,
                 detail: str = "") -> dict:
    # detail: a short focused-row note the asset page shows UNDER THE BOX ART (never in
    # the row - rows stay short by design), e.g. "shared with 3 other games".
    files = [f for f in files if f]
    out = {"key": key, "label": label, "category": category,
           "present": bool(files), "size": sum(f.get("size", 0) for f in files),
           "files": files}
    if detail:
        out["detail"] = detail
    return out


# The steamuser homes inside a Proton prefix that hold in-game saves/settings — the
# cheap "Game saves" subset a per-system "All" backs up (the FULL prefix stays per-game).
_STEAM_SAVE_SUBDIRS = ("Documents", "AppData", "Saved Games")


def _lutris_game_assets(appid: int, size_of, heavy: bool = True):
    """Asset groups for a LUTRIS-launched shortcut (category "steam", same tickable
    shape as the Proton branch), or None when this is not a Lutris game with a live
    wine prefix. Saves = the user homes inside the prefix (wine uses the LINUX user,
    so every drive_c/users/<user> except Public is probed); heavy adds the FULL prefix
    (one folder row, rel namespace steam/lutrisprefix/<home-rel> - restore re-derives
    the bound from the LIVE Lutris config) and the external game folder (a repack
    outside the prefix, riding the shared steam/gamedir namespace). A prefix shared by
    several Lutris games says so in its label - backing up / restoring it touches all
    of them (real on this Deck: 4 games share the transformers prefix)."""
    from . import lutris_games, steam_shortcuts as ss
    lid = ss.lutris_game_id(appid)
    if lid is None:
        return None
    pfx = lutris_games.prefix_for(lid)
    if pfx is None:
        return None
    groups: list = []
    h = os.path.realpath(str(ss.home()))
    base = f"steam/lutrisprefix/{os.path.relpath(str(pfx), h)}"

    save_files: list = []
    users_dir = pfx / "drive_c" / "users"
    try:
        users = sorted(u.name for u in users_dir.iterdir()
                       if u.is_dir() and u.name != "Public")
    except OSError:
        users = []
    for user in users:
        for name in _STEAM_SAVE_SUBDIRS:
            p = os.path.join(str(users_dir), user, name)
            if os.path.isdir(p):
                save_files.append({"src": p,
                                   "rel": f"{base}/drive_c/users/{user}/{name}",
                                   "kind": "folder", "size": size_of(p)})
    groups.append(_asset_group("saves", "Saves", "steam", save_files))

    # The game's Lutris config YAML (tiny): it names the prefix/exe, so a fresh-Deck
    # restore has what it needs to re-create the game in Lutris before the prefix
    # restore's live-bound guard can pass. Rel keyed by the config's own file name.
    cfg = lutris_games.config_path(lid)
    if cfg is not None:
        groups.append(_asset_group(
            "lutriscfg", "Lutris config", "steam",
            [{"src": str(cfg), "rel": f"steam/lutriscfg/{cfg.name}",
              "kind": "file", "size": size_of(str(cfg))}],
            detail="names the wine prefix + exe - restore this first on a fresh Deck"))

    if not heavy:
        return groups

    # The shared-prefix fact rides `detail` (shown under the box art when the row is
    # focused), NEVER the label - the long label clipped on-screen (user 2026-07-30).
    shared = lutris_games.shared_prefix_count(lid)
    detail = (f"SHARED with {shared} other game{'s' if shared != 1 else ''} - backing up "
              "or restoring it touches all of them" if shared
              else "the installed game + its wine environment")
    groups.append(_asset_group("prefix", "Wine prefix", "steam",
                               [{"src": str(pfx), "rel": base,
                                 "kind": "folder", "size": size_of(str(pfx))}],
                               detail=detail))

    gd = lutris_games.game_dir_for(lid)
    if gd is not None:
        groups.append(_asset_group(
            "gamedir", "Game Folder", "steam",
            [{"src": str(gd), "rel": f"steam/gamedir/{os.path.relpath(str(gd), h)}",
              "kind": "folder", "size": size_of(str(gd))}]))
    return groups


def _steam_game_assets(stem: str, size_of, heavy: bool = True) -> list:
    """The steam-specific asset groups for one NON-STEAM game (category "steam"): game
    saves (the steamuser folders inside the Proton prefix), the FULL prefix (the
    compatdata/<appid> children, pfx.lock excluded — child-enumeration IS the exclusion
    mechanism, no copy-engine filter), and the external game folder (a repack install
    like ~/games/OutRun2006, from the live shortcut's StartDir). A Lutris-launched
    shortcut has no prefix of its own: a single grey NOTE group explains that only
    launcher + media are backable. A Steam-proper stem returns [] (nothing steam-side —
    those reinstall from Steam). heavy=False (the per-system "All" path, no prefix/
    gamedir key ticked) skips building/sizing the multi-GB groups entirely — the
    _STEAM_HEAVY_KEYS gate, mirror of _EMUCFG_KEYS. A DEAD shortcut's surviving prefix
    still resolves (rescuable); only its game-dir needs the live shortcut."""
    from . import steam_shortcuts as ss
    groups: list = []
    g = ss.nonsteam_games().get(stem)
    if not g:
        return groups
    appid = g["appid"]
    cd_real = os.path.realpath(str(ss.compatdata_dir(appid)))
    prefix_base = f"steam/compatdata/{appid}"
    # LAUNCH TRUTH FIRST: a shortcut that runs through Lutris keeps its LIVE saves in
    # its Lutris wine prefix, even when a compatdata prefix also exists (several games
    # here were tried under Proton before moving to Lutris - that residue must not
    # shadow the live data). The leftover Proton prefix is still offered, clearly
    # labeled, since it can hold pre-move saves. Gated on the live PREFIX, never on
    # the exe name alone; a Lutris game whose prefix vanished degrades to the note.
    lut = _lutris_game_assets(appid, size_of, heavy=heavy)
    if lut is not None:
        groups.extend(lut)
        if heavy and os.path.isdir(cd_real):
            leftover: list = []
            try:
                children = sorted(os.listdir(cd_real))
            except OSError:
                children = []
            for name in children:
                if name == "pfx.lock":
                    continue
                p = os.path.join(cd_real, name)
                leftover.append({"src": p, "rel": f"{prefix_base}/{name}",
                                 "kind": "folder" if os.path.isdir(p) else "file",
                                 "size": size_of(p)})
            groups.append(_asset_group(
                "prefix-proton", "Old Proton prefix", "steam", leftover,
                detail="leftover from before this game moved to Lutris - may hold old saves"))
        return groups
    if not os.path.isdir(cd_real):
        note = ("Runs via Lutris but its wine prefix was not found - only launcher & "
                "media are backed up" if ss.is_lutris(appid)
                else "No Proton prefix - only launcher & media are backed up")
        groups.append(_asset_group("note", note, "steam", []))
        return groups

    # Game saves: one folder row per present steamuser home. Reuses the "saves" KEY so
    # the fixed per-system "All" allowlist (rom+media+saves+states) picks them up.
    save_files: list = []
    for name in _STEAM_SAVE_SUBDIRS:
        p = os.path.join(cd_real, "pfx", "drive_c", "users", "steamuser", name)
        if os.path.isdir(p):
            save_files.append({"src": p,
                               "rel": f"{prefix_base}/pfx/drive_c/users/steamuser/{name}",
                               "kind": "folder", "size": size_of(p)})
    groups.append(_asset_group("saves", "Saves", "steam", save_files))

    if not heavy:
        return groups

    # The full prefix CONTAINS the saves rows above (both stay pre-ticked by design -
    # the prefix is the point of this feature). granular_backup.plan_game_assets drops
    # a nested rel when an ancestor is already planned, so ticking both copies the
    # steamuser dirs ONCE and the size total does not double-count them.
    prefix_files: list = []
    try:
        children = sorted(os.listdir(cd_real))
    except OSError:
        children = []
    for name in children:
        if name == "pfx.lock":                      # live wine lock: never back it up
            continue
        p = os.path.join(cd_real, name)
        prefix_files.append({"src": p, "rel": f"{prefix_base}/{name}",
                             "kind": "folder" if os.path.isdir(p) else "file",
                             "size": size_of(p)})
    groups.append(_asset_group("prefix", "Proton prefix", "steam", prefix_files,
                               detail="the installed game + its wine environment"))

    gd = ss.game_dir(appid)
    if gd is not None:
        h = os.path.realpath(str(ss.home()))
        rel_home = os.path.relpath(str(gd), h)
        groups.append(_asset_group(
            "gamedir", "Game Folder", "steam",
            [{"src": str(gd), "rel": f"steam/gamedir/{rel_home}",
              "kind": "folder", "size": size_of(str(gd))}]))
    return groups


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


def _save_state_files(system: str, stem: str, systems, size_of, corename) -> tuple:
    """(saves, states) live-file lists for one game. RetroArch (raw-corename folder AND the flat dir, when
    the game launches via a RA core) PLUS mGBA (flat, for the GB/GBA family) so a standalone-mGBA save is
    found even when the default launcher is a RA core; deduped by rel. `corename` (the RA core name or None)
    is passed in so resolve_game_assets computes it once. NOTE: the FLAT RetroArch/mGBA dirs are keyed by ROM
    STEM ONLY (RetroArch's layout when save-sorting is off), so a flat save is inherently shared by any
    same-stem game across systems - that reflects how RetroArch stores it, not a mis-attribution."""
    from . import mad_paths  # lazy: avoid import cycles at load
    saves_root = str(mad_paths.saves_root())
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


# ---- P13: per-game emulator-specific assets (category "emucfg") ------------------------------------------
# Textures / per-game settings / console saves / mods / shader cache / cheats that a STANDALONE emulator keys
# by a per-game ID (PCSX2 serial, Dolphin GameID, Switch/Wii U title-id). Reuses each emulator's own ID
# helper. All land in the emucfg category (rel = "emucfg/<path rel to $HOME>", LOGICAL/front-door path) so
# they restore through the shipped emucfg rule-5 path bounded by emu_map's allowlist. Only PRESENT groups are
# returned (an absent emulator-specific asset is simply not shown, unlike the always-shown core ROM/Media/
# Save/Save-state groups).

_PS2_SYSTEMS = {"ps2"}
_GC_WII_SYSTEMS = {"gc", "wii"}
_WIIU_SYSTEMS = {"wiiu"}
_SWITCH_SYSTEMS = {"switch"}


def _emucfg_rel(path: str) -> str | None:
    return _rel_under(path, str(Path.home()), "emucfg")


def _emucfg_group(key: str, label: str, paths: list, size_of) -> dict:
    """An emucfg-category asset group from live paths (files OR dirs). A dir is ONE folder-kind row (not
    enumerated - textures/saves can be huge). Non-existent / outside-$HOME paths drop out, deduped by src."""
    files: list = []
    seen: set = set()
    for p in paths:
        if not p:
            continue
        sp = os.fspath(p)
        if sp in seen or not os.path.exists(sp):
            continue
        seen.add(sp)
        is_dir = os.path.isdir(sp)
        sz = size_of(sp)
        if is_dir and sz == 0 and sp not in getattr(size_of, "partial_srcs", ()):
            # An empty per-title dir (e.g. a stub load/<tid>/) = nothing to back up.
            # A capped-at-0 dir stays: it HAS content the deadline never got to count.
            continue
        rel = _emucfg_rel(sp)
        if not rel:
            continue
        files.append({"src": sp, "rel": rel, "kind": "folder" if is_dir else "file", "size": sz})
    return _asset_group(key, label, "emucfg", files)


def _child_ci(parent: Path, name: str):
    """The child of `parent` whose name matches `name` case-insensitively, or None. Switch per-title dirs
    vary in case across emulators (eden/citron shader lowercase, load/ UPPER, Ryujinx mods mixed)."""
    if not name:
        return None
    low = name.lower()
    try:
        for c in parent.iterdir():
            if c.name.lower() == low:
                return c
    except OSError:
        return None
    return None


def _titleid_for_stem(library: dict, stem: str) -> str | None:
    """The title-id whose library entry carries this ROM stem (invert a {tid: {'stem':...}} map)."""
    s = (stem or "").lower()
    if not s:
        return None
    for tid, info in library.items():
        if (info.get("stem") or "").lower() == s:
            return tid
    return None


def _pcsx2_assets(home: Path, system: str, stem: str, size_of) -> list:
    from .madsrv import pcsx2_games
    roms = resolve_rom(system, stem)
    if not roms:
        return []
    key = pcsx2_games.path_to_key(roms[0])          # "<SERIAL>_<CRC>"
    if not key:
        return []
    serial = key.split("_", 1)[0]
    crc = key.split("_", 1)[1] if "_" in key else ""
    cfg = home / ".config/PCSX2"
    cheats = [cfg / "cheats" / f"{key}.pnach"]
    if crc:
        cheats.append(cfg / "cheats" / f"{crc}.pnach")
    # PS2 console save: a FOLDER memory card keeps each game's save in a subdir named by serial (e.g.
    # BASLUS-21273...); a FILE memory card (Mcd00x.ps2) is SHARED across all games -> wholesale only. Glob
    # the serial across every folder memcard.
    saves_root = home / "Emulation/saves/pcsx2/saves"
    ps2_saves: list = []
    try:
        for mc in saves_root.iterdir():
            if not mc.is_dir():                     # a folder memcard is a dir; a file memcard is a .ps2 file
                continue
            for sub in mc.iterdir():
                if sub.is_dir() and serial in sub.name:
                    ps2_saves.append(sub)
    except OSError:
        pass
    return [
        _emucfg_group("settings", "Game settings", [cfg / "gamesettings" / f"{key}.ini"], size_of),
        _emucfg_group("console-save", "Console save", ps2_saves, size_of),
        _emucfg_group("textures", "Textures",
                      [home / "Emulation/storage/pcsx2/textures" / serial], size_of),
        _emucfg_group("cheats", "Cheats", cheats, size_of),
    ]


def _dolphin_assets(home: Path, system: str, stem: str, size_of) -> list:
    from . import dolphin_gameids
    roms = resolve_rom(system, stem)
    if not roms:
        return []
    # Resolve the GameID cache-first, then a BOUNDED dolphin-tool shell-out (8s cap) on a miss - so a game
    # whose GameID isn't cached (or whose ROM realpath differs from the cache key, e.g. an SD-card symlink)
    # still gets its per-game rows, without risking the 40s hang against the 12s asset-list RPC budget. The
    # result is cached (by this realpath) so the next open is instant. Dolphin CHEATS ride the GameSettings ini.
    gid = dolphin_gameids.gameid(roms[0], timeout=8)   # 6-char GameID (cache hit is instant)
    if not gid:
        return []
    data = home / ".var/app/org.DolphinEmu.dolphin-emu/data/dolphin-emu"
    tex = data / "Load/Textures"
    console: list = []
    if system == "wii":
        # Wii NAND save: Wii/title/00010000/<title-lo>/data/, where <title-lo> = the ASCII-hex of the 4-char
        # game code (GameID[:4], e.g. R7PP -> 52375050). Verified on device.
        try:
            tlo = gid[:4].encode("ascii", "ignore").hex()
            nand = data / "Wii/title/00010000" / tlo / "data"
            if nand.is_dir():
                console = [nand]
        except (OSError, UnicodeError):
            console = []
    else:
        # GameCube GCI-FOLDER save: each save is a .gci carrying the 4-char game code under GC/<region>/Card ?/.
        # RAW memcards (MemoryCard*.raw) are shared -> wholesale only. Match the game code across region/card dirs.
        code = gid[:4]
        gcroot = data / "GC"
        if gcroot.is_dir():
            try:
                console = [str(p) for p in gcroot.rglob(f"*{code}*.gci") if p.is_file()]
            except OSError:
                console = []
    return [
        _emucfg_group("settings", "Game settings", [data / "GameSettings" / f"{gid}.ini"], size_of),
        _emucfg_group("console-save", "Console save", console, size_of),
        # Dolphin matches textures by 6-char OR 3-char GameID prefix; include whichever exist. The 3-char dir
        # is REGION-SHARED across sibling games (e.g. GSW covers all Star Wars Rogue Squadron regions), so
        # it's included for completeness (a game's pack often lives there) and restore's rule-5 snapshots it.
        _emucfg_group("textures", "Textures", [tex / gid, tex / gid[:3]], size_of),
    ]


def _cemu_assets(home: Path, system: str, stem: str, size_of) -> list:
    from .madsrv import cemu_games
    tid = _titleid_for_stem(cemu_games._library(), stem)
    if not tid or len(tid) != 16:
        return []
    hi8, lo8 = tid[:8], tid[8:16]
    sc = home / ".cache/Cemu/shaderCache"        # Cemu keeps shader/pipeline caches under ~/.cache, not .local
    shaders = [sc / sub / f"{tid}.bin" for sub in ("transferable", "precompiled")]
    shaders += [sc / sub / f"{tid.upper()}.bin" for sub in ("transferable", "precompiled")]
    return [
        _emucfg_group("console-save", "Console save",
                      [home / "Emulation/saves/Cemu/saves" / hi8 / lo8], size_of),
        _emucfg_group("graphicpacks", "Graphic packs", _cemu_packs_for_title(home, tid), size_of),
        _emucfg_group("shader", "Shader cache", shaders, size_of),
    ]


def _cemu_packs_for_title(home: Path, tid: str) -> list:
    """The Cemu graphic-pack DIRS whose rules.txt targets this title-id (packs are keyed by titleIds INSIDE
    rules.txt, not a per-title folder). Scans graphicPacks/**/rules.txt. A pack that targets several games is
    SHARED, so restoring one game's packs rule-5-snapshots the shared dir first (like Dolphin 3-char textures)."""
    low = (tid or "").lower()
    if not low:
        return []
    out: list = []
    gp = home / ".local/share/Cemu/graphicPacks"
    try:
        for rules in gp.rglob("rules.txt"):
            if rules.parent == gp:
                continue          # a root-level rules.txt is the whole tree, not a single game's pack unit
            try:
                txt = rules.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            m = _re.search(r"titleIds\s*=\s*(.+)", txt)
            if m and any(t.strip().lower() == low for t in _re.split(r"[,\s]+", m.group(1)) if t.strip()):
                out.append(rules.parent)
    except OSError:
        pass
    return out


def _switch_assets(home: Path, system: str, stem: str, size_of) -> list:
    from .madsrv import switch_games
    m = switch_games._TITLEID_RE.search(stem or "")
    tid = m.group(1).lower() if m else _titleid_for_stem(switch_games._library(), stem)
    if not tid or len(tid) != 16:
        return []
    saves: list = []
    mods: list = []
    shaders: list = []
    for emu in ("eden", "citron"):                  # probe every present Switch emulator; union per title-id
        base = home / ".local/share" / emu
        nand = base / "nand/user/save/0000000000000000"
        if nand.is_dir():
            try:
                for acct in nand.iterdir():         # <account-id> is not derivable from the title-id -> glob
                    if not acct.is_dir():
                        continue
                    sd = _child_ci(acct, tid)
                    if sd is not None and sd.is_dir():
                        saves.append(sd)
            except OSError:
                pass
        md = _child_ci(base / "load", tid)
        if md is not None and md.is_dir():
            mods.append(md)
        sh = _child_ci(base / "shader", tid)
        if sh is not None and sh.is_dir():
            shaders.append(sh)
    from . import ryujinx_saves
    saves += ryujinx_saves.save_paths(tid)          # opaque SaveDataId -> title-id via ExtraData0
    rmod = _child_ci(home / ".config/Ryujinx/mods/contents", tid)
    if rmod is not None and rmod.is_dir():
        mods.append(rmod)
    rgame = _child_ci(home / ".config/Ryujinx/games", tid)   # per-game shader/PPTC cache (parity w/ eden/citron)
    if rgame is not None and (rgame / "cache").is_dir():
        shaders.append(rgame / "cache")
    return [
        _emucfg_group("console-save", "Console save", saves, size_of),
        _emucfg_group("mods", "Mods & DLC", mods, size_of),
        _emucfg_group("shader", "Shader cache", shaders, size_of),
    ]


def _ra_cheats_assets(home: Path, system: str, stem: str, corename, size_of) -> list:
    """RetroArch per-game cheats (<stem>.cht). Only for a game that launches via a RA core (`corename` is the
    core name, or None for a standalone launch). The default cheats dir is the SIBLING of the per-core
    override tree: RA_CONFIG_BASE ends in .../retroarch/config, so cheats live at .../retroarch/cheats
    (RA_CONFIG_BASE.parent), exactly like saves/states/system. RA's runtime "Save Game Cheats" folders by the
    core display name; we probe both <cheats>/<CoreName>/<stem>.cht and the flat <cheats>/<stem>.cht.
    Best-effort: empty on a device whose cheat_database_path is redirected to the read-only /app DB."""
    if not corename:
        return []
    from . import retroarch_cfg
    cheats_root = retroarch_cfg.RA_CONFIG_BASE.parent / "cheats"
    cht: list = []
    for d in (cheats_root / corename, cheats_root):
        cht += [str(p) for p in _glob_stem(d, stem) if p.suffix.lower() == ".cht"]
    return [_emucfg_group("cheats", "Cheats", cht, size_of)]


def _emucfg_game_assets(system: str, stem: str, corename, size_of) -> list:
    """Per-game emulator-specific assets for one game, PRESENT-ONLY (an absent emulator asset is not shown).
    Groups that share a key (e.g. a PS2 game launched via a RA core yields both pnach and .cht "cheats") are
    MERGED into one group so the UI never shows a duplicate row and the manifest carries one asset kind.
    `corename` is the game's RA core name (or None) - passed so _ra_cheats_assets doesn't recompute it."""
    home = Path.home()
    cands: list = []
    if system in _PS2_SYSTEMS:
        cands += _pcsx2_assets(home, system, stem, size_of)
    elif system in _GC_WII_SYSTEMS:
        cands += _dolphin_assets(home, system, stem, size_of)
    elif system in _WIIU_SYSTEMS:
        cands += _cemu_assets(home, system, stem, size_of)
    elif system in _SWITCH_SYSTEMS:
        cands += _switch_assets(home, system, stem, size_of)
    cands += _ra_cheats_assets(home, system, stem, corename, size_of)
    merged: dict = {}
    order: list = []
    for grp in cands:
        if not grp["present"]:
            continue
        k = grp["key"]
        if k not in merged:
            merged[k] = grp
            order.append(k)
        else:                                 # collapse same-key groups (union their files, sum sizes)
            m = merged[k]
            m["files"] = m["files"] + grp["files"]
            m["size"] += grp["size"]
    return [merged[k] for k in order]


def resolve_game_assets(system: str, stem: str, systems=None, emucfg: bool = True,
                        steam_heavy: bool = True, deadline=None) -> list:
    """One game's backable assets, grouped by the tickable kind the game-first UI shows:
      [{key, label, category, present, size, files:[{src, rel, kind}]}]
    key/label: what the UI ticks; category: the manifest/rel namespace; files: the concrete live files.
    Groups (slice-1): ROM, Media (all 11 kinds), Save, Save state. Read-only; a group with no files comes
    back present=false so the UI can grey it.
    deadline (time.monotonic value, None = uncapped): sizing stops once passed, so a
    huge wine prefix can never make the asset page's RPC time out; a group whose walk
    was cut short comes back with size_partial=True and a "size still counting" note."""
    from . import granular_backup as _gb  # lazy: granular_backup imports this module at load
    partial_srcs: set = set()
    if deadline is None:
        size_of = _gb._path_size
    else:
        def size_of(path):
            size, complete = _gb._path_size_capped(path, deadline)
            if not complete:
                partial_srcs.add(path)
            return size
        # Presence gates (an all-zero dir is normally absent) must NOT drop a dir the
        # capped walk never finished counting - they check this attribute.
        size_of.partial_srcs = partial_srcs
    groups = []

    # ROM - the COMPLETE backable ROM set. resolve_rom_files expands a launcher/manifest/index pointer to
    # its real game payload (sibling folder / .cue tracks / romset), and confines every path to the
    # per-system dir; a plain file/folder ROM is just itself.
    rom_files: list = []
    sysdir = os.path.realpath(str(_system_rom_dir(system)))
    for it in resolve_rom_files(system, stem):
        src = it["path"]
        rel_rom = os.path.relpath(src, sysdir)
        rom_files.append({"src": src, "rel": f"roms/{system}/{rel_rom}",
                          "kind": it["kind"], "size": size_of(src)})
    groups.append(_asset_group("rom", "ROM", "roms", rom_files))

    # OpenBOR per-game SAVE: lives inside the ROM tree (~/OpenBor/<G>/Saves/), so it rides the roms category
    # + per-system anchor and is kept OUT of the ROM group above (they tick separately).
    if system == "openbor":
        groups.append(_openbor_save_group(system, stem, sysdir, size_of))

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

    # SAVE + SAVE STATE (RetroArch + mGBA). These are shown ONLY for a game that launches via a RA core, or an
    # mGBA-family system - i.e. where the game's actual save IS a RetroArch-style stem-keyed save. For a
    # STANDALONE-emulator system (ps2/gc/wii/wiiu/switch) the game's save is the emulator's console save
    # (surfaced below as "Console save"), so these RA-only rows are omitted instead of showing empty "none".
    from . import retroarch_cfg
    try:
        corename = retroarch_cfg.save_corename(system, stem, systems)
    except Exception:
        corename = None
    saves, states = _save_state_files(system, stem, systems, size_of, corename)
    if corename is not None or system in _MGBA_SYSTEMS:
        groups.append(_asset_group("saves", "Saves", "saves", saves))
        groups.append(_asset_group("states", "Save states", "states", states))

    # P13: per-game emulator-specific assets (textures / console saves / mods / shader / graphic packs /
    # cheats), category "emucfg". Skipped when the caller wants only the core keys (the P9 whole-system "All"
    # path). Wrapped so a per-game ID/resolver hiccup can never break the core ROM/Media/Save asset list.
    if emucfg:
        try:
            groups.extend(_emucfg_game_assets(system, stem, corename, size_of))
        except Exception:
            pass

    # steam (non-Steam shortcuts): the launcher rides the generic ROM group above
    # (relabelled — it IS what the gamelist entry launches), media rides the generic
    # media group; the Proton-prefix / game-dir groups are steam-specific (category
    # "steam"). Wrapped like emucfg so a Steam-side hiccup can't break rom/media.
    if system == "steam":
        for grp in groups:
            if grp["key"] == "rom":
                grp["label"] = "Launcher"
        try:
            groups.extend(_steam_game_assets(stem, size_of, heavy=steam_heavy))
        except Exception:
            pass

    if partial_srcs:
        for g in groups:
            if any(f.get("src") in partial_srcs for f in g["files"]):
                g["size_partial"] = True
                note = "size still counting"
                g["detail"] = f"{g['detail']}. {note}" if g.get("detail") else note

    return groups
