"""
Per-game RetroArch override (.cfg) writer for the controller router.

Each ROM-launch may produce a tiny block of `input_player[N]_reserved_device`
and (for lightgun games) `input_player[N]_mouse_index` lines, written into
the per-core per-game override at:

    ~/.var/app/org.libretro.RetroArch/config/retroarch/config/<CoreName>/<ROM_basename>.cfg

These files often ALREADY exist — the bezel-project pipeline wrote ~15k of
them with `input_overlay`, `aspect_ratio_index`, and similar non-input
settings. We must preserve all of that. Our block is wrapped in sentinel
comments so it can be added, refreshed, or stripped without touching the
surrounding lines.

Writes are atomic (tmp + rename in the same directory) and idempotent
(re-running with the same input produces an identical file).
"""
from __future__ import annotations

import re
from pathlib import Path

from . import es_gamelist
from . import es_systems
from . import fsutil
from . import proc_guard

# Sentinel markers — anything between BEGIN and END (inclusive) is owned by
# the router and may be rewritten/removed at will.
BEGIN = "# >>> controller-router begin (auto-managed) >>>"
END = "# <<< controller-router end <<<"

RA_CONFIG_BASE = (
    Path.home() / ".var/app/org.libretro.RetroArch/config/retroarch/config"
)


# System → list of RetroArch CoreDisplayName dirs we should write into. Same
# rom_basename gets the same block in each — RetroArch picks whichever core
# it actually launches and reads the matching override. Multi-core systems
# get multi-write; that's intentional.
#
# Verified against the user's actual core dirs (see ls of ~/.var/app/.../config).
SYSTEM_CORE_MAP: dict[str, list[str]] = {
    "3do":          ["Opera"],
    "amigacd32":    ["PUAE", "PUAE 2021"],
    "arcade":       ["FinalBurn Neo", "MAME", "MAME 2010", "FB Alpha 2012"],
    "atomiswave":   ["Flycast"],
    "daphne":       [],   # Hypseus-Singe, not RetroArch
    "dreamcast":    ["Flycast"],
    "famicom":      ["Nestopia", "Mesen", "FCEUmm", "QuickNES"],
    "fba":          ["FinalBurn Neo", "FB Alpha 2012"],
    "gameandwatch": ["Game & Watch"],
    "gb":           ["Gambatte", "SameBoy", "Gearboy", "TGB Dual", "mGBA"],
    "gba":          ["mGBA", "VBA-M", "VBA Next", "gpSP", "NooDS", "SkyEmu"],
    "gbc":          ["Gambatte", "SameBoy", "Gearboy", "TGB Dual", "mGBA"],
    "gc":           [],   # GameCube -> Dolphin (Standalone), not RetroArch (like wii)
    "genesis":      ["Genesis Plus GX", "BlastEm", "PicoDrive"],
    "genh":         ["Genesis Plus GX"],
    "mame":         ["MAME", "MAME 2010", "MAME 2003-Plus"],
    "mastersystem": ["Gearsystem", "Genesis Plus GX"],
    "megadrive":    ["Genesis Plus GX", "BlastEm", "PicoDrive"],
    "model3":       [],   # Supermodel standalone
    "mugen":        [],   # mugen.sh wrapper
    "n64":          ["Mupen64Plus-Next", "ParaLLEl N64"],
    "naomi":        ["Flycast"],
    "naomi2":       ["Flycast"],
    "neogeo":       ["FinalBurn Neo", "FB Alpha 2012"],
    "nes":          ["Nestopia", "Mesen", "FCEUmm"],
    "pcengine":     ["Beetle PCE", "Beetle PCE Fast", "Beetle SuperGrafx"],
    "pcenginecd":   ["Beetle PCE", "Beetle PCE Fast", "Beetle SuperGrafx"],
    "pcfx":         ["Beetle PC-FX"],
    "ps2":          ["LRPS2", "PCSX2"],            # also has a PCSX2-standalone backend
    "psx":          ["Beetle PSX HW", "Beetle PSX", "SwanStation"],
    "saturn":       ["Beetle Saturn", "Kronos", "YabaSanshiro"],
    "sega32x":      ["PicoDrive"],
    "segacd":       ["Genesis Plus GX", "PicoDrive"],
    "sfc":          ["Snes9x", "bsnes", "bsnes-hd beta"],
    "snes":         ["Snes9x", "bsnes", "bsnes-hd beta"],
    "snesh":        ["Snes9x", "bsnes"],
    "snesmsu1":     ["Snes9x", "bsnes"],
    "supergrafx":   ["Beetle SuperGrafx"],
    "wii":          [],   # Dolphin (Standalone)
    "x68000":       ["PX68K"],
}


_INFO_DIR = RA_CONFIG_BASE.parent / "info"   # …/retroarch/info/<stem>_libretro.info
_corename_cache: dict[str, str | None] = {}
_CORE_SO_RE = re.compile(r"([A-Za-z0-9_]+)_libretro\.so")


def _corename(stem: str) -> str | None:
    """The libretro core's display name (= the name RetroArch uses for its
    per-game-override config dir), read from <stem>_libretro.info's `corename`
    line. Cached. None if the info file/line is absent."""
    if stem in _corename_cache:
        return _corename_cache[stem]
    cn = None
    try:
        for line in (_INFO_DIR / f"{stem}_libretro.info").read_text(
                encoding="utf-8", errors="replace").splitlines():
            if line.lstrip().startswith("corename"):
                m = re.search(r'corename\s*=\s*"?([^"]+?)"?\s*$', line)
                cn = m.group(1).strip() if m else None
                break
    except OSError:
        cn = None
    _corename_cache[stem] = cn
    return cn


def _derived_core_names(system: str) -> set[str]:
    """Core-dir names derived from the system's ES-DE RetroArch commands — the
    dynamic complement to SYSTEM_CORE_MAP, so a newly-added/wrapped RA system
    routes with no hand-edit. Empty if es_systems / the info dir is unavailable."""
    try:
        from . import es_systems        # lazy — no import cycle
        cmds = es_systems.load_systems().get(system, [])
    except Exception:
        return set()
    names = set()
    for _label, cmd in cmds:
        for m in _CORE_SO_RE.finditer(cmd):
            cn = _corename(m.group(1))
            if cn:
                names.add(cn)
    return names


def core_dirs_for_system(system: str, prefer_core: str | None = None) -> list[Path]:
    """Core dirs to write the per-game override into, restricted to those that
    actually exist on disk. UNION of the curated SYSTEM_CORE_MAP (exceptions /
    legacy baseline — covers corename≠dir cases like dolphin_emu and MAME 2010)
    and dirs DERIVED from the system's active ES-DE commands (covers new systems
    + cores the map missed). Multi-write is intentional so per-game
    <altemulator> overrides keep working. Degrades to exactly the old map result
    when derivation yields nothing.

    `prefer_core` (a core-dir NAME, e.g. from launched_core()) moves that dir to
    the FRONT via a stable sort — the rest stay A→Z. WRITE callers must never
    pass this (writers stay multi-write across every core dir); it's for READ
    callers that want the LAUNCHED core's content instead of the alphabetically-
    first one whenever more than one core dir exists for the system."""
    names = set(SYSTEM_CORE_MAP.get(system, [])) | _derived_core_names(system)
    dirs = [RA_CONFIG_BASE / n for n in sorted(names) if (RA_CONFIG_BASE / n).is_dir()]
    if prefer_core:
        dirs.sort(key=lambda d: d.name != prefer_core)   # stable: preferred dir first
    return dirs


# ── launched-core resolver ────────────────────────────────────────────────────
# Per-game reads (get_game_options/get_game_remap et al.) default to the
# ALPHABETICALLY-FIRST core dir core_dirs_for_system returns — wrong whenever a
# system has more than one real core installed and the user's actual launch
# uses a different one. `launched_core` resolves which core dir the CURRENT
# launch (system default, or a per-game <altemulator> override) actually reads,
# so callers can pass it as `prefer_core` and get the right content. Writes are
# UNCHANGED (still multi-write to every core dir) — this is read-side only.

def _command_for_label(system: str, label: str, systems: dict | None = None) -> str | None:
    """The es_systems <command> TEXT whose label == `label` for `system`, or
    None if no command carries that label. `systems` lets a caller that already
    holds es_systems.load_systems() avoid re-parsing the XML per call."""
    for lbl, text in (systems or es_systems.load_systems()).get(system, []):
        if lbl == label:
            return text
    return None


def _core_name_from_command(cmd: str | None) -> str | None:
    """The libretro core's display name embedded in an es_systems <command>
    (via its *_libretro.so token → _corename()), or None for a standalone
    command (no %EMULATOR_RETROARCH% macro) or one with no resolvable core."""
    if not cmd or es_systems.is_standalone(cmd):
        return None
    for m in _CORE_SO_RE.finditer(cmd):
        cn = _corename(m.group(1))
        if cn:
            return cn
    return None


def _reconcile_core(system: str, cn: str | None) -> str | None:
    """Reconcile a resolved corename against the on-disk config dir. A core's
    .info `corename` does not always name its config dir: it can carry a version
    suffix (MAME 2010's corename "MAME 2010 (0.139)" -> dir "MAME 2010") or be a
    plain display name (GameCube's Dolphin core -> dir "dolphin_emu"). Order:
    (1) exact dir; (2) the corename minus a trailing "(version)"; (3) the
    SYSTEM_CORE_MAP candidate whose dir exists that BEST matches `cn` (the
    longest that is a prefix of it - so "MAME 2010" wins over "FinalBurn Neo"),
    else the first existing candidate; (4) `cn` best-effort. Fixes: an
    altemulator=MAME 2010 game was mis-resolving to the map's index-0 core."""
    if cn is None:
        return None
    if (RA_CONFIG_BASE / cn).is_dir():
        return cn
    base = re.sub(r"\s*\([^)]*\)\s*$", "", cn)          # "MAME 2010 (0.139)" -> "MAME 2010"
    if base != cn and (RA_CONFIG_BASE / base).is_dir():
        return base
    existing = [c for c in SYSTEM_CORE_MAP.get(system, []) if (RA_CONFIG_BASE / c).is_dir()]
    for cand in sorted(existing, key=len, reverse=True):
        if cn.startswith(cand):                          # disambiguate vs the first-existing
            return cand
    return existing[0] if existing else cn


def default_core(system: str, systems: dict | None = None) -> str | None:
    """The core-display-name the system's DEFAULT command launches (no per-game
    <altemulator>). It is identical for every game in a system, so ragame.games
    resolves it ONCE rather than per game -- default_command re-reads the
    gamelist (for the system-level alternativeEmulator) and doing that per game
    is ~3.5s on a 1800+ game system."""
    if systems is None:
        systems = es_systems.load_systems()
    return _reconcile_core(system, _core_name_from_command(
        es_systems.default_command(system, systems)))


def launched_core(system: str, stem: str, systems: dict | None = None) -> str | None:
    """The RetroArch core-display-name the LAUNCHED command actually reads its
    per-game override from: the per-game <altemulator> command if the gamelist
    carries one for this game, else the system's active default command
    (es_systems.default_command). None for a standalone system, or when no
    core name can be resolved. `systems` lets a caller iterating many games
    (e.g. ragame.games) pass an already-loaded es_systems.load_systems() once
    instead of this re-parsing es_systems.xml on every game.

    RECONCILES corename ≠ config-dir-name: a core's _libretro.info `corename`
    doesn't always match the directory RetroArch actually writes per-game
    overrides into (e.g. GameCube's Dolphin core → config dir `dolphin_emu`;
    MAME 2010's corename → config dir `MAME 2010`). If the resolved name IS a
    real config dir, use it as-is; otherwise fall back to the first
    SYSTEM_CORE_MAP candidate for the system whose config dir exists on disk;
    otherwise return the resolved name as a best-effort guess (still better
    than the alphabetically-first dir)."""
    alt = es_gamelist.record(system, stem).get("altemulator")
    if not alt:
        return default_core(system, systems)             # common path: no per-game override
    cmd = _command_for_label(system, alt, systems or es_systems.load_systems())
    if cmd is None:                                       # altemulator label not found -> default
        return default_core(system, systems)
    return _reconcile_core(system, _core_name_from_command(cmd))


# RetroArch device-reservation type written for every resolved player port.
#   "1" = RESERVED (exclusive): the port accepts ONLY its reserved device; no
#         other device may occupy it, and if the reserved device is absent the
#         port is left empty.
#   "2" = PREFERRED: the reserved device prefers the port, but ANY other device
#         may squat it when assignment order gets there first.
#
# We use RESERVED ("1"). PREFERRED ("2") was the original choice but it fails the
# router's whole purpose when MORE devices are connected than there are reserved
# ports — exactly the user's target setup (13 gamepads + X-Arcade + 2 Sinden
# guns all plugged in at once). Verified live 2026-06-04 (Ninjawarriors/Snes9x,
# Sindens unplugged, 3 pads present): the router correctly reserved P1=DualSense
# / P2=X-Arcade, yet RetroArch left the DualSense in port 2 and logged
#   "Preferred slot was taken earlier by (null), reassigning that to 1"
# — the preferred-cascade mis-bumped and the P1 reservation never took. The
# Sinden guns (which enumerate as joypads) jam ports 0/1 the same way. RESERVED
# makes the player ports exclusive, so guns / Wii-Pro / Steam-virtual pads are
# forced into the unreserved ports 3+ and can never displace the chosen pad.
# We only ever reserve devices we just enumerated as PRESENT (see
# controller-router._resolve_ports + its fallback), so "left empty if absent"
# can't strand a port. Same-vid:pid cascade (two X-Arcade ifaces → P1+P2, two
# identical pads → P1+P2) still works: RA fills reserved ports of a shared
# vid:pid in connection order.
_RESERVATION_TYPE = "1"


def _build_block(port_names: dict[int, str],
                 mouse_indices: dict[int, int] | None = None,
                 port_binds: dict[int, dict[str, str]] | None = None) -> str:
    """Generate the body of the sentinel block (no sentinels themselves).

    `port_binds` maps a port → {bind_suffix: value} for devices whose reserved
    port needs explicit physical→RetroPad binds (RetroArch does not carry a
    device's autoconfig binds onto a reserved port — see lib/device_binds.py).
    These override the global `input_player{N}_*` binds for the launch only.
    """
    lines = []
    for port in sorted(port_names):
        name = port_names[port]
        lines.append(f'input_player{port}_device_reservation_type = "{_RESERVATION_TYPE}"')
        lines.append(f'input_player{port}_reserved_device = "{name}"')
    if mouse_indices:
        for port in sorted(mouse_indices):
            idx = mouse_indices[port]
            lines.append(f'input_player{port}_mouse_index = "{idx}"')
    if port_binds:
        for port in sorted(port_binds):
            for suffix in sorted(port_binds[port]):
                val = port_binds[port][suffix]
                lines.append(f'input_player{port}_{suffix} = "{val}"')
    return "\n".join(lines) + "\n" if lines else ""


_SENTINEL_RE = re.compile(
    re.escape(BEGIN) + r".*?" + re.escape(END) + r"\n?",
    re.DOTALL,
)


def _strip_block(text: str) -> str:
    return _SENTINEL_RE.sub("", text)


def _atomic_write(target: Path, content: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".router-tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(target)


def write_override(system: str, rom_basename: str,
                   port_names: dict[int, str],
                   mouse_indices: dict[int, int] | None = None,
                   port_binds: dict[int, dict[str, str]] | None = None,
                   ) -> list[Path]:
    """Write/refresh the router-managed sentinel block in each per-game
    override file under the system's core dirs.

    Returns the list of paths actually written. If the system has no
    configured cores (e.g. Daphne, MUGEN — non-RetroArch launches), returns
    an empty list and writes nothing.

    Atomic: tmp + rename in the same dir. Idempotent.
    """
    if not port_names and not mouse_indices and not port_binds:
        # Nothing to write — caller had no policy hits.
        return []

    block_body = _build_block(port_names, mouse_indices, port_binds)
    if not block_body:
        return []

    written: list[Path] = []
    for core_dir in core_dirs_for_system(system):
        target = core_dir / f"{rom_basename}.cfg"
        existing = target.read_text(encoding="utf-8") if target.exists() else ""
        cleaned = _strip_block(existing).rstrip("\n")
        block = f"{BEGIN}\n{block_body}{END}\n"
        if cleaned:
            merged = f"{cleaned}\n\n{block}"
        else:
            merged = block
        _atomic_write(target, merged)
        written.append(target)
    return written


def clear_override(system: str, rom_basename: str) -> list[Path]:
    """Strip the router-managed sentinel block from each per-game override.
    If the file is then empty (or comments-only), delete it.

    Returns the list of paths actually touched (stripped or deleted).
    """
    touched: list[Path] = []
    for core_dir in core_dirs_for_system(system):
        target = core_dir / f"{rom_basename}.cfg"
        if not target.exists():
            continue
        existing = target.read_text(encoding="utf-8")
        if BEGIN not in existing:
            continue  # nothing to do
        cleaned = _strip_block(existing).rstrip("\n")
        # Drop file if nothing meaningful left (only whitespace or comments)
        meaningful = any(
            line.strip() and not line.strip().startswith("#")
            for line in cleaned.splitlines()
        )
        if meaningful:
            _atomic_write(target, cleaned + "\n")
        elif any(line.strip() for line in cleaned.splitlines()):
            # Non-blank lines remain but none are real settings, so only USER COMMENTS are
            # left. That IS user data: MOVE to a recoverable _TMP (rule #5), never rm.
            fsutil.recoverable_delete(
                target, tmp_base=Path.home() / "Downloads" / "_TMP", tag="clear-override",
                recovery_note=f"Cleared router block from {target.name}; only comments remained.")
        else:
            # Nothing left at all: the file was a pure router-owned block (the common case,
            # rewritten fresh each launch for a game with no user cfg). Not user data, so plain
            # rm; otherwise we'd litter _TMP with a new dir on every RA game-end.
            target.unlink()
        touched.append(target)
    return touched


# ── MAD per-SYSTEM RetroArch options (Systems-page toggles) ──────────────────
# Distinct from the router's per-GAME block above. Written to the PER-CONTENT-
# DIRECTORY cfg `config/<Core>/<system>.cfg`, so it applies to every game of the
# system. Managed inside its own sentinel; preserves the bezel/overlay lines the
# bezel pipeline left there, and de-dups any pre-existing STANDALONE line for a
# managed key (e.g. the hand-added `video_driver = "glcore"` n64 fix).
SYS_BEGIN = "# >>> MAD system options (auto-managed) >>>"
SYS_END = "# <<< MAD system options end <<<"

_SYS_SENTINEL_RE = re.compile(
    re.escape(SYS_BEGIN) + r".*?" + re.escape(SYS_END) + r"\n?", re.DOTALL)


def _sys_managed(text: str) -> dict[str, str]:
    """The key→value pairs currently inside the MAD sentinel block."""
    m = _SYS_SENTINEL_RE.search(text)
    out: dict[str, str] = {}
    if not m:
        return out
    for line in m.group(0).splitlines():
        if line.strip().startswith("#"):
            continue
        mm = re.match(r'\s*(\w+)\s*=\s*"?([^"\n]*)"?\s*$', line)
        if mm:
            out[mm.group(1)] = mm.group(2)
    return out


def set_system_option(system: str, key: str, value: str | None) -> list[Path]:
    """Set (value) or clear (None) ONE RetroArch option for ALL of a system's
    cores, in `config/<Core>/<system>.cfg`. Idempotent + atomic; preserves
    unrelated lines; removes any standalone duplicate of the key so the managed
    value wins. Returns the cfg paths touched."""
    touched: list[Path] = []
    for core_dir in core_dirs_for_system(system):
        target = core_dir / f"{system}.cfg"
        existing = target.read_text(encoding="utf-8") if target.exists() else ""
        managed = _sys_managed(existing)
        body = _SYS_SENTINEL_RE.sub("", existing)
        drop = set(managed) | {key}
        body = "\n".join(
            ln for ln in body.splitlines()
            if not any(re.match(rf'\s*{re.escape(k)}\s*=', ln) for k in drop)
        ).rstrip("\n")
        if value is None:
            managed.pop(key, None)
        else:
            managed[key] = value
        parts = []
        if body:
            parts.append(body)
        if managed:
            block = "\n".join(f'{k} = "{v}"' for k, v in sorted(managed.items()))
            parts.append(f"{SYS_BEGIN}\n{block}\n{SYS_END}")
        new_text = ("\n\n".join(parts) + "\n") if parts else ""
        if new_text != existing:
            if new_text:
                _atomic_write(target, new_text)
            elif target.exists():
                target.unlink()
        touched.append(target)
    return touched


def get_system_option(system: str, key: str) -> str | None:
    """Effective value of `key` for the system (last occurrence wins, as RA
    layers it). Returns None if unset. Reads the first core cfg that has it."""
    for core_dir in core_dirs_for_system(system):
        target = core_dir / f"{system}.cfg"
        if not target.exists():
            continue
        val = None
        for ln in target.read_text(encoding="utf-8").splitlines():
            if ln.strip().startswith("#"):
                continue
            mm = re.match(rf'\s*{re.escape(key)}\s*=\s*"?([^"\n]*)"?\s*$', ln)
            if mm:
                val = mm.group(1)  # last wins
        if val is not None:
            return val
    return None


# ── MAD per-GAME RetroArch options (gameview per-game page) ──────────────────
# The THIRD independent sentinel block that can coexist in one per-game override
# `config/<Core>/<rom_basename>.cfg`, alongside the router reservation block
# (BEGIN/END) and the bezel-project overlay lines. Modeled on set_system_option
# but per-GAME (writes <rom_basename>.cfg, not <system>.cfg) and with its OWN
# distinct sentinel so each writer touches ONLY its own block: this writer strips
# and rewrites just the PG_* block, leaving the router block + bezel lines
# byte-for-byte intact (and, symmetrically, the router's write_override/
# clear_override strip only their BEGIN/END block, preserving this one).
PG_BEGIN = "# >>> MAD per-game options (auto-managed) >>>"
PG_END = "# <<< MAD per-game options end <<<"

_PG_SENTINEL_RE = re.compile(
    re.escape(PG_BEGIN) + r".*?" + re.escape(PG_END) + r"\n?", re.DOTALL)

# Flat "key = value" line shape shared by every non-sentinel-aware reader in
# this module (_pg_managed below, _sys_managed's own copy, get_global_option(s));
# named here so base_game_options can reuse it instead of yet another inline copy.
_KV_RE = re.compile(r'\s*(\w+)\s*=\s*"?([^"\n]*)"?\s*$')


def _pg_managed(text: str) -> dict[str, str]:
    """The key→value pairs currently inside the MAD per-game sentinel block."""
    m = _PG_SENTINEL_RE.search(text)
    out: dict[str, str] = {}
    if not m:
        return out
    for line in m.group(0).splitlines():
        if line.strip().startswith("#"):
            continue
        mm = _KV_RE.match(line)
        if mm:
            out[mm.group(1)] = mm.group(2)
    return out


def set_game_option(system: str, rom_basename: str,
                    key: str, value: str | None,
                    only_core: str | None = None) -> list[Path]:
    """Set (value) or clear (None) ONE MAD per-game RetroArch option in each of the
    system's core dirs, in `config/<Core>/<rom_basename>.cfg`. Idempotent + atomic.
    Touches ONLY the PG_* block: the router reservation block and the bezel overlay
    lines are left byte-for-byte unchanged (they live outside the block and are
    never scrubbed — the block, appended last, wins under RetroArch's last-line
    semantics). Returns the cfg paths touched. If the file is left empty, it is
    removed.

    `only_core` (a core-dir NAME) restricts the write to that ONE core dir — the
    per-core picker's per-core save; the default (None) multi-writes every core."""
    touched: list[Path] = []
    for core_dir in core_dirs_for_system(system):
        if only_core and core_dir.name != only_core:
            continue
        target = core_dir / f"{rom_basename}.cfg"
        existing = target.read_text(encoding="utf-8") if target.exists() else ""
        managed = _pg_managed(existing)
        if value is None:
            managed.pop(key, None)
        else:
            managed[key] = value
        # Strip our own block; keep EVERYTHING else (router block + bezel lines) intact.
        body = _PG_SENTINEL_RE.sub("", existing).rstrip("\n")
        parts = []
        if body:
            parts.append(body)
        if managed:
            block = "\n".join(f'{k} = "{v}"' for k, v in sorted(managed.items()))
            parts.append(f"{PG_BEGIN}\n{block}\n{PG_END}")
        new_text = ("\n\n".join(parts) + "\n") if parts else ""
        if new_text != existing:
            if new_text:
                _atomic_write(target, new_text)
            elif target.exists():
                target.unlink()
        touched.append(target)
    return touched


def get_game_options(system: str, rom_basename: str,
                     prefer_core: str | None = None,
                     only_core: str | None = None) -> dict[str, str]:
    """The MAD per-game options {key: value} for a game (from the first core cfg
    that carries a PG_* block — `prefer_core` puts the LAUNCHED core's cfg
    first, see launched_core()). {} if none is set.

    `only_core` (a core-dir NAME) ISOLATES the read to that ONE core: it returns
    that core's block or {} and NEVER falls through to another core's cfg — the
    per-core picker's read, so a picked core with no config of its own shows
    empty instead of a different core's overrides. Default (None) = fall-through."""
    for core_dir in core_dirs_for_system(system, prefer_core):
        if only_core and core_dir.name != only_core:
            continue
        target = core_dir / f"{rom_basename}.cfg"
        if not target.exists():
            continue
        text = target.read_text(encoding="utf-8")
        if PG_BEGIN in text:
            return _pg_managed(text)
    return {}


def has_game_overrides(system: str, rom_basename: str,
                       prefer_core: str | None = None,
                       only_core: str | None = None) -> bool:
    """True if any core cfg for the game carries a non-empty MAD per-game block."""
    return bool(get_game_options(system, rom_basename, prefer_core, only_core))


def base_game_options(system: str, rom_basename: str,
                      prefer_core: str | None = None,
                      only_core: str | None = None) -> dict[str, str]:
    """The STANDALONE key→value lines already living in a game's per-game
    override cfg — bezel-project overlay/aspect lines, RA-UI-saved "Game
    Overrides", or any other pre-existing content — with MAD's own PG_* block
    stripped out first. ~18,764 games on this rig carry a bare
    `aspect_ratio_index` line this way (the bezel pipeline), OUTSIDE the PG_*
    block get_game_options reads; this is how the per-game Settings editor
    (ragameset) shows the TRUE effective value instead of a misleading
    "Inherit global" for them ("layer on top" — see retroarch_game_cmds.py).

    Reads the FIRST existing core cfg (mirrors get_game_options' file
    precedence — `prefer_core` puts the LAUNCHED core first, see
    launched_core()) and parses with last-occurrence-wins (mirrors _pg_managed
    / RA's own read semantics — a later duplicate line wins). Purely a reader:
    never writes, and the PG block itself is never touched here. {} if no
    core cfg exists yet, or nothing is left once the PG block is stripped.

    `only_core` (a core-dir NAME) isolates the read to that one core (see
    get_game_options), so the per-core picker's display context matches its
    per-core PG read instead of falling through to a different core."""
    for core_dir in core_dirs_for_system(system, prefer_core):
        if only_core and core_dir.name != only_core:
            continue
        target = core_dir / f"{rom_basename}.cfg"
        if not target.exists():
            continue
        body = _PG_SENTINEL_RE.sub("", target.read_text(encoding="utf-8"))
        out: dict[str, str] = {}
        for line in body.splitlines():
            if line.strip().startswith("#"):
                continue
            mm = _KV_RE.match(line)
            if mm:
                out[mm.group(1)] = mm.group(2)   # last occurrence wins
        return out
    return {}


# ── global retroarch.cfg ──────────────────────────────────────────────────────
# The "configure RetroArch without desktop mode" surface. retroarch.cfg holds the
# GLOBAL defaults RA applies to every core; per-system overrides live in the
# config/<Core>/<system>.cfg files handled above. RA reads this file at startup
# and REWRITES THE WHOLE FILE on exit, so callers must refuse to write while it is
# running (use proc_guard.retroarch_running()).
RA_GLOBAL_CFG = RA_CONFIG_BASE.parent / "retroarch.cfg"
_GLOBAL_BAK = RA_CONFIG_BASE.parent / "retroarch.cfg.mad-bak"


def _ensure_global_bak(original: str) -> None:
    """One-time backup of retroarch.cfg before MAD's first edit — House rule #5:
    never clobber user data without a recoverable copy."""
    if original and not _GLOBAL_BAK.exists():
        try:
            _GLOBAL_BAK.write_text(original, encoding="utf-8")
        except OSError:
            pass


def get_global_option(key: str) -> str | None:
    """Effective value of `key` in the global retroarch.cfg (last line wins, the
    way RA reads it). None if the file or key is absent."""
    if not RA_GLOBAL_CFG.exists():
        return None
    val = None
    for ln in RA_GLOBAL_CFG.read_text(encoding="utf-8", errors="replace").splitlines():
        if ln.lstrip().startswith("#"):
            continue
        mm = re.match(rf'\s*{re.escape(key)}\s*=\s*"?([^"\n]*)"?\s*$', ln)
        if mm:
            val = mm.group(1)  # last wins
    return val


def get_global_options(keys) -> dict:
    """Read retroarch.cfg ONCE and return {key: value|None} for every requested
    key. Pages that need many keys (the input/keybindings page reads ~40) must use
    this instead of get_global_option per key, which re-reads the whole ~3000-line
    file each call."""
    result = {k: None for k in keys}
    if not RA_GLOBAL_CFG.exists():
        return result
    wanted = set(keys)
    for ln in RA_GLOBAL_CFG.read_text(encoding="utf-8", errors="replace").splitlines():
        if ln.lstrip().startswith("#"):
            continue
        mm = re.match(r'\s*(\w+)\s*=\s*"?([^"\n]*)"?\s*$', ln)
        if mm and mm.group(1) in wanted:
            result[mm.group(1)] = mm.group(2)  # last occurrence wins (as RA reads it)
    return result


def read_global_bak_options(keys) -> dict:
    """Read {key: value|None} for `keys` from the one-time retroarch.cfg.mad-bak -- the config as
    it was before MAD's FIRST edit (the original resting values). Used for corrupt-sidecar
    recovery so we restore the user's real binds instead of destroying them. Same grammar as
    get_global_options; None for a key the backup lacks."""
    result = {k: None for k in keys}
    if not _GLOBAL_BAK.exists():
        return result
    wanted = set(keys)
    for ln in _GLOBAL_BAK.read_text(encoding="utf-8", errors="replace").splitlines():
        if ln.lstrip().startswith("#"):
            continue
        mm = re.match(r'\s*(\w+)\s*=\s*"?([^"\n]*)"?\s*$', ln)
        if mm and mm.group(1) in wanted:
            result[mm.group(1)] = mm.group(2)
    return result


# System-hotkey mouse-button keys — a non-nul value = a hotkey is bound to a mouse
# button (e.g. the X-Arcade red button). Mirrors the "System hotkeys" group in
# madsrv/retroarch_cmds.py (the _mbtn variant of each).
RA_HOTKEY_MBTN_KEYS = (
    "input_enable_hotkey_mbtn", "input_menu_toggle_mbtn", "input_exit_emulator_mbtn",
    "input_save_state_mbtn", "input_load_state_mbtn", "input_toggle_fast_forward_mbtn",
    "input_rewind_mbtn", "input_screenshot_mbtn", "input_pause_toggle_mbtn",
    "input_state_slot_increase_mbtn", "input_state_slot_decrease_mbtn",
)


def ra_mouse_hotkey_bound() -> bool:
    """True if any RetroArch system hotkey is bound to a MOUSE button (non-nul *_mbtn)
    in the global cfg. The controller-router uses this to decide whether to pin
    player-1's mouse to the X-Arcade trackball (RA polls hotkeys on player-1's mouse
    only); the Preview page surfaces the resulting mouse assignment."""
    vals = get_global_options(list(RA_HOTKEY_MBTN_KEYS))
    return any(v not in (None, "", "nul") for v in vals.values())


def set_global_option(key: str, value: str) -> Path:
    """Set ONE key in the global retroarch.cfg in place, preserving every other
    line (the file is thousands of lines). Rewrites the LAST existing occurrence
    (so the effective value changes), or appends `key = "value"` if absent. Atomic;
    makes a one-time .mad-bak first. RetroArch must be CLOSED — it rewrites the
    whole file on exit."""
    text = (RA_GLOBAL_CFG.read_text(encoding="utf-8", errors="replace")
            if RA_GLOBAL_CFG.exists() else "")
    line = f'{key} = "{value}"'
    pat = re.compile(rf'^([^\S\n]*){re.escape(key)}[^\S\n]*=.*$', re.MULTILINE)
    matches = list(pat.finditer(text))
    if matches:
        m = matches[-1]                       # last wins, mirrors get_global_option
        new = text[:m.start()] + m.group(1) + line + text[m.end():]
    else:
        new = (text.rstrip("\n") + "\n" + line + "\n") if text else line + "\n"
    if new != text:
        _ensure_global_bak(text)
        _atomic_write(RA_GLOBAL_CFG, new)
    return RA_GLOBAL_CFG


def read_opt(opt_path: Path, key: str) -> str | None:
    """Read `key = "value"` (last occurrence wins) from a flat RetroArch .opt / .cfg core-options
    file. None if the file or key is absent. Same `key = "value"` grammar as retroarch.cfg."""
    if not opt_path.is_file():
        return None
    text = opt_path.read_text(encoding="utf-8", errors="replace")
    ms = list(re.finditer(rf'(?m)^[^\S\n]*{re.escape(key)}[^\S\n]*=[^\S\n]*"([^"]*)"[^\S\n]*$', text))
    return ms[-1].group(1) if ms else None


def write_opt(opt_path: Path, key: str, value: str) -> bool:
    """Byte-preserving set of `key = "value"` (last occurrence) in a flat .opt / .cfg core-options
    file. Rewrites ONLY if the key already EXISTS (never creates — an option we don't recognise is
    left untouched) and the value changed. One-time <file>.mad-bak. Returns True if rewritten."""
    if not opt_path.is_file():
        return False
    text = opt_path.read_text(encoding="utf-8", errors="replace")
    pat = re.compile(rf'(?m)^([^\S\n]*{re.escape(key)}[^\S\n]*=[^\S\n]*)"[^"]*"([^\S\n]*)$')
    ms = list(pat.finditer(text))
    if not ms:
        return False
    m = ms[-1]
    new = text[:m.start()] + m.group(1) + f'"{value}"' + m.group(2) + text[m.end():]
    if new == text:
        return False
    bak = opt_path.with_suffix(opt_path.suffix + ".mad-bak")
    if not bak.exists():
        try:
            bak.write_text(text, encoding="utf-8")
        except OSError:
            pass
    _atomic_write(opt_path, new)
    return True


def ensure_pergame_enabled(kinds) -> None:
    """RA silently IGNORES per-game override (.cfg) / remap (.rmp) files unless
    the matching global flag is on — a per-game write with these off is a file
    that's written but never loaded. Call once on the FIRST per-game write of
    each `kind` ("overrides" and/or "remaps") so a freshly-managed game's
    override actually takes effect:
      overrides -> auto_overrides_enable
      remaps    -> auto_remaps_enable + input_remap_binds_enable
    Best-effort / fail-soft: this touches the GLOBAL retroarch.cfg, so callers
    on the per-game write path (ragameset.save / ragamein.save) must already be
    guarded by proc_guard.retroarch_running() before reaching here; as a second
    line of defense this is a no-op while RA is running rather than raising, so
    a transient guard gap can never abort the per-game write that triggered it."""
    if proc_guard.retroarch_running():
        return
    want = set(kinds)
    if "overrides" in want and get_global_option("auto_overrides_enable") != "true":
        set_global_option("auto_overrides_enable", "true")
    if "remaps" in want:
        if get_global_option("auto_remaps_enable") != "true":
            set_global_option("auto_remaps_enable", "true")
        if get_global_option("input_remap_binds_enable") != "true":
            set_global_option("input_remap_binds_enable", "true")


if __name__ == "__main__":
    # Self-test: write, re-write (idempotent), then clear. Use a throwaway
    # path so we don't touch a real .cfg.
    import tempfile, sys
    tmpdir = Path(tempfile.mkdtemp(prefix="router-cfg-test-"))
    fake_core = tmpdir / "FakeCore"
    fake_core.mkdir()
    # Pretend a bezel-project file already exists
    existing_path = fake_core / "Test Game (USA).cfg"
    existing_path.write_text(
        "# bezelproject — auto-generated, safe to delete\n"
        "input_overlay = \"/path/to/overlay.cfg\"\n"
        "aspect_ratio_index = \"22\"\n"
    )

    # Monkey-patch the base path so write_override targets our tmp dir
    import lib.retroarch_cfg as rcfg
    rcfg.RA_CONFIG_BASE = tmpdir
    rcfg.SYSTEM_CORE_MAP = {"testsys": ["FakeCore"]}

    paths = rcfg.write_override("testsys", "Test Game (USA)", {
        1: "X-Arcade", 2: "DualSense",
    }, mouse_indices={1: 3, 2: 4})
    print(f"wrote {len(paths)} files")
    after = existing_path.read_text()
    print("--- after write ---")
    print(after)

    # Re-write should be idempotent
    rcfg.write_override("testsys", "Test Game (USA)", {
        1: "X-Arcade", 2: "DualSense",
    }, mouse_indices={1: 3, 2: 4})
    if existing_path.read_text() != after:
        sys.exit("FAIL: not idempotent")
    print("OK: idempotent")

    # Clear should strip our block and leave bezel content intact
    rcfg.clear_override("testsys", "Test Game (USA)")
    after_clear = existing_path.read_text()
    print("--- after clear ---")
    print(after_clear)
    assert "controller-router" not in after_clear
    assert "bezelproject" in after_clear
    assert "input_overlay" in after_clear
    print("OK: clear preserved bezel lines")

    # ── triple-block coexistence: router + bezel + MAD per-game, in ONE .cfg ──
    # Prove each writer touches ONLY its own block (the highest-risk Phase 3 bet).
    triple = fake_core / "Triple Game (USA).cfg"
    bezel_lines = ('input_overlay = "/path/to/overlay.cfg"\n'
                   'aspect_ratio_index = "22"\n')
    triple.write_text("# bezelproject — auto-generated\n" + bezel_lines)

    def _bezel_ok(txt):
        return ('input_overlay = "/path/to/overlay.cfg"' in txt
                and 'aspect_ratio_index = "22"' in txt)

    # 1) router block
    rcfg.write_override("testsys", "Triple Game (USA)", {1: "X-Arcade"})
    # 2) MAD per-game block (two keys)
    rcfg.set_game_option("testsys", "Triple Game (USA)", "video_smooth", "true")
    rcfg.set_game_option("testsys", "Triple Game (USA)", "menu_driver", "ozone")
    t = triple.read_text()
    assert BEGIN in t and END in t, "router block missing"
    assert PG_BEGIN in t and PG_END in t, "per-game block missing"
    assert _bezel_ok(t), "bezel lines missing"
    assert rcfg.get_game_options("testsys", "Triple Game (USA)") == {
        "video_smooth": "true", "menu_driver": "ozone"}
    assert rcfg.has_game_overrides("testsys", "Triple Game (USA)")
    print("OK: three blocks coexist")

    # 3) router re-write is idempotent AND preserves the per-game + bezel blocks
    router_block_before = _SENTINEL_RE.search(t).group(0)
    rcfg.write_override("testsys", "Triple Game (USA)", {1: "X-Arcade"})
    t2 = triple.read_text()
    assert PG_BEGIN in t2 and _bezel_ok(t2), "router write clobbered PG/bezel"
    assert 'video_smooth = "true"' in t2 and 'menu_driver = "ozone"' in t2

    # 4) per-game write preserves the router block byte-for-byte + bezel
    rcfg.set_game_option("testsys", "Triple Game (USA)", "video_smooth", "false")
    t3 = triple.read_text()
    assert _SENTINEL_RE.search(t3).group(0) == router_block_before, \
        "per-game write altered the router block"
    assert _bezel_ok(t3), "per-game write clobbered bezel"
    assert 'video_smooth = "false"' in t3

    # 5) router clear preserves the per-game block + bezel
    rcfg.clear_override("testsys", "Triple Game (USA)")
    t4 = triple.read_text()
    assert BEGIN not in t4, "router block not cleared"
    assert PG_BEGIN in t4 and _bezel_ok(t4), "router clear clobbered PG/bezel"

    # 6) clearing the per-game keys leaves bezel intact
    rcfg.set_game_option("testsys", "Triple Game (USA)", "video_smooth", None)
    rcfg.set_game_option("testsys", "Triple Game (USA)", "menu_driver", None)
    t5 = triple.read_text()
    assert PG_BEGIN not in t5, "per-game block not cleared"
    assert not rcfg.has_game_overrides("testsys", "Triple Game (USA)")
    assert _bezel_ok(t5), "per-game clear clobbered bezel"
    print("OK: triple-block round-trips preserve every other block")

    # Cleanup
    import shutil
    shutil.rmtree(tmpdir)
