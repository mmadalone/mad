"""Wii "Classic Controller pads -> players" priority page (Input -> Wii) -- delegated from pads.* .

Same reorderable page as the GameCube one (C++ GuiMadPagePadsPriority), but the priority list holds
Wii CLASSIC-CONTROLLER profiles (Profiles/Wiimote/*.ini with Extension = Classic), not device types:
Dolphin couples a Wii Remote's Classic bindings to its device backend, so a self-consistent profile
(Device + Classic bindings) is the only reliable per-slot unit. The C++ page treats each pad `id` as
an opaque string, so profile-name ids drive it unchanged (no rebuild).

pads_cmds delegates pads.get/pads.set/pads.hands_off here when emu == "dolphin_wii". Prefs live in
[backends.dolphin_wii] of controller-policy.local.toml (pads_priority: list, pads_hands_off: bool);
applied at a no-DolphinBar DOCKED Wii launch by lib/dolphin_wii_pads via lib/dolphin_wii_source.
"""
from __future__ import annotations

from .. import dolphin_wii_profiles, proc_guard
from ..policy import load_merged

_BACKEND = "dolphin_wii"
_LABEL = "Wii"
_PLAYERS = 4               # Dolphin emulates up to four Wii Remotes
_HANDHELD_DEFAULT = "Steamdeck = classic controller"    # the Deck's built-in-pad CC profile


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
    """Connected real pads' names -- BOTH each pad's evdev name AND its SDL name, so a profile's
    Device name matches regardless of which backend Dolphin took it from (a DS4 is evdev
    'Wireless Controller' / SDL 'PS4 Controller')."""
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
    """A profile's target pad is present iff its Device name EXACTLY equals a connected pad name."""
    return bool(dev_name) and dev_name in connected


def _handheld_profile() -> str:
    """The profile reserved for HANDHELD play (the Deck's built-in pad); kept OUT of the docked list."""
    return str(_be().get("undocked_profile", _HANDHELD_DEFAULT) or _HANDHELD_DEFAULT)


def _docked_profiles() -> list[str]:
    """CC profiles eligible for the DOCKED pads->players priority: every CC profile EXCEPT the
    handheld (undocked) one, so the Deck's own sticks never phantom-fill a slot when docked."""
    hh = _handheld_profile()
    return [p for p in dolphin_wii_profiles.list_profiles() if p != hh]


def docked_default() -> list[str]:
    """The implicit priority when the user has set none (public: the launch assigner falls back to
    this so docked Classic Controller works out of the box) -- all docked CC profiles in name order."""
    return _docked_profiles()


def _ordered_profiles() -> list[str]:
    """Docked CC profiles, stored-priority order first (stale names dropped), the rest appended."""
    allp = _docked_profiles()
    stored = [p for p in priority() if p in allp]
    return stored + [p for p in allp if p not in stored]


def _pads_get(params):
    profs = _ordered_profiles()
    connected = _connected_names()
    running = proc_guard.emulator_running("dolphin")
    ho = hands_off()
    rows = [{"id": name, "vidpid": name,
             "connected": _pad_connected(dolphin_wii_profiles.profile_device(name), connected),
             "label": name} for name in profs]
    for r in rows:                        # bake the ● marker into the label (page reads label only)
        if r["connected"]:
            r["label"] += "  ●"
    if running:
        note = "Close Dolphin first -- it rewrites its config on exit."
    elif ho:
        note = "Hands-off: Dolphin uses its own controller config; MAD won't touch it."
    elif not profs:
        note = ("No Wii Classic Controller profiles yet. Create them in Dolphin (Controllers -> "
                "Wii Remote -> Emulated -> Classic extension -> Configure -> Profile -> Save).")
    else:
        note = (f"Order your Classic Controller profiles -- when docked with NO DolphinBar, the top "
                f"{_PLAYERS} whose pad is connected become Players 1-{_PLAYERS}.  ● = pad connected now.")
    return {"emu": _BACKEND, "label": _LABEL, "players": _PLAYERS,
            "running": running, "hands_off": ho, "note": note, "pads": rows, "unsupported": []}


def _pads_set(params):
    valid = set(_docked_profiles())
    order = [p for p in (str(x) for x in (params.get("order") or [])) if p in valid]
    _set_pref("pads_priority", order)
    return {"emu": _BACKEND, "order": order,
            "message": "Saved -- applied when you launch a docked Wii game with no DolphinBar."}


def _pads_hands_off(params):
    value = bool(params.get("value"))
    _set_pref("pads_hands_off", value)
    return {"emu": _BACKEND, "hands_off": value,
            "message": ("Hands-off ON -- MAD won't touch Wii Classic Controller mapping."
                        if value else
                        "Hands-off OFF -- MAD applies your profile order on a no-bar docked launch.")}
