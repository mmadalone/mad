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
try:
    from . import es_collections as _esc
    ROMS = _esc.rom_root()      # ES-DE's <ROMDirectory> (falls back to ~/ROMs); folder-agnostic
except Exception:
    ROMS = Path("/run/media/deck/1tbDeck/ROMs")   # last-resort fallback
_RA = _HOME / ".var/app/org.libretro.RetroArch/config/retroarch"
OVERLAY_BASE = _RA / "overlays/GameBezels"
CONFIG_BASE = _RA / "config"
SENTINEL = "# bezelproject"
# Line-anchored markers for "this per-game cfg was tool-generated (safe to overwrite)".
# A bare substring match would misclassify a HAND-MADE cfg that merely mentions
# bezelproject / wire-bezels inside a comment, and then overwrite it (House Rule #5 data
# loss). Match only a real leading-comment marker line.
_SENTINEL_RE = re.compile(r"(?m)^\s*# bezelproject")
_WIRE_RE = re.compile(r"(?m)^\s*# wire-bezels")


def _is_tool_generated(text: str) -> bool:
    """True ⇒ this per-game cfg was written by us (bezel sentinel) or wire-bezels, so it is
    safe to overwrite. False ⇒ hand-made; callers MUST move it to _TMP, never overwrite."""
    return bool(_SENTINEL_RE.search(text) or _WIRE_RE.search(text))


_ROM_EXTS = ("zip", "7z", "chd", "iso", "cue", "cdi", "bin", "nes", "sfc", "smc",
             "smd", "gen", "md", "32x", "gb", "gbc", "gba", "n64", "z64", "v64", "pce",
             "sgx", "wbfs", "rvz", "gcm")
# ROM extensions that represent a GAME ENTRY (what ES-DE lists) for the reassign
# TARGET picker, adding the disc entry-points gdi/m3u/pbp that _ROM_EXTS omits. "bin"
# IS included (Genesis/32X/PCE/MAME ship single-file .bin GAMES) — raw multi-track disc
# files ("<game> (Track 1).bin") are filtered out by name via _TRACK_RE instead, so
# real .bin games stay visible while disc tracks don't clutter the list.
_GAME_EXTS = ("zip", "7z", "chd", "iso", "cue", "cdi", "gdi", "m3u", "pbp", "bin",
              "nes", "sfc", "smc", "smd", "gen", "md", "32x", "gb", "gbc", "gba",
              "n64", "z64", "v64", "pce", "sgx", "wbfs", "rvz", "gcm")
_TRACK_RE = re.compile(r"\(Track\s*\d+\)", re.IGNORECASE)   # "<game> (Track 1)" disc tracks
_DISC_MASTER_EXTS = {"cue", "gdi", "cdi", "chd", "m3u"}     # presence ⇒ .bin are data tracks

# key | label | repo (bezelproject-<repo>) | overlay subdir | rom dirs | cores | art system
SYSTEMS = [
    ("nes", "NES", "NES", "NES", ["nes"], ["Mesen", "Nestopia", "FCEUmm"], "nes"),
    ("famicom", "Famicom", "Famicom", "Famicom", ["famicom"], ["Mesen", "Nestopia", "FCEUmm"], "famicom"),
    # snesh (romhacks) + snesmsu1 (MSU-1) launch via snes9x — fold their dirs into the SNES pack so they
    # get bezels too. snesmsu1's "Base (USA) (MSU1)" names norm-match the base bezel exactly (auto-wire);
    # snesh hacks have original titles -> reviewable / manual. Same cores, so cfgs land in the right dirs.
    ("snes", "SNES", "SNES", "SNES", ["snes", "sfc", "snesh", "snesmsu1"],
     ["Snes9x", "bsnes", "Snes9x - Current"], "snes"),
    ("n64", "Nintendo 64", "N64", "Nintendo 64", ["n64"], ["Mupen64Plus-Next", "ParaLLEl N64"], "n64"),
    # genh (Genesis romhacks) launch via genesis_plus_gx — folded in (reviewable; mostly manual).
    ("megadrive", "Mega Drive / Genesis", "MegaDrive", "Megadrive", ["genesis", "megadrive", "genh"],
     ["Genesis Plus GX", "BlastEm", "PicoDrive"], "megadrive"),
    ("mastersystem", "Master System", "MasterSystem", "MasterSystem", ["mastersystem"],
     ["Genesis Plus GX", "Gearsystem"], "mastersystem"),
    ("gamegear", "Game Gear", "GameGear", "GameGear", ["gamegear"], ["Gearsystem", "Genesis Plus GX"], "gamegear"),
    ("segacd", "Sega CD", "SegaCD", "Sega CD", ["segacd"], ["Genesis Plus GX", "PicoDrive"], "segacd"),
    ("sega32x", "Sega 32X", "Sega32x", "Sega32X", ["sega32x"], ["PicoDrive"], "sega32x"),
    ("saturn", "Saturn", "Saturn", "Saturn", ["saturn"], ["Beetle Saturn", "Kronos", "YabaSanshiro"], "saturn"),
    ("dreamcast", "Dreamcast", "Dreamcast", "Dreamcast", ["dreamcast"], ["Flycast"], "dreamcast"),
    ("atomiswave", "Atomiswave", "Atomiswave", "Atomiswave", ["atomiswave"], ["Flycast"], "atomiswave"),
    # naomi = a COMBINED "Sega Arcade (Naomi / Naomi 2 / Model 3)" collection (one ~/ROMs/naomi
    # dir). The Naomi pack is exact-stem matched; Naomi-2 titles in the collection are covered
    # only if a Naomi 2 pack is added, and Model 3 titles launched via Supermodel get no RA overlay.
    ("naomi", "Naomi", "Naomi", "Naomi", ["naomi"], ["Flycast"], "naomi"),
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
# Saturn/Dreamcast/Sega CD + Naomi mix 4:3 and 16:9 — the 4:3 bezel can look wrong on a
# widescreen game; the page warns for these. (Atomiswave is 4:3-native, so it's NOT here.)
WIDESCREEN_WARN = {"saturn", "dreamcast", "segacd", "naomi"}

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


# Per-game Flycast WIDESCREEN override (config/<core>/<game>.opt). The bezel per-game cfg
# forces 4:3 (aspect_ratio_index=22), which would SQUISH a game the user set up for 16:9 via
# the Flycast widescreen hack/cheats — and clobber that .opt-paired setup. install() skips
# such games. Non-Flycast cores never carry reicast_ keys, so this is a no-op for them.
_WIDESCREEN_RE = re.compile(r'(?m)^\s*reicast_widescreen_(?:hack|cheats)\s*=\s*"?enabled"?')


def _has_widescreen_on(game, cores):
    """True if a per-game core-options override (config/<core>/<game>.opt) has the Flycast
    widescreen hack OR cheats explicitly ENABLED — i.e. the game is set up for 16:9."""
    for core in cores:
        opt = CONFIG_BASE / core / f"{game}.opt"
        try:
            if opt.is_file() and _WIDESCREEN_RE.search(
                    opt.read_text(encoding="utf-8", errors="replace")):
                return True
        except OSError:
            continue
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
    """All bezel systems with repo-presence + status. SUPERSEDED for the page tiles by
    bezel_discover.list_systems() (which is DYNAMIC/gamelist-filtered — drops systems with
    no games, e.g. Game Gear). Kept as the unfiltered full-SYSTEMS reference; do NOT wire it
    back to bezels.list or the dropped tiles reappear."""
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
    skipped_widescreen = 0
    for cfg_src in src.glob("*.cfg"):
        game = cfg_src.stem
        if not _rom_exists(game, rom_dirs):
            continue
        # Leave WIDESCREEN-configured games alone — a forced-4:3 bezel would squish them.
        if _has_widescreen_on(game, cores):
            skipped_widescreen += 1
            continue
        for core in cores:
            cdir = CONFIG_BASE / core
            cdir.mkdir(parents=True, exist_ok=True)
            target = cdir / f"{game}.cfg"
            enabled = "true"
            if target.exists():
                existing = target.read_text(encoding="utf-8", errors="replace")
                if _is_tool_generated(existing):
                    # Re-install / update must NOT silently re-enable a bezel the user
                    # disabled (disable_game) — preserve the current toggle. Only a fresh
                    # cfg (or one replacing a hand-made cfg) defaults to enabled.
                    m = re.search(r'(?m)^\s*input_overlay_enable\s*=\s*"?(true|false)"?', existing)
                    if m and m.group(1) == "false":
                        enabled = "false"
                else:
                    if tmp["d"] is None:
                        tmp["d"] = _tmp_dir()
                    shutil.move(str(target), str(tmp["d"] / f"{core}__{game}.cfg"))
            target.write_text(_PER_GAME_CFG.format(
                overlay=overlay / f"{game}.cfg", enabled=enabled), encoding="utf-8")
        games += 1

    # ── Phase-3 normalized-equal pass (additive; only still-unwired owned ROMs). Folded in
    # here so a fresh install also picks up region/edition-different ROMs; auto_match is
    # also callable standalone for an already-populated pack (it never rewrites existing cfgs).
    nm = auto_match(key, tmp_holder=tmp["d"])
    if nm.get("preserved_tmp"):
        tmp["d"] = Path(nm["preserved_tmp"])
    skipped_widescreen += nm.get("skipped_widescreen", 0)

    return {"system": key, "links": links, "games": games,
            "norm_games": nm.get("norm_games", 0),
            "skipped_widescreen": skipped_widescreen,
            "preserved_tmp": str(tmp["d"]) if tmp["d"] else None}


def auto_match(key, *, tmp_holder=None):
    """Normalized-equal ADDITIVE bezel matching: for each owned ROM with no bezel yet, if
    its normalized name maps to a SINGLE installed pack bezel (region/edition/punctuation
    differences only), wire it via assign_bezel. Ambiguous norms (several bezels share the
    name) are left for the interactive review — never silent-guessed. Touches ONLY unwired
    ROMs — never rewrites or re-enables an existing cfg, so it is safe to run repeatedly on
    a populated pack. Returns {norm_games, skipped_widescreen, preserved_tmp}."""
    s = _by_key(key)
    if not s:
        raise ValueError(f"unknown bezel system {key!r}")
    cores = s[5]
    overlay = OVERLAY_BASE / s[3]
    if not overlay.is_dir():   # pack not installed -> nothing wire-able
        return {"system": key, "norm_games": 0, "skipped_widescreen": 0, "preserved_tmp": None}
    from . import bezel_match
    bezels = [c.stem for c in overlay.glob("*.cfg") if not (c.is_symlink() and not c.exists())]
    nmap = bezel_match.norm_map(bezels)
    tmp = {"d": tmp_holder}
    norm_games = 0
    skipped_widescreen = 0
    for rom in _owned_unmatched(key):
        pick = _norm_tiebreak(nmap.get(bezel_match.norm(rom), []), rom)
        if pick is None:
            continue
        if _has_widescreen_on(rom, cores):   # same 4:3-squish guard as the exact pass
            if pick != rom:   # exact-named widescreen games are already counted by install()'s
                skipped_widescreen += 1   # exact pass; only tally the norm-matched ones here.
            continue
        try:
            res = assign_bezel(key, rom, pick, tmp_holder=tmp["d"])
        except (FileNotFoundError, OSError):
            continue
        if res.get("preserved_tmp"):
            tmp["d"] = Path(res["preserved_tmp"])
        norm_games += 1
    return {"system": key, "norm_games": norm_games,
            "skipped_widescreen": skipped_widescreen,
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


def prune_unowned(key):
    """Move MAD/bezelproject sentinel per-game cfgs whose ROM the user does NOT own to _TMP
    (rule #5, recoverable) — e.g. cfgs a bulk Bezel-Project install left for games not in
    your collection. Cfgs for owned games are untouched. Returns {moved, tmp}."""
    s = _by_key(key)
    if not s:
        raise ValueError(f"unknown bezel system {key!r}")
    owned = _owned_rom_stems(key)
    tmp = None
    moved = 0
    games = set()
    for p in _game_cfgs(key):
        if p.stem in owned:
            continue
        if tmp is None:
            tmp = _tmp_dir()
        shutil.move(str(p), str(tmp / f"{p.parent.name}__{p.name}"))
        moved += 1
        games.add(p.stem)
    return {"system": key, "moved": moved, "games": len(games), "tmp": str(tmp) if tmp else None}


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


def _titles_for(key):
    """{rom-stem.lower(): gamelist <name>} unioned over a bezel system's member rom dirs
    (e.g. megadrive = genesis + megadrive), so the page can show human titles instead of
    rom stems. Empty/unreadable gamelists -> {} and callers fall back to the stem."""
    from . import es_gamelist
    s = _by_key(key)
    return es_gamelist.titles_for(s[4]) if s else {}


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
    titles = _titles_for(key)
    # Show only games the user actually has — i.e. that ES-DE lists in a member
    # gamelist. A bulk Bezel-Project install wires overlay .cfgs for thousands of
    # romsets you may not own (DECO-cassette / homebrew arcade, …); those carry no
    # gamelist entry and no title, so they'd otherwise show as bare romset stems.
    # Fail-safe: if NO member gamelist is readable (empty set), don't filter — never
    # hide every row.
    from . import es_gamelist
    listed = es_gamelist.listed_stems(tuple(s[4])) if s else frozenset()
    out = []
    for g in sorted(seen):
        if listed and g.lower() not in listed:
            continue  # wired bezel for a game not in your gamelists — hide it
        # The preview is the bezel the game's cfg POINTS AT — its own name for an installed
        # game, a DIFFERENT bezel for a reassigned one (Feature ③ assign_bezel) — so derive it
        # from the cfg's input_overlay, not always <game>.png.
        src = _assigned_source(key, g) or g
        png = (overlay / f"{src}.png") if overlay else None
        out.append({"game": g, "enabled": seen[g],
                    "title": titles.get(g.lower(), ""),     # "" -> C++ falls back to the stem
                    "preview": str(png) if png and png.exists() else ""})
    return out


# ── assign / reassign an EXISTING bezel to a same-system game ──────────────────
# Use case: "Death Crimson 2 (Japan)" has a Bezel-Project bezel but the community
# English-patched "Death Crimson 2 (Japan) (English)" has no 1:1-named bezel, so it
# gets none. Point game B at game A's existing overlay .cfg via a per-game RA override
# (NO symlink — house rule #4). Reassign = call assign_bezel again with a new source.

def list_available_bezels(key):
    """Every bezel CURRENTLY AVAILABLE for a system: the overlay .cfg/.png pairs in
    overlays/GameBezels/<subdir>/ (present once the pack is installed). For the
    reassign picker's SOURCE list. Returns name + preview PNG path (A->Z)."""
    s = _by_key(key)
    if not s:
        return []
    overlay = OVERLAY_BASE / s[3]
    if not overlay.is_dir():
        return []
    titles = _titles_for(key)
    out = []
    for cfg in sorted(overlay.glob("*.cfg")):
        if cfg.is_symlink() and not cfg.exists():
            continue            # broken symlink (pack uninstalled) — don't offer an unusable bezel
        name = cfg.stem
        png = overlay / f"{name}.png"
        out.append({"name": name, "title": titles.get(name.lower(), ""),
                    "preview": str(png) if png.exists() else ""})
    return out


def _assigned_source(key, game):
    """The bezel-stem a game's MAD per-game cfg currently points input_overlay at,
    or '' if the game has no MAD bezel cfg for this system."""
    s = _by_key(key)
    if not s:
        return ""
    subdir, cores = s[3], s[5]
    marker = f"/GameBezels/{subdir}/"
    for core in cores:
        p = CONFIG_BASE / core / f"{game}.cfg"
        if not p.is_file():
            continue
        try:
            t = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if SENTINEL in t and marker in t:
            m = re.search(r'(?m)^\s*input_overlay\s*=\s*"([^"]+)"', t)
            if m:
                return Path(m.group(1)).stem
    return ""


def _owned_rom_stems(key):
    """The set of game-stems ES-DE would list for this bezel system's member rom dirs
    (the reassign-target / fuzzy population), with disc-track + .bin-on-disc filtering."""
    s = _by_key(key)
    if not s:
        return set()
    stems = set()
    for d in s[4]:
        rd = ROMS / d
        if not rd.is_dir():
            continue
        files = [p for p in rd.iterdir() if p.is_file()]
        # If this dir holds disc images (gdi/cue/cdi/chd/m3u), any .bin is a data TRACK of
        # one of them (incl. ".A1"/".B1" segment names, not just "(Track N)") — drop .bin
        # for disc systems; keep it for CARTRIDGE systems (Genesis/32X/PCE ship single-file
        # .bin GAMES). "(Track N)"-named files are dropped regardless of system.
        has_disc = any(p.suffix.lower().lstrip(".") in _DISC_MASTER_EXTS for p in files)
        for p in files:
            ext = p.suffix.lower().lstrip(".")
            if ext not in _GAME_EXTS or _TRACK_RE.search(p.stem):
                continue
            if ext == "bin" and has_disc:
                continue
            stems.add(p.stem)
    return stems


def _owned_unmatched(key):
    """Owned game-stems with NO MAD/bezelproject sentinel cfg yet — the population the
    Phase-3 norm-equal + fuzzy passes try to assign a bezel to."""
    wired = {p.stem for p in _game_cfgs(key)}
    return _owned_rom_stems(key) - wired


def _norm_tiebreak(cands, rom):
    """Resolve several bezels that share a normalized name. UNIQUE -> that one. The only
    auto-resolved ambiguity is Amiga CD32 vs floppy, decided BY THE ROM: a CD32 ROM takes
    the unique '… CD32' bezel, a non-CD32 ROM takes the unique non-CD32 bezel. Anything
    still ambiguous returns None and is LEFT for the interactive review (never silent-guessed)."""
    if len(cands) == 1:
        return cands[0]
    rom_cd32 = "cd32" in rom.lower()
    cd32 = [c for c in cands if "cd32" in c.lower()]
    noncd32 = [c for c in cands if "cd32" not in c.lower()]
    if rom_cd32 and len(cd32) == 1:
        return cd32[0]
    if not rom_cd32 and len(noncd32) == 1:
        return noncd32[0]
    return None


def list_roms(key):
    """Every ROM for a system (so a game with NO bezel is still a pickable TARGET),
    each flagged with the bezel it currently points at (assigned='' = none) and whether
    a 1:1-named bezel exists. For the reassign picker's target list (A->Z)."""
    s = _by_key(key)
    if not s:
        return []
    subdir = s[3]
    overlay = OVERLAY_BASE / subdir
    stems = _owned_rom_stems(key)
    titles = _titles_for(key)
    out = []
    for g in sorted(stems):
        assigned = _assigned_source(key, g)
        out.append({"game": g, "assigned": assigned,
                    "title": titles.get(g.lower(), ""),
                    "assigned_title": titles.get(assigned.lower(), "") if assigned else "",
                    "has_own_bezel": bool(overlay.is_dir() and (overlay / f"{g}.cfg").exists())})
    return out


_NORMED_CACHE: dict = {}   # (key, overlay-mtime_ns) -> [(bezel_stem, norm)], one entry at a time


def _normed_bezels(key):
    """Cached [(bezel_stem, norm(stem))] for a system's installed overlay bezels, reused
    across the per-ROM fuzzy_candidates() calls of one review so only the first ROM pays the
    ~0.5 s glob+normalize of a 9k-bezel pack. Re-derived when the overlay dir changes (e.g.
    after an install — keyed on its mtime)."""
    s = _by_key(key)
    if not s:
        return []
    overlay = OVERLAY_BASE / s[3]
    try:
        sig = (key, overlay.stat().st_mtime_ns)
    except OSError:
        return []
    cached = _NORMED_CACHE.get(sig)
    if cached is None:
        from . import bezel_match
        bezels = [c.stem for c in overlay.glob("*.cfg") if not (c.is_symlink() and not c.exists())]
        cached = bezel_match.normed(bezels)
        _NORMED_CACHE.clear()      # bound to one system's review at a time
        _NORMED_CACHE[sig] = cached
    return cached


def fuzzy_unmatched(key):
    """The interactive review's WORK LIST: every owned ROM with no bezel yet (game + title).
    Candidates are ranked LAZILY per-ROM via fuzzy_candidates() — ranking every ROM up front
    is too slow on big packs (9k+ bezels × 100+ ROMs ≈ 12 s). Run AFTER auto_match so the
    confident normalized-equal ROMs are already wired and excluded."""
    s = _by_key(key)
    if not s:
        return []
    titles = _titles_for(key)
    return [{"game": rom, "title": titles.get(rom.lower(), "")}
            for rom in sorted(_owned_unmatched(key))]


def fuzzy_candidates(key, game, n=8, query=""):
    """Top-n difflib-ranked bezel candidates for ONE rom (name + title + preview PNG +
    score), for the interactive picker — ranked against all installed bezels on demand
    (≈0.15 s, so the review walks ROM-by-ROM without a long up-front wait). When `query`
    is given (the Y/refine search) candidates are ranked against the typed text instead of
    the rom name — so a romhack can be searched by its base game."""
    s = _by_key(key)
    if not s:
        return []
    from . import bezel_match
    overlay = OVERLAY_BASE / s[3]
    if not overlay.is_dir():
        return []
    target = query.strip() or game
    titles = _titles_for(key)
    out = []
    for bez, score in bezel_match.rank_candidates(target, _normed_bezels(key), n):
        png = overlay / f"{bez}.png"
        out.append({"name": bez, "title": titles.get(bez.lower(), ""),
                    "preview": str(png) if png.is_file() else "",
                    "score": round(score, 3)})
    return out


def assign_bezel(key, target_game, source_bezel, *, enabled=True, tmp_holder=None):
    """Point target_game at an EXISTING bezel (source_bezel) by writing a per-game RA
    override whose input_overlay is that bezel's overlay .cfg, across the system's cores
    (no symlink). A HAND-MADE (non-sentinel, non-wire-bezels) cfg for the target is moved
    to _TMP first (rule #5). Both the source .cfg AND .png must exist, else RetroArch
    would silently render no overlay. Reassign = call again with a different source."""
    s = _by_key(key)
    if not s:
        raise ValueError(f"unknown bezel system {key!r}")
    _, _, _, subdir, _, cores, _ = s
    overlay = OVERLAY_BASE / subdir
    src_cfg = overlay / f"{source_bezel}.cfg"
    src_png = overlay / f"{source_bezel}.png"
    if not src_cfg.is_file():
        raise FileNotFoundError(f"bezel {source_bezel!r} is not installed for {key}")
    if not src_png.is_file():
        raise FileNotFoundError(
            f"bezel image {source_bezel}.png is missing — RetroArch would show no overlay")
    tmp = {"d": tmp_holder}
    written = []
    for core in cores:
        cdir = CONFIG_BASE / core
        cdir.mkdir(parents=True, exist_ok=True)
        target = cdir / f"{target_game}.cfg"
        if target.exists():
            existing = target.read_text(encoding="utf-8", errors="replace")
            if SENTINEL not in existing and "wire-bezels" not in existing:
                if tmp["d"] is None:
                    tmp["d"] = _tmp_dir()
                shutil.move(str(target), str(tmp["d"] / f"{core}__{target_game}.cfg"))
        target.write_text(_PER_GAME_CFG.format(
            overlay=src_cfg, enabled="true" if enabled else "false"), encoding="utf-8")
        written.append(str(target))
    return {"system": key, "target": target_game, "source": source_bezel,
            "cores": len(written), "written": written,
            "preserved_tmp": str(tmp["d"]) if tmp["d"] else None}
