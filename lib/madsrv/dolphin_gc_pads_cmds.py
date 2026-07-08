"""GameCube "Pads -> players" priority page (Input -> GameCube) — delegated from pads.* .

Same reorderable page as the other standalones (C++ GuiMadPagePadsPriority), but the priority list
holds GC PROFILES (Profiles/GCPad/*.ini), not device types: Dolphin binds each port to a device by
source/index/name AND couples the button tokens to the device backend, so a self-consistent profile
(Device + bindings) is the only reliable per-port unit. The C++ page treats each pad `id` as an
opaque string, so profile-name ids drive it unchanged (no rebuild).

pads_cmds delegates pads.get/pads.set/pads.hands_off here when emu == "dolphin_gc". Prefs live in
[backends.dolphin_gc] of controller-policy.local.toml (pads_priority: list, pads_hands_off: bool),
alongside the dock/handheld prefs; applied at DOCKED gc launch by lib/dolphin_gc_pads.py.
"""
from __future__ import annotations

from .. import dolphin_profiles, proc_guard
from ..policy import load_merged

_BACKEND = "dolphin_gc"
_LABEL = "GameCube"
_PLAYERS = 4               # GameCube has four controller ports


def _be() -> dict:
    be = (load_merged().get("backends") or {}).get(_BACKEND)
    return be if isinstance(be, dict) else {}


def priority() -> list[str]:
    """Stored profile priority order (public: read by the launch assigner)."""
    return [str(x) for x in (_be().get("pads_priority") or [])]


def hands_off() -> bool:
    return bool(_be().get("pads_hands_off", False))


def _set_pref(key: str, value) -> None:
    from .. import localpolicy
    from ..policy import LOCAL
    data = localpolicy.load(LOCAL)
    tbl = data.setdefault("backends", {}).setdefault(_BACKEND, {})
    if value in (None, [], False):
        tbl.pop(key, None)
        if not tbl:
            (data.get("backends") or {}).pop(_BACKEND, None)
    else:
        tbl[key] = value
    localpolicy.dump(LOCAL, data)          # atomic write + staterev.bump("config")


def _connected_names() -> set[str]:
    """Connected real pads' names — BOTH each pad's evdev name AND its SDL name, so a profile's
    Device name matches regardless of which backend Dolphin took it from (a DS4 is evdev
    'Wireless Controller' / SDL 'PS4 Controller'). Backend context = SDL is already warm."""
    from .. import devices as dv
    names: set[str] = set()
    try:
        names |= {d.name for d in dv.joypads(dv.enumerate_devices()) if d.name}
    except Exception:
        pass
    try:
        names |= {s.name for s in dv.sdl_devices() if s.name}
    except Exception:
        pass
    return names


def _pad_connected(dev_name: str | None, connected: set[str]) -> bool:
    """A profile's target pad is present iff its Device name EXACTLY equals a connected pad name.
    Exact (not substring): evdev name == SDL2 string exactly on this rig, so a substring test would
    false-fire (e.g. a bare 'Wireless Controller' vs 'DualSense Wireless Controller'). Kept in step
    with the launch assigner's _take so the ● dot predicts what will actually bind."""
    return bool(dev_name) and dev_name in connected


def _ordered_profiles() -> list[str]:
    """All profiles, stored-priority order first (stale names dropped), new ones appended."""
    allp = dolphin_profiles.list_profiles()
    stored = [p for p in priority() if p in allp]
    return stored + [p for p in allp if p not in stored]


def _pads_get(params):
    profs = _ordered_profiles()
    connected = _connected_names()
    running = proc_guard.emulator_running("dolphin")
    ho = hands_off()
    rows = [{"id": name, "vidpid": name,
             "connected": _pad_connected(dolphin_profiles.profile_device(name), connected),
             "label": name} for name in profs]
    for r in rows:                        # bake the ● marker into the label (page reads label only)
        if r["connected"]:
            r["label"] += "  ●"
    if running:
        note = "Close Dolphin first — it rewrites its config on exit."
    elif ho:
        note = "Hands-off: Dolphin uses its own controller config; MAD won't touch it."
    elif not profs:
        note = ("No GameCube profiles yet. Create them in Dolphin "
                "(Controllers → GameCube → Configure → Profile → Save).")
    else:
        note = (f"Order your GameCube profiles — when docked, the top {_PLAYERS} whose pad is "
                f"connected become Players 1–{_PLAYERS} at launch.  ● = its pad is connected now.")
    return {"emu": _BACKEND, "label": _LABEL, "players": _PLAYERS,
            "running": running, "hands_off": ho, "note": note, "pads": rows, "unsupported": []}


def _pads_set(params):
    valid = set(dolphin_profiles.list_profiles())
    order = [p for p in (str(x) for x in (params.get("order") or [])) if p in valid]
    _set_pref("pads_priority", order)
    return {"emu": _BACKEND, "order": order,
            "message": "Saved — applied when you launch a docked GameCube game."}


def _pads_hands_off(params):
    value = bool(params.get("value"))
    _set_pref("pads_hands_off", value)
    return {"emu": _BACKEND, "hands_off": value,
            "message": ("Hands-off ON — MAD won't touch GameCube controllers."
                        if value else
                        "Hands-off OFF — MAD applies your profile order on a docked launch.")}
