"""Handheld internal-resolution downshift for RetroArch heavy HW cores.

When the Deck is HANDHELD, the heavy GPU-bound libretro cores (Beetle PSX HW, Flycast,
Kronos, Mupen64Plus-Next) render at native internal resolution instead of their docked
upscale, reverted on exit. This mirrors the PS2/PS3 rail in switch_bind but for RetroArch,
which reaches its config a different way: core options live in flat `key = "value"` `.opt`
files, and the launch path is controller-router (not the mad-*-launch wrappers).

Its own independent marker rail (own dir), swept at RA launch-start AND game-end (in
controller-router `_setup` / `_cleanup`), so a crash orphan can never leave a later DOCKED
RA game stuck at the handheld-low resolution. Each marker (one per touched .opt) atomically
records {path, keys:{key:resting}}; the revert never depends on any other file parsing.

The target `.opt` is the per-content `<rom_basename>.opt` if RetroArch has one for the game
(it overrides), else the folder default `<Core>.opt`. Only ever LOWERS (never raises above a
value the user set below native). Beetle Saturn has NO hardware upscale (use Kronos);
ParaLLEl-N64 is skipped (Mupen64Plus-Next is the default N64 core).

Known limitation: if DURING a handheld session the user manually invokes RetroArch's Quick
Menu -> "Save Game Options" for a game that had only the folder <Core>.opt, RA writes a new
per-content <basename>.opt capturing the lowered value; the game-end sweep reverts the folder
marker but not that new file, so that one game reads native res while docked until its per-game
options are re-saved. Niche (a deliberate manual action mid-session), single-game, recoverable.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import deck_state, retroarch_cfg
from .policy import load_merged

_RES_DIR = Path.home() / "Emulation" / "storage" / "controller-router" / "ra-res"

# RetroArch config-dir core name -> [(option key, native/lowest value)]. native = the 1x floor,
# so a downshift can never go BELOW it. Mupen exposes separate 4:3 and 16:9 size keys.
_CORE_RES = {
    "Beetle PSX HW":    [("beetle_psx_hw_internal_resolution", "1x")],
    "Flycast":          [("reicast_internal_resolution", "640x480")],
    "Kronos":           [("kronos_resolution_mode", "1X")],
    "Mupen64Plus-Next": [("mupen64plus-43screensize", "640x480"),
                         ("mupen64plus-169screensize", "960x540")],
}


def _metric(v) -> float | None:
    """A comparable 'GPU cost' for an internal-res option value so we only ever LOWER:
    '2x' / '1X' -> the leading int; 'WxH' -> pixel area; a bare number -> itself. None if
    unparseable (then we skip, never risk raising)."""
    s = str(v).strip().strip('"')
    m = re.fullmatch(r"(\d+)\s*[xX]\s*(\d+)", s)          # "960x720"
    if m:
        return float(m.group(1)) * float(m.group(2))
    m = re.fullmatch(r"(\d+)\s*[xX]", s)                  # "2x", "1X"
    if m:
        return float(m.group(1))
    try:
        return float(s)
    except ValueError:
        return None


def _opt_file(core: str, rom_basename: str) -> Path | None:
    """The .opt the launching game actually reads: its per-content <basename>.opt if present
    (RetroArch loads it instead of the folder default), else the folder <Core>.opt."""
    base = retroarch_cfg.RA_CONFIG_BASE / core
    if not base.is_dir():
        return None
    per = base / f"{rom_basename}.opt"
    if per.is_file():
        return per
    folder = base / f"{core}.opt"
    return folder if folder.is_file() else None


def _marker(path: Path) -> Path:
    slug = re.sub(r"[^A-Za-z0-9._-]", "_", str(path))
    return _RES_DIR / (slug + ".json")


def sweep_all() -> None:
    """Revert every recorded RA-core res downshift to its resting value and drop the marker.
    Idempotent + self-healing; run at RA launch-start and game-end. A corrupt/unusable marker
    is dropped so it can never wedge the sweep."""
    try:
        markers = sorted(_RES_DIR.glob("*.json"))
    except OSError:
        return
    for mk in markers:
        try:
            d = json.loads(mk.read_text(encoding="utf-8"))
            path = Path(d["path"])
            for key, rec in (d.get("keys") or {}).items():
                if not isinstance(rec, dict):
                    continue
                # revert to resting ONLY if the file still holds the value we applied; if the user
                # changed it since (e.g. edited between an unclean exit and this sweep), leave it.
                if retroarch_cfg.read_opt(path, key) == rec.get("low"):
                    retroarch_cfg.write_opt(path, key, rec.get("prev"))
        except Exception:
            pass
        try:
            mk.unlink()
        except OSError:
            pass


def apply(system: str, rom_basename: str, core: str | None) -> None:
    """Downshift the launching game's heavy-core internal resolution when handheld. No-op unless
    the core is res-managed, the on-the-go feature is enabled, we are HANDHELD, the system
    participates, res != 'inherit', and the value is actually higher than native. Writes an
    atomic marker BEFORE lowering so the revert survives a crash."""
    if not core:
        return
    spec = _CORE_RES.get(core)
    if not spec:
        return
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
    if str(sys_hh.get("res", "native")).strip().lower() == "inherit":
        return
    path = _opt_file(core, rom_basename)
    if path is None:
        return
    recs = {}
    for key, native in spec:
        cur = retroarch_cfg.read_opt(path, key)
        if cur is None:
            continue
        mc, mn = _metric(cur), _metric(native)
        if mc is None or mn is None or mn >= mc:         # only ever LOWER
            continue
        recs[key] = {"prev": cur, "low": native}         # keep BOTH: revert can detect a user edit
    if not recs:
        return
    try:
        _RES_DIR.mkdir(parents=True, exist_ok=True)
        mk = _marker(path)
        tmp = mk.with_name(mk.name + ".tmp")
        tmp.write_text(json.dumps({"path": str(path), "keys": recs}), encoding="utf-8")
        tmp.replace(mk)                                  # atomic: complete-or-absent
        for key, rec in recs.items():
            retroarch_cfg.write_opt(path, key, rec["low"])
    except Exception:
        pass
