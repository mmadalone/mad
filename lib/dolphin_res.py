"""Handheld internal-resolution downshift for GameCube / Wii (standalone Dolphin).

When the Deck is HANDHELD, Dolphin renders GameCube/Wii games at Native (1x) internal resolution
instead of their docked upscale, reverted on exit.

Dolphin layers config: a user per-game GameSettings/<GameID>.ini [Video_Settings] InternalResolution
OVERRIDES the global GFX.ini [Settings] InternalResolution. So we downshift the file the launching
game actually reads: the per-game override if it sets the key (which is exactly where the heavy,
user-tuned titles like Rogue Leader live), else the global GFX.ini. Missing this is why a global-only
downshift silently did nothing for the heaviest games (P3 review).

Own independent per-config atomic marker rail (own dir), swept at game-start (launch) AND game-end so
a crash orphan can never leave a later DOCKED game stuck at native res on the TV. Each marker records
the resting value AND the value we applied, so the revert is skipped if the user changed
InternalResolution in-Dolphin (revert-if-unchanged). InternalResolution is an enum index: 0=Auto,
1=Native(1x), 2=2x, ...; higher = more GPU. Only ever LOWERS, so Auto(0)/Native(1) are left untouched.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import deck_state, dolphin_gameids
from .madsrv import cfgutil
from .policy import load_merged

_GFX = Path.home() / ".var/app/org.DolphinEmu.dolphin-emu/config/dolphin-emu" / "GFX.ini"
_RES_DIR = Path.home() / "Emulation" / "storage" / "controller-router" / "dolphin-res"
_KEY, _NATIVE = "InternalResolution", "1"                 # 1 = Native / 1x


def _marker(path: Path) -> Path:
    slug = re.sub(r"[^A-Za-z0-9._-]", "_", str(path))
    return _RES_DIR / (slug + ".json")


def _effective(rom: str):
    """The (file, section) that actually governs InternalResolution for this game: the user's
    per-game GameSettings/<GameID>.ini [Video_Settings] if it SETS the key (it wins over the
    global), else the global GFX.ini [Settings]. None if neither exists / holds the key. GameID
    resolution is done here (after apply()'s cheap gates) so a docked launch never runs it."""
    try:
        gid = dolphin_gameids.gameid(rom)
        if gid:
            ug = dolphin_gameids.user_ini(gid)
            t = cfgutil.read_text(ug)
            if t is not None and cfgutil.ini_read(t, "Video_Settings", _KEY) is not None:
                return ug, "Video_Settings"
    except Exception:
        pass
    t = cfgutil.read_text(_GFX)
    if t is not None and cfgutil.ini_read(t, "Settings", _KEY) is not None:
        return _GFX, "Settings"
    return None


def sweep_all() -> None:
    """Revert every recorded Dolphin InternalResolution downshift to resting and drop the marker.
    Reverts ONLY if the file still holds the value we applied (else the user changed it in-Dolphin
    -- leave their edit). Idempotent + self-healing; run at launch-start and game-end."""
    try:
        markers = sorted(_RES_DIR.glob("*.json"))
    except OSError:
        return
    for mk in markers:
        try:
            d = json.loads(mk.read_text(encoding="utf-8"))
            path, section = Path(d["path"]), d["section"]
            text = cfgutil.read_text(path)
            if text is not None and cfgutil.ini_read(text, section, _KEY) == d.get("low"):
                new = cfgutil.ini_replace(text, section, _KEY, d.get("prev"))
                if new and new != text:
                    cfgutil.atomic_write(path, new)
        except Exception:
            pass
        try:
            mk.unlink()
        except OSError:
            pass


def apply(system: str, rom: str) -> None:
    """Downshift the launching GameCube/Wii game's internal resolution to native when handheld,
    on whichever file the game actually reads (per-game override or global). No-op unless the
    feature is enabled, we are HANDHELD, the system participates, res != 'inherit', and the current
    value is an upscale (>= 2x). Writes an atomic resting-value marker BEFORE lowering."""
    if system not in ("gc", "wii"):
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
    eff = _effective(rom)
    if eff is None:
        return
    path, section = eff
    text = cfgutil.read_text(path)
    if text is None:
        return
    prev = cfgutil.ini_read(text, section, _KEY)
    if prev is None:
        return
    try:                                       # only ever LOWER; Auto(0)/Native(1) already light
        if int(float(_NATIVE)) >= int(float(str(prev).strip())):
            return
    except (TypeError, ValueError):
        return
    try:
        _RES_DIR.mkdir(parents=True, exist_ok=True)
        mk = _marker(path)
        tmp = mk.with_name(mk.name + ".tmp")
        tmp.write_text(json.dumps({"path": str(path), "section": section,
                                   "prev": prev, "low": _NATIVE}), encoding="utf-8")
        tmp.replace(mk)                        # atomic: complete-or-absent
        new = cfgutil.ini_replace(text, section, _KEY, _NATIVE)
        if new and new != text:
            cfgutil.ensure_bak(path)
            cfgutil.atomic_write(path, new)
    except Exception:
        pass
