r"""eden_pg_input.* - Eden (Switch) PER-GAME Input Profiles.

One selector per player (Player 1..8): "Use global input configuration" or a named profile from
~/.config/eden/input/*.ini. Stored in custom/<TITLEID>.ini's [Controls] as `player_N_profile_name`.

CRITICAL (Yuzu-fork behaviour, shared with Citron): a per-game profile is NOT read from
input/<name>.ini at boot -- the fork BAKES the resolved bindings inline into the per-game ini at
save time and reads that snapshot. So selecting a profile here writes `player_N_profile_name="<name>"`
AND copies the profile's `player_N_button_*`/stick/motion bindings in (each with its \default=false
twin). Writing only the name would drop that player to KEYBOARD at boot. "Use global" removes the
player's keys (absent = inherit the global config). The baked bindings keep the profile's OWN device
(guid/port), which is exactly the intended per-game device PIN that bypasses MAD's pads->players
routing (see memory switch-per-game-profile-routing).

Rendered per game by GuiMadPageEmuSettings (enum rows). Writes refuse while Eden runs.
"""
from __future__ import annotations

import re
from pathlib import Path

from .. import eden_cfg, inifile, proc_guard, staterev
from . import cfgutil, eden_cmds
from .rpc import RpcError, method

_INPUT_DIR = Path.home() / ".config/eden/input"
_PROC = "eden"
_PLAYERS = 8                                          # Player 1..8 = player_0..player_7
_TID_RE = re.compile(r"^[0-9A-Fa-f]{16}$")
_SPECIAL = re.compile(r"[^A-Za-z0-9_.-]")
_GLOBAL = "Use global input configuration"


def _running() -> bool:
    return proc_guard.emulator_running(_PROC)


def _tid(params) -> str:
    t = params.get("titleid") or ""
    if not _TID_RE.match(t):
        raise RpcError("EINVAL", f"bad game id {t!r}")
    return t


def _quote(v: str) -> str:
    return f'"{v}"' if (v == "" or _SPECIAL.search(v)) else v


def _profiles() -> list[str]:
    try:
        return sorted((p.stem for p in _INPUT_DIR.glob("*.ini")), key=str.lower)
    except OSError:
        return []


def _profile_name(body: str, n: int) -> str:
    """The player's selected per-game profile name ('' = use global)."""
    raw = cfgutil.ini_read("[Controls]\n" + body, "Controls", f"player_{n}_profile_name")
    if raw is None:
        return ""
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"':
        raw = raw[1:-1]
    return raw


def _clear_player(body: str, n: int) -> str:
    """Remove every player_{n}_* line (bindings + \\default twins + profile_name) -> inherit global."""
    pref = f"player_{n}_"
    return "\n".join(ln for ln in body.splitlines() if not ln.lstrip().startswith(pref))


def _bake(body: str, n: int, name: str) -> str:
    """Write player_{n}_profile_name + the profile's resolved inline bindings (kept verbatim, so the
    profile's own device is pinned) into the [Controls] body, each with its \\default=false twin."""
    prof = _INPUT_DIR / f"{name}.ini"
    if not prof.is_file():
        raise RpcError("ENOENT", f"input profile {name!r} not found")
    body = _clear_player(body, n)                     # replace any existing selection cleanly
    ov = dict(eden_cfg._template_bindings(prof))      # {button_a: '"engine:sdl,..."', ...}
    ov["profile_name"] = _quote(name)
    # A per-game override player STOPS inheriting from global once profile_name is set, so it must
    # carry connected/type itself -- else the fork boots Players 2-8 disconnected (their default) and
    # the device PIN silently does nothing. Mirrors eden_cfg.assign()/assign_devices().
    ov["connected"] = "true"
    ov["type"] = "0"
    return eden_cfg._apply_player(body, n, ov)


@method("eden_pg_input.get", slow=True)
def _get(params):
    tid = _tid(params)
    pg_text = cfgutil.read_text(eden_cmds.pergame_path(tid)) or ""
    body = inifile.section_body(pg_text, "Controls") or ""
    profiles = _profiles()
    options = [_GLOBAL] + profiles
    rows = []
    for n in range(_PLAYERS):
        name = _profile_name(body, n)
        value = (profiles.index(name) + 1) if name in profiles else 0
        rows.append({"key": f"player_{n}", "label": f"Player {n + 1} profile",
                     "type": "enum", "options": options, "value": value})
    note = ("Pick a named input profile per player, or 'Use global input configuration' to route "
            "the player normally. A named profile PINS that player's device for this game "
            "(overrides pads -> players)." if profiles else
            "No input profiles found. Create named profiles in Eden's Controls dialog "
            "(saved under ~/.config/eden/input/) to assign them per game here.")
    return {"exists": True, "running": _running(), "note": note,
            "groups": [{"title": "Input Profiles", "note": "", "settings": rows}]}


@method("eden_pg_input.set", slow=True)
def _set(params):
    if _running():
        raise RpcError("EBUSY", "close Eden first - it rewrites its config on exit.")
    tid = _tid(params)
    key = params.get("key", "")
    m = re.fullmatch(r"player_([0-7])", key)
    if not m:
        raise RpcError("EINVAL", f"{key!r} is not a player profile selector")
    n = int(m.group(1))
    profiles = _profiles()
    try:
        idx = int(float(params.get("value")))
    except (TypeError, ValueError):
        raise RpcError("EINVAL", "pick a profile or 'Use global input configuration'")
    pg = eden_cmds.pergame_path(tid)
    text = cfgutil.read_text(pg) or ""
    body = inifile.section_body(text, "Controls") or ""
    if idx <= 0:                                       # Use global -> clear this player
        new_body = _clear_player(body, n)
    else:
        if idx - 1 >= len(profiles):
            raise RpcError("EINVAL", "profile no longer exists")
        new_body = _bake(body, n, profiles[idx - 1])
    if inifile.section_body(text, "Controls") is not None:
        new_text = inifile.set_section(text, "Controls", new_body)
    elif new_body:                                     # only create [Controls] if there is something to write
        new_text = (text + ("" if not text or text.endswith("\n") else "\n")
                    + "[Controls]\n" + new_body + ("\n" if not new_body.endswith("\n") else ""))
    else:                                              # Use-global on a game with no per-game ini -> no-op:
        new_text = text                                # don't create an otherwise-nonexistent empty-[Controls] file
    if new_text != text:
        pg.parent.mkdir(parents=True, exist_ok=True)
        cfgutil.ensure_bak(pg)
        cfgutil.atomic_write(pg, new_text)
        staterev.bump("config")
    return {"key": key, "value": idx if idx > 0 else 0}
