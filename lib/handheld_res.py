"""Backend-aware handheld internal-resolution rail.

When the Deck is HANDHELD, each launching game's internal/upscale resolution is dropped to a
user-chosen step and restored when docked. Unlike the old per-emulator rails this one DETECTS the
exact emulator the game actually launches with -- a specific RetroArch core (via
retroarch_cfg.launched_core, which honours the per-game <altemulator>) OR a standalone emulator
(via es_systems.standalone_backend_id) -- and writes the resolution into THAT emulator's config.
So a Saturn game launched with YabaSanshiro and another launched with Kronos each get the right knob.

The picker stores an abstract FACTOR (native/2x/3x/4x/6x/8x) per system; every backend snaps it DOWN
to its nearest real value (a core with no 3x rung uses 2x). This keeps one uniform picker even though
a single ES-DE system can launch through several backends whose value lists are mutually
incompatible. We only ever LOWER (never raise a game above its docked/resting value).

Own transient marker rail (own dir), swept at game-start AND game-end (unified hooks 09/11), so a
crash orphan can never leave a later DOCKED game at the handheld-low resolution. Each marker is
self-describing (backend + writer_kind + section + path + {key:{prev,low}}), so sweep_all needs no
registry. sweep_all also heals orphans left by the three superseded rails (ra_res / dolphin_res /
switch_bind._res_apply) for one release. Every error degrades to "leave the config alone; the launch
continues".
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from . import deck_state, es_systems, mad_paths, retroarch_cfg
from .madsrv import cfgutil
from .policy import load_merged

_DIR = mad_paths.storage("controller-router", "handheld-res")
_LOG_FILE = mad_paths.storage("controller-router", "router.log")

# policy res token -> multiplier factor. 'inherit' is handled before this map; an unknown token
# falls back to native (1) so a hand-edited policy never crashes the launch.
_TOKEN_FACTOR = {"native": 1, "2x": 2, "3x": 3, "4x": 4, "6x": 6, "8x": 8}


def _log(msg: str) -> None:
    try:
        _LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with _LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(f"handheld-res: {msg}\n")
    except Exception:
        pass


# ── ladder builders (each returns a KeySpec: how one option key maps factor->value + ranks it) ──
@dataclass(frozen=True)
class KeySpec:
    key: str
    native: str
    value_for_factor: Callable[[int], str]   # factor(1..8) -> the exact token to write
    rank: Callable[[str], Optional[float]]   # GPU-cost rank of ANY token (None = unparseable/foreign)


def enum_family(key: str, rungs, rank_order=None) -> KeySpec:
    """A fixed enum ladder. `rungs` = [(factor, token), ...] ascending -- the values we may WRITE
    (factor->token, snap-DOWN to the largest rung factor <= f). `rank_order` = the full token order
    low->high for the only-lower comparison (defaults to the rung tokens); pass it when the enum has
    high members we never target but must still rank above the rungs (YabaSanshiro 720p/1080p/4k)."""
    rungs = sorted(rungs, key=lambda p: p[0])
    order = list(rank_order) if rank_order else [t for _, t in rungs]
    rank_index = {t: i for i, t in enumerate(order)}
    native = rungs[0][1]

    def vff(f: int) -> str:
        chosen = rungs[0][1]
        for fac, tok in rungs:
            if fac <= f:
                chosen = tok
            else:
                break
        return chosen

    def rank(tok):
        return float(rank_index[tok]) if tok in rank_index else None

    return KeySpec(key, native, vff, rank)


def scalar_family(key: str, per_factor, fmt, native: str, cap=None) -> KeySpec:
    """An open-ended numeric scalar (Dolphin int / PCSX2 float / RPCS3 percent). value = fmt(cap?
    min(per_factor(f), cap) : per_factor(f)); rank = the number itself."""
    def vff(f: int) -> str:
        v = per_factor(f)
        if cap is not None:
            v = min(v, cap)
        return fmt(v)

    def rank(tok):
        try:
            return float(str(tok).strip())
        except (TypeError, ValueError):
            return None

    return KeySpec(key, native, vff, rank)


def wxh_family(key: str, native_w: int, native_h: int, members) -> KeySpec:
    """A WxH resolution list (Flycast / Mupen). value = the member whose WIDTH is the largest
    <= factor*native_w (snap-DOWN, clamped to the native member); rank = pixel area, which parses
    ANY WxH (even a docked value not in `members`)."""
    members = list(members)
    native = f"{native_w}x{native_h}"

    def _w(m: str) -> int:
        return int(m.split("x")[0])

    def vff(f: int) -> str:
        target = f * native_w
        chosen = members[0]
        for m in members:
            if _w(m) <= target:
                chosen = m
            else:
                break
        return chosen

    def rank(tok):
        m = re.fullmatch(r"(\d+)\s*[xX]\s*(\d+)", str(tok).strip())
        return float(m.group(1)) * float(m.group(2)) if m else None

    return KeySpec(key, native, vff, rank)


# Full on-device value lists (read from the core binaries; see deck-docs / the res-ladder scan).
_FLYCAST = ["320x240", "640x480", "800x600", "960x720", "1024x768", "1280x960", "1440x1080",
            "1600x1200", "1920x1440", "2560x1920", "2880x2160", "3200x2400", "3840x2880",
            "4480x3360", "5120x3840", "5760x4320", "6400x4800", "7040x5280", "7680x5760",
            "8320x6240", "8960x6720", "9600x7200"]
_MUPEN_43 = ["320x240", "640x480", "960x720", "1280x960", "1440x1080", "1600x1200", "1920x1440",
             "2240x1680", "2560x1920", "2880x2160", "3200x2400", "3520x2640", "3840x2880"]
# Mupen 16:9: the core interleaves ultrawide (64:27) families; keep only the standard 16:9 rungs.
_MUPEN_169 = ["640x360", "960x540", "1280x720", "1920x1080", "2560x1440", "3840x2160", "7680x4320"]


# ── backend registry ────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Backend:
    id: str
    writer_kind: str                              # "opt" | "ini" | "yaml"
    section: Optional[str]                        # ini/yaml section (None for opt, or target-provided)
    target: Optional[Callable]                    # (rom) -> Path | (Path, section) | None; None for opt
    keys: tuple


def _dolphin_target(rom: str):
    from . import dolphin_res
    return dolphin_res._effective(rom)            # (path, "Video_Settings"|"Settings") or None


def _dolphin_pergame_factor(rom: str):
    """The per-game Wii handheld resolution factor from `[backends.dolphin_wii.pergame.<GameID>].hhres`
    (a factor token like 'native'/'2x'), or None when unset / 'inherit' / unresolvable / unrecognized.
    Reuses the launch decider's EXACT GameID resolution so the stored id matches. When set, it overrides
    the per-system token but rides the same _effective target + marker/revert machinery -- so it is
    transient (reverts on game-end) and never upscales (the downshift-only rule in _consider still holds)."""
    try:
        from . import dolphin_wii_tdb
        gid = dolphin_wii_tdb._resolve(rom)
        if not gid:
            return None
        be = (load_merged().get("backends") or {}).get("dolphin_wii") or {}
        pg = (be.get("pergame") or {}).get(gid)
        tok = str(pg.get("hhres")).strip().lower() if isinstance(pg, dict) and pg.get("hhres") else ""
        if not tok or tok == "inherit":
            return None
        return _TOKEN_FACTOR.get(tok)             # None if unrecognized -> ignore (fall back to per-system)
    except Exception:
        return None


def _pcsx2_target(rom: str):
    from . import switch_bind
    return switch_bind._pcsx2_res_file(rom)


def _rpcs3_target(rom: str):
    from . import switch_bind
    return switch_bind._rpcs3_res_file(rom)


REGISTRY = {
    # RetroArch cores (target = the launched core's .opt; keyed by the core-display-name that
    # launched_core returns, which reconciles to the on-disk config dir).
    "Beetle PSX HW": Backend("Beetle PSX HW", "opt", None, None, (
        enum_family("beetle_psx_hw_internal_resolution",
                    [(1, "1x(native)"), (2, "2x"), (4, "4x"), (8, "8x"), (16, "16x")]),)),
    "Flycast": Backend("Flycast", "opt", None, None, (
        wxh_family("reicast_internal_resolution", 640, 480, _FLYCAST),)),
    "Kronos": Backend("Kronos", "opt", None, None, (
        enum_family("kronos_resolution_mode",
                    [(1, "original"), (2, "2X"), (4, "4X"), (8, "8X")]),)),
    "YabaSanshiro": Backend("YabaSanshiro", "opt", None, None, (
        enum_family("yabasanshiro_resolution_mode",
                    [(1, "original"), (2, "2x"), (4, "4x")],
                    rank_order=["original", "2x", "4x", "720p", "1080p", "4k"]),)),
    "SwanStation": Backend("SwanStation", "opt", None, None, (
        scalar_family("duckstation_GPU.ResolutionScale", lambda f: f, str, "1", cap=16),)),
    "Mupen64Plus-Next": Backend("Mupen64Plus-Next", "opt", None, None, (
        wxh_family("mupen64plus-43screensize", 640, 480, _MUPEN_43),
        wxh_family("mupen64plus-169screensize", 640, 360, _MUPEN_169),)),
    # Standalone emulators (target = the config the game actually reads).
    "dolphin": Backend("dolphin", "ini", None, lambda r: _dolphin_target(r), (
        scalar_family("InternalResolution", lambda f: f, str, "1"),)),
    "pcsx2": Backend("pcsx2", "ini", "EmuCore/GS", lambda r: _pcsx2_target(r), (
        scalar_family("upscale_multiplier", lambda f: float(f), cfgutil.fmt_float, "1", cap=12.0),)),
    "rpcs3": Backend("rpcs3", "yaml", "Video", lambda r: _rpcs3_target(r), (
        scalar_family("Resolution Scale", lambda f: f * 100, str, "100", cap=800),)),
}


# --- public: resolution picker labels (WS-H) ---
# Turn the abstract factor token into the resolution the SYSTEM's configured backend actually renders,
# deduped across factors that snap to the same value, labeled in each emulator's OWN honest style
# (exact WxH where the base is fixed; the emulator's own multiplier/percent where it varies per game).
# Same REGISTRY + value_for_factor as the rail, so the label can never disagree with what launches.
_PICKER_FACTORS = (1, 2, 3, 4, 6, 8)
_FACTOR_TOKEN = {1: "native", 2: "2x", 3: "3x", 4: "4x", 6: "6x", 8: "8x"}
_INHERIT = ("inherit", "Inherit (leave as-is)")
# Fallback ladder for a system with no resolution-registered backend (abstract, like before).
_ABSTRACT_CHOICES = [("native", "Native (1x)"), ("2x", "2x"), ("3x", "3x"), ("4x", "4x"),
                     ("6x", "6x"), ("8x", "8x"), _INHERIT]
# PCSX2's OWN vertical-pixel hints (base varies per game), keyed by the whole-number multiplier.
_PCSX2_LABEL = {1: "Native", 2: "2x Native (~720px)", 3: "3x Native (~1080px)",
                4: "4x Native (~1440px)", 6: "6x Native (~2160px)", 8: "8x Native (~2880px)"}
# Multiplier-only enums (variable base -> no honest WxH): the written token -> "Native/Nx".
_MULT_LABEL = {"1x(native)": "Native", "2x": "2x", "4x": "4x", "8x": "8x", "16x": "16x",
               "original": "Native", "2X": "2x", "4X": "4x", "8X": "8x"}


def _res_label(backend_id: str, value: str) -> str:
    """The honest label for one backend's config VALUE (the string value_for_factor writes)."""
    if backend_id in ("Flycast", "Mupen64Plus-Next"):
        return value                                     # value IS a WxH -> exact, show it literally
    if backend_id == "dolphin":                          # fixed 640x528 base -> exact WxH
        n = int(value)
        return "Native (640x528)" if n == 1 else f"{n}x ({640 * n}x{528 * n})"
    if backend_id == "rpcs3":                             # percent of a 720p base
        pct = int(value)
        return f"{720 * pct // 100}p ({pct}%)"
    if backend_id == "pcsx2":
        return _PCSX2_LABEL.get(int(float(value)), f"{value}x Native")
    if backend_id == "SwanStation":                      # 1..16 scale, variable base -> multiplier
        n = int(value)
        return "Native" if n == 1 else f"{n}x"
    return _MULT_LABEL.get(value, value)                 # Beetle PSX HW / Kronos / YabaSanshiro enums


def _label_key(backend):
    """The key to build labels + dedupe from: the 16:9 rung for Mupen (its last key), else the only key."""
    return backend.keys[-1]


def _render_backend(system: str):
    """The Backend the SYSTEM is configured to launch with (auto-detect: the default RA core, else the
    default standalone), or None if it isn't resolution-registered. Mirrors apply()'s resolution with
    no specific game (stem="") -> the system's default emulator/core."""
    try:
        core = retroarch_cfg.launched_core(system, "")
    except Exception:
        core = None
    bid = core
    if bid is None:
        try:
            bid = es_systems.standalone_backend_id(es_systems.resolved_command(system, ""))
        except Exception:
            bid = None
    return REGISTRY.get(bid)


def resolution_choices(system: str):
    """[(token, label)] for the system's Resolution picker: the real resolutions its configured backend
    renders, DEDUPED (rungs that snap to the same value collapse to the lowest), each labeled in the
    emulator's honest style, + Inherit. Falls back to the abstract ladder if the backend is unresolved
    or not resolution-registered."""
    backend = _render_backend(system)
    if backend is None:
        return list(_ABSTRACT_CHOICES)
    ks = _label_key(backend)
    seen: set = set()
    out = []
    for f in _PICKER_FACTORS:
        value = ks.value_for_factor(f)
        if value in seen:
            continue
        seen.add(value)
        out.append((_FACTOR_TOKEN[f], _res_label(backend.id, value)))
    out.append(_INHERIT)
    return out


def snap_token(system: str, token: str) -> str:
    """The canonical deduped token a STORED token maps to (via its snapped value), so a pre-existing
    '3x' selects the '2x' row it actually renders. 'inherit' / unresolved pass through."""
    token = (token or "").strip().lower()
    if token == "inherit":
        return "inherit"
    backend = _render_backend(system)
    if backend is None:
        return token if token in _TOKEN_FACTOR else "native"
    ks = _label_key(backend)
    value = ks.value_for_factor(_TOKEN_FACTOR.get(token, 1))
    for f in _PICKER_FACTORS:                            # the lowest factor producing this value
        if ks.value_for_factor(f) == value:
            return _FACTOR_TOKEN[f]
    return "native"


# ── read/write helpers per writer_kind ──────────────────────────────────────
def _reader(kind):
    return cfgutil.ini_read if kind == "ini" else cfgutil.yaml_read


def _writer(kind):
    return cfgutil.ini_replace if kind == "ini" else cfgutil.yaml_replace


def _marker(path: Path) -> Path:
    slug = re.sub(r"[^A-Za-z0-9._-]", "_", str(path))
    return _DIR / (slug + ".json")


def _consider(recs: dict, ks: KeySpec, cur, factor: int) -> None:
    """Record a downshift for one key IFF the target is strictly LOWER than the current value."""
    if cur is None:
        return
    tok = ks.value_for_factor(factor)
    rt, rc = ks.rank(tok), ks.rank(cur)
    if rt is None or rc is None or rt >= rc:      # only ever LOWER; unparseable -> never touch
        return
    recs[ks.key] = {"prev": cur, "low": tok}


# ── public: apply / sweep ────────────────────────────────────────────────────
def apply(system: str, rom: str) -> None:
    """Downshift the launching game's resolution to the per-system handheld factor, on whichever
    emulator it actually launches with. No-op unless: feature enabled, HANDHELD, the system
    participates, res != 'inherit', the launched backend is in the registry, and the target value
    is actually lower than the resting value. Writes an atomic marker BEFORE mutating."""
    try:
        pol = load_merged()
    except Exception:
        return
    hh = pol.get("handheld") if isinstance(pol, dict) else None
    if not (isinstance(hh, dict) and hh.get("enabled", False)):
        return
    try:
        if not deck_state.is_handheld(deck_state.resolve_force(hh)):
            return
    except Exception:
        return
    systems = pol.get("systems")
    sysd = systems.get(system) if isinstance(systems, dict) else None
    sys_hh = sysd.get("handheld") if isinstance(sysd, dict) else None
    if not (isinstance(sys_hh, dict) and sys_hh.get("enabled", False)):
        return
    token = str(sys_hh.get("res", "native")).strip().lower()
    if token == "inherit" and system != "wii":
        return                                   # non-Wii fast path; Wii may still carry a per-game res

    # ES-DE passes hooks backslash-escaped paths; strip ONCE so both the gamelist stem lookup and
    # the standalone target resolvers (which stat/realpath the file to find a per-game config) see
    # the real path -- otherwise a spaced filename silently falls back to the global config.
    try:
        from .classify import _strip_escapes
        rom = _strip_escapes(rom)
    except Exception:
        pass
    # Backend resolution reads XML/gamelist, so it runs only AFTER the cheap gates above.
    stem = Path(rom).stem
    try:
        core = retroarch_cfg.launched_core(system, stem)
    except Exception:
        core = None
    if core is not None:
        backend_id = core
    else:
        try:
            backend_id = es_systems.standalone_backend_id(es_systems.resolved_command(system, stem))
        except Exception:
            backend_id = None
    entry = REGISTRY.get(backend_id)
    if entry is None:
        _log(f"unsupported backend {backend_id!r} (system={system}, game={stem!r}) -- leaving resolution alone")
        return

    # Dolphin Wii: a PER-GAME handheld resolution (`[backends.dolphin_wii.pergame.<id>].hhres`) overrides
    # the per-system token, and applies even when the per-system res is 'inherit'. It uses the SAME
    # _effective target + marker machinery -- only the factor differs -- so it stays transient (reverts
    # on game-end) and never upscales (the downshift-only rule in _consider still holds).
    pergame_factor = _dolphin_pergame_factor(rom) if (entry.id == "dolphin" and system == "wii") else None
    if pergame_factor is not None:
        factor = pergame_factor
    elif token == "inherit":
        return                                   # Wii, per-system inherit, no per-game override -> nothing
    else:
        factor = _TOKEN_FACTOR.get(token, 1)

    # Resolve the target file (+ read current values) -- wrapped so a bad path / parse can never
    # escape to the caller. This is entirely pre-mutation, so returning here leaves every config
    # untouched (the hooks force exit 0, but a non-hook caller / unit test must be safe too).
    text = None
    recs: dict = {}
    try:
        if entry.writer_kind == "opt":
            from . import ra_res
            path, section = ra_res._opt_file(entry.id, stem), None
        else:
            tgt = entry.target(rom) if entry.target else None
            if tgt is None:
                return
            path, section = tgt if isinstance(tgt, tuple) else (tgt, entry.section)
        if path is None:
            return
        if entry.writer_kind == "opt":
            for ks in entry.keys:
                _consider(recs, ks, retroarch_cfg.read_opt(path, ks.key), factor)
        else:
            text = cfgutil.read_text(path)
            if text is None:
                return
            reader = _reader(entry.writer_kind)
            for ks in entry.keys:
                _consider(recs, ks, reader(text, section, ks.key), factor)
    except Exception as e:
        _log(f"target/read failed ({e!r}) -- leaving resolution alone")
        return
    if not recs:
        return

    # Atomic self-describing marker BEFORE mutation, so the revert survives a crash.
    marker = {"backend": entry.id, "writer_kind": entry.writer_kind, "section": section,
              "path": str(path), "keys": recs}
    try:
        _DIR.mkdir(parents=True, exist_ok=True)
        mk = _marker(path)
        tmp = mk.with_name(mk.name + ".tmp")
        tmp.write_text(json.dumps(marker), encoding="utf-8")
        tmp.replace(mk)
    except Exception as e:
        _log(f"marker write failed ({e!r})")
        return

    try:
        if entry.writer_kind == "opt":
            for key, rec in recs.items():
                retroarch_cfg.write_opt(path, key, rec["low"])
        else:
            writer = _writer(entry.writer_kind)
            new = text
            for key, rec in recs.items():
                nt = writer(new, section, key, rec["low"])
                if nt and nt != new:
                    new = nt
            if new != text:
                cfgutil.ensure_bak(path)
                cfgutil.atomic_write(path, new)
        _log(f"{entry.id}: {system}/{stem} factor {factor} :: " +
             ", ".join(f"{k} {r['prev']}->{r['low']}" for k, r in recs.items()))
    except Exception as e:
        _log(f"apply write failed ({e!r})")


def _revert_marker(mk: Path) -> bool:
    """Revert one marker (only where the file still holds the value we applied) and report whether
    it should be KEPT (an I/O write failure -> keep for the next sweep). Malformed -> dropped."""
    keep = False
    try:
        d = json.loads(mk.read_text(encoding="utf-8"))
        kind, path, section = d.get("writer_kind"), Path(d["path"]), d.get("section")
        keys = d.get("keys") or {}
        if kind == "opt":
            for key, rec in keys.items():
                if isinstance(rec, dict) and retroarch_cfg.read_opt(path, key) == rec.get("low"):
                    try:
                        retroarch_cfg.write_opt(path, key, rec.get("prev"))
                    except Exception:
                        keep = True                   # I/O failure -> keep the marker, retry later
        else:
            text = cfgutil.read_text(path)
            if text is not None:
                reader, writer = _reader(kind), _writer(kind)
                new = text
                for key, rec in keys.items():
                    if isinstance(rec, dict) and reader(new, section, key) == rec.get("low"):
                        nt = writer(new, section, key, rec.get("prev"))
                        if nt and nt != new:
                            new = nt
                if new != text:
                    try:
                        cfgutil.atomic_write(path, new)
                    except Exception:
                        keep = True                   # I/O failure -> keep the marker, retry later
    except Exception:
        pass                                          # unreadable/malformed -> drop it
    return keep


def sweep_all() -> None:
    """Revert every recorded downshift to resting (revert-if-unchanged; leaves a mid-session user
    edit alone) and drop its marker. Also heals orphans from the three superseded rails for one
    release. Idempotent + self-healing; safe at game-start and game-end."""
    try:
        markers = sorted(_DIR.glob("*.json"))
    except OSError:
        markers = []
    for mk in markers:
        if not _revert_marker(mk):
            try:
                mk.unlink()
            except OSError:
                pass
    # Transitional: heal an orphan written by the old code just before this update.
    for imp in ("ra_res", "dolphin_res"):
        try:
            mod = __import__(f"lib.{imp}", fromlist=["sweep_all"])
            mod.sweep_all()
        except Exception:
            pass
    try:
        from . import switch_bind
        switch_bind._res_sweep_all()
    except Exception:
        pass
