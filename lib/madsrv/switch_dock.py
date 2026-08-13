"""Shared engine for the per-fork "Dock detection" toggles (citron_dock.* / eden_dock.* /
ryujinx_dock.*). The three *_dock_cmds modules are thin shims calling register().

A MAD-owned preference (NOT an emulator config key): whether the launch wrapper auto-sets
the emulator's docked mode at game start to match the Steam Deck's live dock state (an
external screen -> docked / 1080p base; the built-in screen -> handheld / 720p base).
Stored as `[backends.<be>].dock_autodetect` in controller-policy.local.toml (deep-merged
over the shipped default `true`), and read at launch by switch_bind._dock_autodetect.
What the wrapper then writes differs per fork - the Yuzu forks (Citron/Eden) get
`[System] use_docked_mode` (+ the `\\default` twin) in qt-config.ini, Ryujinx gets the
top-level `docked_mode` key in its JSON config - but that logic lives in lib/switch_bind,
not here. Rendered by GuiMadPageEmuSettings as a single bool row, so the payload mirrors
cfgutil.get_groups (exists/running/note/groups).

This module registers nothing on import (no `_cmds` suffix by convention); each shim's
register() call registers its own namespace, so importing a shim registers exactly that
fork's methods, same as before the fold.
"""
from __future__ import annotations

from .rpc import method

_TRUE = {"1", "true", "yes", "on"}


def _enabled(backend: str) -> bool:
    """The effective flag (shipped default true, overlaid by any local override)."""
    try:
        from ..policy import load_merged
        be = (load_merged().get("backends") or {}).get(backend) or {}
        return bool(be.get("dock_autodetect", True))
    except Exception:
        return True


def _set_enabled(backend: str, on: bool) -> None:
    from .. import localpolicy
    from ..policy import LOCAL
    data = localpolicy.load(LOCAL)
    data.setdefault("backends", {}).setdefault(backend, {})["dock_autodetect"] = bool(on)
    localpolicy.dump(LOCAL, data)          # atomic write + staterev.bump("config")


def register(ns: str, backend: str, display: str) -> None:
    """Register <ns>.get/set for one fork. display is the user-facing emulator name
    templated into the note (byte-identical to the pre-fold per-module strings -
    pinned by tests/test_switch_ns_parity.test_dock_notes_byte_stable)."""

    @method(f"{ns}.get", slow=True)
    def _get(params, _be=backend, _name=display):
        return {
            "exists": True,
            "running": False,                  # a MAD preference, editable anytime
            "note": f"Automatically set {_name}'s docked/handheld mode at launch to "
                    "match your setup: docked (an external screen is connected) uses a "
                    "1080p base, handheld (the Steam Deck's built-in screen) uses a "
                    "720p base. Detected from the physical display; if the On-the-go "
                    "feature is off it falls back to controller presence. Turn off to "
                    f"keep whatever you set inside {_name}.",
            "groups": [{"title": "Docked mode", "note": "", "settings": [
                {"key": "dock_autodetect",
                 "label": "Auto-detect docked/handheld at launch",
                 "type": "bool", "value": _enabled(_be)}]}],
        }

    @method(f"{ns}.set", slow=True)
    def _set(params, _be=backend):
        on = str(params.get("value")).strip().lower() in _TRUE
        _set_enabled(_be, on)
        return {"key": params.get("key", "dock_autodetect"), "value": on}
