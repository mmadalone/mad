r"""cemu_pg_input.* - Cemu (Wii U) PER-GAME controller profiles.

Cemu's game profile ([Controller] controller1..controller8 in gameProfiles/<titleid>.ini) names a
saved controllerProfiles/<name>.xml to load into each port FOR THIS GAME, overriding Options > Input
Settings (applied at launch by InputManager::apply_game_profile). Unlike the Yuzu forks, Cemu
resolves the profile BY REFERENCE at boot - it does NOT bake bindings - so we only write the profile
NAME (no extension), exactly as Cemu's own game-profile editor does.

IMPORTANT: naming a profile here PINS that port's device for this game and BYPASSES the launch-time
controller-router assignment (lib/cemu_cfg.py) that normally clones the right pad template per port.
So index 0 = "Use router / global" (NO controllerN key) which is the DEFAULT and keeps the working
router in charge - set a profile only when you deliberately want a fixed device/profile for one game.
The named-profile list excludes the router-managed active slot files (controller0..controller7).
Instant save; refuses while Cemu runs (it rewrites profiles on exit).
"""
from __future__ import annotations

import re

from .. import proc_guard, staterev
from . import cemu_games, cfgutil
from . import yuzu_pergame as yp
from .rpc import RpcError, method

_PROC = "cemu"
_PORTS = 8                                        # [Controller] controller1..controller8 (1-based)
_ROUTER = "Use router / global"
_SLOTFILE_RE = re.compile(r"^controller[0-7]$")   # the generated active files, not named templates
_TID_RE = re.compile(r"^[0-9A-Fa-f]{16}$")
_SECTION = "Controller"


def _running() -> bool:
    return proc_guard.emulator_running(_PROC)


def _tid(params) -> str:
    t = (params.get("titleid") or "").strip()
    if not _TID_RE.match(t):
        raise RpcError("EINVAL", f"bad game id {t!r}")
    return t


def _profiles() -> list[str]:
    """Named controller profiles (controllerProfiles/*.xml) minus the router-managed active slot
    files controller0..controller7. Sorted case-insensitively."""
    try:
        return sorted((p.stem for p in cemu_games.controllerprofiles_dir().glob("*.xml")
                       if not _SLOTFILE_RE.match(p.stem)), key=str.lower)
    except OSError:
        return []


@method("cemu_pg_input.get", slow=True)
def _get(params):
    text, _crlf = cemu_games.read_ini(cemu_games.pergame_path(_tid(params)))
    text = text or ""
    profiles = _profiles()
    options = [_ROUTER] + profiles
    rows = []
    for n in range(1, _PORTS + 1):
        name = (cfgutil.ini_read(text, _SECTION, f"controller{n}") or "").strip()
        value = (profiles.index(name) + 1) if name in profiles else 0
        rows.append({"key": f"controller{n}", "label": f"Controller {n} profile",
                     "type": "enum", "options": options, "value": value})
    note = ("Assign a saved controller profile per port for THIS game. 'Use router / global' (default) "
            "lets the normal launch-time router pick the right pad - a named profile pins that port's "
            "device for this game and overrides the router." if profiles else
            "No saved controller profiles found in ~/.config/Cemu/controllerProfiles/. Create named "
            "profiles in Cemu's Input Settings to assign them per game here.")
    return {"exists": True, "running": _running(), "note": note,
            "groups": [{"title": "Per-game controller profiles", "note": "", "settings": rows}]}


@method("cemu_pg_input.set", slow=True)
def _set(params):
    if _running():
        raise RpcError("EBUSY", "close Cemu first - it rewrites game profiles on exit.")
    tid = _tid(params)
    key = params.get("key", "")
    m = re.fullmatch(r"controller([1-8])", key)
    if not m:
        raise RpcError("EINVAL", f"{key!r} is not a controller-profile selector")
    profiles = _profiles()
    try:
        idx = int(float(params.get("value")))
    except (TypeError, ValueError):
        raise RpcError("EINVAL", "pick a profile or 'Use router / global'")
    pg = cemu_games.pergame_path(tid)
    lf, crlf = cemu_games.read_ini(pg)
    text = lf or ""
    if idx <= 0:                                          # Use router / global -> remove the key
        new = cfgutil.ini_remove(text, _SECTION, key)
    else:
        if idx - 1 >= len(profiles):
            raise RpcError("EINVAL", "profile no longer exists")
        new = yp._ensure_section(text, _SECTION)         # append [Controller] if missing
        new = cfgutil.ini_set_or_insert(new, _SECTION, key, profiles[idx - 1]) or new
    if new != text:
        cemu_games.write_ini(pg, new, crlf)
        staterev.bump("config")
    return {"key": key, "value": idx if idx > 0 else 0}
