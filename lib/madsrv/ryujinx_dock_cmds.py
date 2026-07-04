"""ryujinx_dock.* — the "Dock detection" toggle on the Ryujinx tile (System group).

A MAD-owned preference (NOT a Ryujinx config key): whether the launch wrapper auto-sets Ryujinx's
top-level `docked_mode` at game start to match the Steam Deck's live dock state (an external
controller present -> docked / 1080p base; only the built-in Deck gamepad -> handheld / 720p base).
Stored as `[backends.ryujinx].dock_autodetect` in controller-policy.local.toml (deep-merged over the
shipped default `true`), and read at launch by switch_bind._dock_autodetect. Rendered by
GuiMadPageEmuSettings as a single bool row, so the payload mirrors cfgutil.get_groups
(exists/running/note/groups)."""
from __future__ import annotations

from .rpc import method

_TRUE = {"1", "true", "yes", "on"}


def _enabled() -> bool:
    """The effective flag (shipped default true, overlaid by any local override)."""
    try:
        from ..policy import load_merged
        be = (load_merged().get("backends") or {}).get("ryujinx") or {}
        return bool(be.get("dock_autodetect", True))
    except Exception:
        return True


def _set_enabled(on: bool) -> None:
    from .. import localpolicy
    from ..policy import LOCAL
    data = localpolicy.load(LOCAL)
    data.setdefault("backends", {}).setdefault("ryujinx", {})["dock_autodetect"] = bool(on)
    localpolicy.dump(LOCAL, data)          # atomic write + staterev.bump("config")


@method("ryujinx_dock.get", slow=True)
def _get(params):
    return {
        "exists": True,
        "running": False,                  # a MAD preference, editable anytime
        "note": "Automatically set Ryujinx's docked/handheld mode at launch to match your setup: "
                "docked (an external controller is connected) uses a 1080p base, handheld (only the "
                "Steam Deck's built-in gamepad) uses a 720p base. Turn off to keep whatever you set "
                "inside Ryujinx.",
        "groups": [{"title": "Docked mode", "note": "", "settings": [
            {"key": "dock_autodetect",
             "label": "Auto-detect docked/handheld at launch",
             "type": "bool", "value": _enabled()}]}],
    }


@method("ryujinx_dock.set", slow=True)
def _set(params):
    on = str(params.get("value")).strip().lower() in _TRUE
    _set_enabled(on)
    return {"key": params.get("key", "dock_autodetect"), "value": on}
