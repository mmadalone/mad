"""cemures.* -- MAD On-the-go per-game HANDHELD RESOLUTION for Cemu (Wii U).

For a Wii U game that has an ENABLED resolution graphic pack, the launch rail (lib/cemu_res.py)
switches the pack HANDHELD and restores the resting preset on exit. The handheld DEFAULT is a 720p
CAP (auto-applied only when it LOWERS the game's resting resolution -- the nearest option at or below
720p; a game already at/below 720p is left unchanged, never upscaled); pick another preset to
override, or 'Keep' to leave a game unchanged. The list is DYNAMIC -- any game with a resolution
pack appears automatically (via cemu_packs_cmds.resolution_titleids).

Storage: policy [systems.wiiu.handheld.res_presets].<titleid> = a preset name, or cp.KEEP for an
explicit "leave as-is"; ABSENT = unset = the 720p default.

Pages (the `settings_pergame` browser: game picker -> a settings page per game, no submenu):
  cemures.games -> {games:[...only titles with an enabled resolution pack...], system:"wiiu"}
  cemures.get   -> {exists, note, groups:[{settings:[ enum: Keep + the pack's presets ]}]}
  cemures.set   -> persist the choice (idx 0 = Keep -> store cp.KEEP; idx N -> the Nth preset)
"""
from __future__ import annotations

import re

from .. import proc_guard
from . import cemu_games, cemu_packs_cmds as cp
from .rpc import RpcError, method

_KEEP = "Keep (no change)"
_TID_RE = re.compile(r"^[0-9A-Fa-f]{16}$")


def _tid(params) -> str:
    t = (params.get("titleid") or "").strip().lower()
    if not _TID_RE.match(t):
        raise RpcError("EINVAL", f"bad game id {t!r}")
    return t


def _load_presets() -> dict:
    """policy [systems.wiiu.handheld.res_presets] -> {titleid: preset name}."""
    from ..policy import load_merged
    m = load_merged()
    systems = m.get("systems") if isinstance(m, dict) else None
    wiiu = systems.get("wiiu") if isinstance(systems, dict) else None
    hh = wiiu.get("handheld") if isinstance(wiiu, dict) else None
    rp = hh.get("res_presets") if isinstance(hh, dict) else None
    return rp if isinstance(rp, dict) else {}


def _store(tid: str, preset) -> None:
    """Set/clear the per-title handheld preset in controller-policy.local.toml (atomic +
    staterev bump via localpolicy.dump). preset=None clears the override."""
    from .. import localpolicy
    from ..policy import LOCAL
    data = localpolicy.load(LOCAL)
    blk = data
    for k in ("systems", "wiiu", "handheld", "res_presets"):
        blk = blk.setdefault(k, {})
    if preset is None:
        blk.pop(tid, None)
    else:
        blk[tid] = preset
    localpolicy.dump(LOCAL, data)


@method("cemures.games", slow=True)
def _games(params):
    res = cp.resolution_titleids()                      # DYNAMIC: titles with an enabled res pack
    stored = _load_presets()

    def _summary(tid):
        p = stored.get(tid)
        if not p:
            return ""
        return "Handheld: Keep" if p == cp.KEEP else f"Handheld: {cp._strip_default_tag(p)}"

    rows = [g for g in cemu_games.listing(override_fn=lambda t: t in stored, summary_fn=_summary)
            if g["titleid"] in res]
    return {"games": rows, "system": cemu_games._ESDE_SYSTEM}


@method("cemures.get", slow=True)
def _pg_get(params):
    tid = _tid(params)
    info = cp.resolution_titleids().get(tid)
    running = proc_guard.emulator_running("cemu")
    if not info:
        return {"exists": True, "running": running, "groups": [],
                "note": "This game no longer has an enabled resolution graphic pack."}
    presets = info["presets"]
    stored = _load_presets().get(tid)
    opts = [_KEEP] + [cp._strip_default_tag(n) for n in presets]
    if stored == cp.KEEP:
        val = 0                                          # explicit Keep -> leave as-is
    elif stored in presets:
        val = presets.index(stored) + 1                  # an explicit preset override
    else:                                                # unset or stale -> the effective downshift default
        eff = cp.downshift_target(info)                  # 720p cap, only when it lowers the resting res
        val = (presets.index(eff) + 1) if eff in presets else 0
    note = ("Handheld resolution for this game (via its resolution graphic pack). Defaults to 720p "
            "when the game renders higher (down to the nearest option at or below it); a game already "
            "at or below 720p is left unchanged. Pick another to override, or 'Keep'. Your docked "
            "preset returns automatically on exit.")
    return {"exists": True, "running": running, "note": note,
            "groups": [{"title": "Handheld resolution", "note": "", "settings": [
                {"key": "preset", "label": "Handheld resolution", "type": "enum",
                 "options": opts, "value": val, "picker": True}]}]}   # WS-H: always the full list


@method("cemures.set", slow=True)
def _pg_set(params):
    tid = _tid(params)
    if params.get("key") != "preset":
        raise RpcError("EINVAL", f"unknown key {params.get('key')!r}")
    info = cp.resolution_titleids().get(tid)
    presets = info["presets"] if info else []
    try:
        idx = int(float(params.get("value")))
    except (TypeError, ValueError):
        raise RpcError("EINVAL", "bad option index")
    if idx <= 0:
        _store(tid, cp.KEEP)                            # explicit Keep -> leave as-is (NOT unset)
    elif 1 <= idx <= len(presets):
        _store(tid, presets[idx - 1])                   # store the FULL preset name (idx 0 = Keep)
    else:
        raise RpcError("EINVAL", "option index out of range")
    return {"key": "preset", "value": idx}
