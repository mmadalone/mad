"""dolphin_gc_dock.* -- the GameCube "Dock / handheld" settings (Input -> GameCube).

Two MAD preferences (NOT Dolphin ini keys) in `[backends.dolphin_gc]` of controller-policy.local.toml
(deep-merged over the shipped defaults), read at gc launch by controller-router.py:
  dock_autodetect (bool, default ON): when only the Deck's built-in gamepad is present (handheld),
    load the undocked profile into GameCube Port 1 at launch. (Docked launches are governed by the
    separate "Pads -> players" page, not this setting.) A transient swap, restored after the game.
  undocked_profile (str, default ""): which Profiles/GCPad profile to load into Port 1 when handheld.

Rendered by the generic GuiMadPageEmuSettings (a bool + an enum) -- payload mirrors the groups schema.
"""
from __future__ import annotations

from .. import dolphin_profiles
from ..policy import load_merged
from .rpc import RpcError, method

_TRUE = {"1", "true", "yes", "on"}
_BACKEND = "dolphin_gc"
_NONE = "(none)"


def _be() -> dict:
    be = (load_merged().get("backends") or {}).get(_BACKEND)
    return be if isinstance(be, dict) else {}


def _autodetect() -> bool:
    return bool(_be().get("dock_autodetect", True))


def undocked_profile() -> str:
    """The chosen undocked profile name ("" if none) — read by the launch binder too."""
    return str(_be().get("undocked_profile", "") or "")


def _profile_options() -> list[str]:
    return [_NONE] + dolphin_profiles.list_profiles()


def _set_pref(key: str, value) -> None:
    from .. import localpolicy
    from ..policy import LOCAL
    data = localpolicy.load(LOCAL)
    data.setdefault("backends", {}).setdefault(_BACKEND, {})[key] = value
    localpolicy.dump(LOCAL, data)          # atomic write + staterev.bump("config")


def dock_group() -> dict:
    """The Dock/handheld settings GROUP — folded into the On-the-go GameCube Settings page
    (2026-08-04; this page's own tile row was removed). Sets delegate back to `_set`."""
    profs = _profile_options()
    cur = undocked_profile()
    val = profs.index(cur) if cur in profs else 0
    return {"title": "Dock / handheld",
            "note": "When handheld, the chosen profile loads into GameCube Port 1 at launch "
                    "(docked keeps the Pads → players order; a per-game pick wins over both). "
                    "The swap is temporary and reverted after the game.",
            "settings": [
                {"key": "dock_autodetect",
                 "label": "Auto-swap to the undocked profile when handheld",
                 "type": "bool", "value": _autodetect()},
                {"key": "undocked_profile", "label": "Undocked profile (Port 1)",
                 "type": "enum", "options": profs, "value": val, "picker": True},
            ]}


@method("dolphin_gc_dock.get", slow=True)
def _get(params):
    return {
        "exists": True,
        "running": False,                  # a MAD preference, editable anytime
        "note": "When only the Deck's built-in gamepad is connected (handheld), load the chosen "
                "profile into GameCube Port 1 at launch. (When docked, the separate Pads → players "
                "page assigns your controllers.) The swap is temporary and reverted after the game. "
                "Turn off to keep your normal mapping when handheld.",
        "groups": [dock_group()],
    }


@method("dolphin_gc_dock.set", slow=True)
def _set(params):
    key = params.get("key")
    if key == "dock_autodetect":
        on = str(params.get("value")).strip().lower() in _TRUE
        _set_pref("dock_autodetect", on)
        return {"key": key, "value": on}
    if key == "undocked_profile":
        profs = _profile_options()
        try:
            idx = int(float(params.get("value")))
        except (TypeError, ValueError):
            raise RpcError("EINVAL", "bad profile index")
        if not (0 <= idx < len(profs)):
            raise RpcError("EINVAL", "profile index out of range")
        _set_pref("undocked_profile", "" if idx == 0 else profs[idx])   # index 0 = "(none)"
        return {"key": key, "value": idx}
    raise RpcError("EINVAL", f"{key!r} is not a dock setting")
