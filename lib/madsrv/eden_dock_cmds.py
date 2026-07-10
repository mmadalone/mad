"""eden_dock.* — the "Dock detection" toggle on the Eden tile.

Same MAD-owned launch preference as citron_dock/ryujinx_dock: whether the launch wrapper auto-sets
Eden's [System] use_docked_mode at game start to match the Steam Deck's live dock state (an external
controller present -> docked / 1080p base; only the built-in Deck gamepad -> handheld / 720p base).
Stored as `[backends.eden].dock_autodetect` in controller-policy.local.toml (deep-merged over the
shipped default `true`), read at launch by switch_bind._dock_autodetect. Single bool row payload
(exists/running/note/groups)."""
from __future__ import annotations

from .rpc import method

_TRUE = {"1", "true", "yes", "on"}


def _enabled() -> bool:
    try:
        from ..policy import load_merged
        be = (load_merged().get("backends") or {}).get("eden") or {}
        return bool(be.get("dock_autodetect", True))
    except Exception:
        return True


def _set_enabled(on: bool) -> None:
    from .. import localpolicy
    from ..policy import LOCAL
    data = localpolicy.load(LOCAL)
    data.setdefault("backends", {}).setdefault("eden", {})["dock_autodetect"] = bool(on)
    localpolicy.dump(LOCAL, data)          # atomic write + staterev.bump("config")


@method("eden_dock.get", slow=True)
def _get(params):
    return {
        "exists": True,
        "running": False,
        "note": "Automatically set Eden's docked/handheld mode at launch to match your setup: "
                "docked (an external screen is connected) uses a 1080p base, handheld (the Steam "
                "Deck's built-in screen) uses a 720p base. Detected from the physical display; if "
                "the On-the-go feature is off it falls back to controller presence. Turn off to "
                "keep whatever you set inside Eden.",
        "groups": [{"title": "Docked mode", "note": "", "settings": [
            {"key": "dock_autodetect",
             "label": "Auto-detect docked/handheld at launch",
             "type": "bool", "value": _enabled()}]}],
    }


@method("eden_dock.set", slow=True)
def _set(params):
    on = str(params.get("value")).strip().lower() in _TRUE
    _set_enabled(on)
    return {"key": params.get("key", "dock_autodetect"), "value": on}
