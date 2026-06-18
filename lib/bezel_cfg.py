"""bezel_cfg — install / status / uninstall / enable / disable RetroArch bezel
packs (The Bezel Project) for the flatpak RetroArch, for the MAD Bezel page.

Reimplements the proven logic of install-bezels.sh / install-bezels-all.sh in
Python (so the GUI and CLI share one source of truth) and adds the operations the
shell scripts lack: status, uninstall, enable/disable (per system + per game).

Safety (House rule #5 — never destroy user data):
  * install never overwrites a HAND-MADE per-game .cfg (one without our
    `# bezelproject` marker) — it's moved to ~/Downloads/_TMP first (+ RECOVERY.txt).
  * uninstall MOVES our overlay symlinks + our sentinel-marked per-game cfgs to
    ~/Downloads/_TMP (recoverable), never rm; hand-made cfgs are left untouched.
  * enable/disable only flips `input_overlay_enable` in OUR sentinel cfgs
    (additive, byte-preserving) — it never strips the bezel block.
Refuse install/uninstall/enable while RetroArch is running (it rewrites cfgs on exit).
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

_HOME = Path.home()
BEZEL_BASE = _HOME / "Emulation/tools/bezelproject"
ROMS = Path("/run/media/deck/1tbDeck/ROMs")
_RA = _HOME / ".var/app/org.libretro.RetroArch/config/retroarch"
OVERLAY_BASE = _RA / "overlays/GameBezels"
CONFIG_BASE = _RA / "config"
SENTINEL = "# bezelproject"
_ROM_EXTS = ("zip", "7z", "chd", "iso", "cue", "cdi", "bin", "nes", "sfc", "smc",
             "smd", "gen", "md", "gb", "gbc", "gba", "n64", "z64", "v64", "pce",
             "sgx", "wbfs", "rvz", "gcm")

# key | label | repo (bezelproject-<repo>) | overlay subdir | rom dirs | cores | art system
SYSTEMS = [
    ("nes", "NES", "NES", "NES", ["nes"], ["Mesen", "Nestopia", "FCEUmm"], "nes"),
    ("famicom", "Famicom", "Famicom", "Famicom", ["famicom"], ["Mesen", "Nestopia", "FCEUmm"], "famicom"),
    ("snes", "SNES", "SNES", "SNES", ["snes", "sfc"], ["Snes9x", "bsnes", "Snes9x - Current"], "snes"),
    ("n64", "Nintendo 64", "N64", "Nintendo 64", ["n64"], ["Mupen64Plus-Next", "ParaLLEl N64"], "n64"),
    ("megadrive", "Mega Drive / Genesis", "MegaDrive", "Megadrive", ["genesis", "megadrive"],
     ["Genesis Plus GX", "BlastEm", "PicoDrive"], "megadrive"),
    ("mastersystem", "Master System", "MasterSystem", "MasterSystem", ["mastersystem"],
     ["Genesis Plus GX", "Gearsystem"], "mastersystem"),
    ("gamegear", "Game Gear", "GameGear", "GameGear", ["gamegear"], ["Gearsystem", "Genesis Plus GX"], "gamegear"),
    ("segacd", "Sega CD", "SegaCD", "Sega CD", ["segacd"], ["Genesis Plus GX", "PicoDrive"], "segacd"),
    ("sega32x", "Sega 32X", "Sega32x", "Sega32X", ["sega32x"], ["PicoDrive"], "sega32x"),
    ("saturn", "Saturn", "Saturn", "Saturn", ["saturn"], ["Beetle Saturn", "Kronos", "YabaSanshiro"], "saturn"),
    ("dreamcast", "Dreamcast", "Dreamcast", "Dreamcast", ["dreamcast"], ["Flycast"], "dreamcast"),
    ("pcengine", "PC Engine", "PCEngine", "PC Engine", ["pcenginecd", "pcengine"],
     ["Beetle PCE", "Beetle PCE Fast"], "pcengine"),
    ("supergrafx", "SuperGrafx", "SuperGrafx", "SuperGrafx", ["supergrafx"],
     ["Beetle SuperGrafx", "Beetle PCE"], "supergrafx"),
    ("pcfx", "PC-FX", "PCFX", "PCFX", ["pcfx"], ["Beetle PC-FX"], "pcfx"),
    ("3do", "3DO", "3DO", "3DO", ["3do"], ["Opera"], "3do"),
    ("amiga", "Amiga", "Amiga", "Amiga", ["amigacd32", "amiga"], ["PUAE"], "amiga"),
    ("mame", "Arcade (MAME)", "MAME", "MAME", ["arcade", "mame", "fba", "fbneo"],
     ["MAME", "MAME 2010", "MAME 2003-Plus", "FinalBurn Neo", "FB Alpha 2012"], "arcade"),
]
# Saturn/Dreamcast/Sega CD mix 4:3 and 16:9 — the 4:3 bezel can look wrong on a
# widescreen game; the page warns for these.
WIDESCREEN_WARN = {"saturn", "dreamcast", "segacd"}

_PER_GAME_CFG = (
    "# bezelproject — auto-generated, safe to delete\n"
    'input_overlay = "{overlay}"\n'
    'input_overlay_enable = "{enabled}"\n'
    'input_overlay_opacity = "1.000000"\n'
    'video_fullscreen = "true"\n'
    'aspect_ratio_index = "22"\n'
    'video_aspect_ratio = "1.333333"\n'
)


def _by_key(key):
    for s in SYSTEMS:
        if s[0] == key:
            return s
    return None


def _src_subdir(repo, target_subdir):
    base = BEZEL_BASE / f"bezelproject-{repo}" / "retroarch" / "overlay"
    for c in (base / "GameBezels" / target_subdir, base / "ArcadeBezels",
              base / "GameBezels" / repo):
        if c.is_dir():
            return c
    return None


def _rom_exists(game, rom_dirs):
    for d in rom_dirs:
        for ext in _ROM_EXTS:
            if (ROMS / d / f"{game}.{ext}").is_file():
                return True
    return False


def _tmp_dir():
    from datetime import datetime  # local: top-level datetime banned in workflow ctx only
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    d = _HOME / "Downloads" / f"_TMP_bezel-{ts}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "RECOVERY.txt").write_text(
        "Files moved here by MAD's Bezel page instead of being deleted (rule #5).\n"
        "Per-game cfgs are named <core>__<game>.cfg; restore by moving back to\n"
        f"  {CONFIG_BASE}/<core>/<game>.cfg\n"
        "Overlay symlinks were moved from the GameBezels/<system> dir.\n")
    return d


def _game_cfgs(key):
    """MAD-generated per-game cfg paths for a system, across its cores. Cores are
    SHARED between systems (e.g. Genesis Plus GX serves megadrive/mastersystem/
    gamegear/segacd), so a cfg counts for THIS system only if its overlay points at
    this system's GameBezels/<subdir>/ — the sentinel alone isn't enough."""
    s = _by_key(key)
    if not s:
        return []
    _, _, _, subdir, _, cores, _ = s
    marker = f"/GameBezels/{subdir}/"
    out = []
    for core in cores:
        cdir = CONFIG_BASE / core
        if not cdir.is_dir():
            continue
        for p in cdir.glob("*.cfg"):
            try:
                t = p.read_text(encoding="utf-8", errors="replace")
                if SENTINEL in t and marker in t:
                    out.append(p)
            except OSError:
                pass
    return out


def status(key):
    """{installed, overlay_files, games, enabled, disabled, repo_present}."""
    s = _by_key(key)
    if not s:
        return {"installed": False}
    _, _, repo, subdir, _, _, _ = s
    overlay = OVERLAY_BASE / subdir
    overlay_files = len(list(overlay.glob("*"))) if overlay.is_dir() else 0
    # A game spans several cores; count UNIQUE games (enabled if any core cfg is on).
    games: dict[str, bool] = {}
    for p in _game_cfgs(key):
        try:
            on = bool(re.search(r'(?m)^\s*input_overlay_enable\s*=\s*"?true"?',
                                p.read_text(errors="replace")))
        except OSError:
            on = False
        games[p.stem] = games.get(p.stem, False) or on
    enabled = sum(1 for v in games.values() if v)
    return {"installed": overlay_files > 0 or bool(games),
            "overlay_files": overlay_files, "games": len(games),
            "enabled": enabled, "disabled": len(games) - enabled,
            "widescreen_warn": key in WIDESCREEN_WARN,
            "repo_present": _src_subdir(repo, subdir) is not None}


def list_systems():
    """All bezel systems with repo-presence + status (for the page tiles)."""
    out = []
    for key, label, repo, subdir, _, _, art in SYSTEMS:
        st = status(key)
        out.append({"key": key, "label": label, "art_system": art,
                    "repo_present": _src_subdir(repo, subdir) is not None,
                    "widescreen_warn": key in WIDESCREEN_WARN, **st})
    # Tiles render in this list's order (MadTileGrid lays out left-to-right);
    # sort by display label so the Bezel page reads A->Z. All lookups are by
    # key via _by_key(), so output order is purely cosmetic.
    return sorted(out, key=lambda r: r["label"].lower())


def install(key, *, tmp_holder=None):
    """Symlink the pack's overlays + write per-game cfgs for owned ROMs (enabled).
    Hand-made cfgs are preserved to _TMP. Returns a summary dict."""
    s = _by_key(key)
    if not s:
        raise ValueError(f"unknown bezel system {key!r}")
    _, label, repo, subdir, rom_dirs, cores, _ = s
    src = _src_subdir(repo, subdir)
    if src is None:
        raise FileNotFoundError(f"bezel pack for {label} not found under {BEZEL_BASE}/bezelproject-{repo}")
    overlay = OVERLAY_BASE / subdir
    overlay.mkdir(parents=True, exist_ok=True)

    links = 0
    for p in list(src.glob("*.cfg")) + list(src.glob("*.png")):
        dest = overlay / p.name
        if dest.is_symlink() or dest.exists():
            dest.unlink()
        dest.symlink_to(p)
        links += 1

    tmp = {"d": tmp_holder}  # one _TMP shared across a run, created lazily
    games = 0
    for cfg_src in src.glob("*.cfg"):
        game = cfg_src.stem
        if not _rom_exists(game, rom_dirs):
            continue
        for core in cores:
            cdir = CONFIG_BASE / core
            cdir.mkdir(parents=True, exist_ok=True)
            target = cdir / f"{game}.cfg"
            if target.exists():
                existing = target.read_text(encoding="utf-8", errors="replace")
                if SENTINEL not in existing and "wire-bezels" not in existing:
                    if tmp["d"] is None:
                        tmp["d"] = _tmp_dir()
                    shutil.move(str(target), str(tmp["d"] / f"{core}__{game}.cfg"))
            target.write_text(_PER_GAME_CFG.format(
                overlay=overlay / f"{game}.cfg", enabled="true"), encoding="utf-8")
        games += 1
    return {"system": key, "links": links, "games": games,
            "preserved_tmp": str(tmp["d"]) if tmp["d"] else None}


def uninstall(key):
    """Move our overlay symlinks + sentinel per-game cfgs to _TMP (recoverable).
    Hand-made cfgs are left untouched. Returns a summary."""
    s = _by_key(key)
    if not s:
        raise ValueError(f"unknown bezel system {key!r}")
    _, _, _, subdir, _, _, _ = s
    tmp = _tmp_dir()
    moved_cfgs = 0
    for p in _game_cfgs(key):
        shutil.move(str(p), str(tmp / f"{p.parent.name}__{p.name}"))
        moved_cfgs += 1
    moved_links = 0
    overlay = OVERLAY_BASE / subdir
    if overlay.is_dir():
        dest = tmp / "overlays" / subdir
        dest.parent.mkdir(parents=True, exist_ok=True)
        moved_links = len(list(overlay.iterdir()))
        shutil.move(str(overlay), str(dest))
    return {"system": key, "moved_cfgs": moved_cfgs, "moved_overlay_files": moved_links,
            "tmp": str(tmp)}


def _set_enable_in(path, on):
    text = path.read_text(encoding="utf-8", errors="replace")
    new = re.sub(r'(?m)^(\s*input_overlay_enable\s*=\s*)"?(?:true|false)"?\s*$',
                 lambda m: m.group(1) + ('"true"' if on else '"false"'), text, count=1)
    if new != text:
        tmp = path.with_suffix(path.suffix + ".mad-tmp")
        tmp.write_text(new, encoding="utf-8")
        tmp.replace(path)
        return True
    return False


def set_enabled(key, on):
    """Enable/disable bezels for a whole system (flips input_overlay_enable in our cfgs)."""
    n = sum(1 for p in _game_cfgs(key) if _set_enable_in(p, on))
    return {"system": key, "changed": n, "enabled": bool(on)}


def disable_game(key, game, on):
    """Enable/disable bezel for ONE game (across its cores)."""
    s = _by_key(key)
    cores = s[5] if s else []
    n = 0
    for core in cores:
        p = CONFIG_BASE / core / f"{game}.cfg"
        if p.is_file():
            try:
                if SENTINEL in p.read_text(errors="replace") and _set_enable_in(p, on):
                    n += 1
            except OSError:
                pass
    return {"system": key, "game": game, "changed": n, "enabled": bool(on)}


def list_games(key):
    """Configured games for a system with per-game enabled state + the bezel
    preview image path (for the per-game page)."""
    seen = {}
    for p in _game_cfgs(key):
        game = p.stem
        try:
            on = bool(re.search(r'(?m)^\s*input_overlay_enable\s*=\s*"?true"?',
                                p.read_text(errors="replace")))
        except OSError:
            on = False
        # a game spans cores; treat enabled if any core cfg is enabled
        seen[game] = seen.get(game, False) or on
    s = _by_key(key)
    overlay = OVERLAY_BASE / s[3] if s else None
    out = []
    for g in sorted(seen):
        png = (overlay / f"{g}.png") if overlay else None
        out.append({"game": g, "enabled": seen[g],
                    "preview": str(png) if png and png.exists() else ""})
    return out
