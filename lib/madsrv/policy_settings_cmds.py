"""policy_settings_cmds — per-system controller-policy flag toggles surfaced as
'settings' pages on the Standalones tiles.

The X-Arcade presence warnings and the wii peripheral / hands-off flags used to
live on the retired Systems page. They are per-ES-DE-system controller-policy
flags (controller-policy*.toml) read at launch by controller-router.py. This
module re-homes their EDITING onto the standalone emulator tiles: one settings
namespace per standalone, warn-bearing system, rendered by the generic
GuiMadPageEmuSettings (kind:"settings", no C++). Writes reuse policy_cmds
(policy.set_system_flag) so the base-default revert + the router_skip
base-hands-off clamp stay enforced in ONE place.

Scope: RetroArch-launched systems keep their warn toggle on RetroArch >
Controllers (backends_cmds.priority.get); this module covers only STANDALONE
systems. The two group tiles (Switch, Namco 246/256) are intentionally excluded
to preserve their bespoke section trees (memory switch-emu-menu-scheme) -- their
warn stays a controller-policy.toml value for now.
"""
from __future__ import annotations

from ..policy import load_merged
from . import policy_cmds
from .rpc import RpcError, method
from .systems_cmds import _warn_flag, resolve_category

_TRUE = {"1", "true", "yes", "on"}

# wii-only flags (beyond the X-Arcade warn) that lived on the Systems detail page.
_WII_FLAGS = [
    ("require_dolphinbar", "Require a DolphinBar"),
    ("require_sinden", "Require a Sinden gun"),
    ("router_skip", "Hands-off (leave input untouched)"),
]


def _flag_default(flag: str) -> bool:
    """Display default: warn_* default ON; require_* / router_skip default OFF
    (mirrors the old Systems detail page + policy_cmds revert logic)."""
    return flag.startswith("warn_")


def _flags_for(system: str, merged: dict) -> list[tuple[str, str]]:
    """The policy-flag (key, label) toggles for a system, in display order: its
    one X-Arcade warn flag, plus (wii only) the peripheral / hands-off flags."""
    flags: list[tuple[str, str]] = []
    wf = _warn_flag(system, resolve_category(system, merged))
    if wf:
        flags.append(wf)
    if system == "wii":
        flags += _WII_FLAGS
    return flags


def _standalone_flag_systems() -> dict[str, list[tuple[str, str]]]:
    """Every STANDALONE-launched ES-DE system that carries policy-flag toggles ->
    its flag list. Enumerated once at import for method registration."""
    out: dict[str, list[tuple[str, str]]] = {}
    try:
        from .. import es_systems
        sysxml = es_systems.load_systems()
        merged = load_merged()
    except Exception:
        return out
    for s in sysxml:
        try:
            if not es_systems.is_standalone(es_systems.default_command(s, sysxml)):
                continue
            flags = _flags_for(s, merged)   # inside the try: a malformed policy
        except Exception:                   # entry must not brick the import (and
            continue                        # thus the whole backend, per policy.py)
        if flags:
            out[s] = flags
    return out


SYSFLAGS = _standalone_flag_systems()


def ns_for(system: str) -> str:
    return f"sysflags_{system}"


def _sysflags_get(system: str) -> dict:
    merged = load_merged()
    ent = merged.get("systems", {}).get(system, {})
    ent = ent if isinstance(ent, dict) else {}
    settings = [
        {"key": key, "label": label, "type": "bool",
         "value": bool(ent.get(key, _flag_default(key)))}
        for key, label in _flags_for(system, merged)
    ]
    return {"exists": True, "running": False,
            "note": ("Controller options for " + system + ", applied whenever a "
                     + system + " game is launched."),
            "groups": ([{"title": "Options", "note": "", "settings": settings}]
                       if settings else [])}


def _sysflags_set(system: str, params: dict) -> dict:
    key = params["key"]
    value = str(params.get("value")).strip().lower() in _TRUE
    valid = {k for k, _ in _flags_for(system, load_merged())}
    if key not in valid:
        raise RpcError("EINVAL", f"{key!r} is not a policy flag for {system!r}")
    # Reuse policy.set_system_flag: base-default revert + router_skip hands-off
    # clamp live there (one source of truth for both this page and RA Controllers).
    policy_cmds._set_system_flag({"system": system, "flag": key, "value": value})
    ent = load_merged().get("systems", {}).get(system, {})
    ent = ent if isinstance(ent, dict) else {}
    return {"key": key, "value": bool(ent.get(key, _flag_default(key)))}


def _register(system: str) -> None:
    ns = ns_for(system)

    @method(f"{ns}.get", slow=True)
    def _g(params, system=system):
        return _sysflags_get(system)

    @method(f"{ns}.set", slow=True)
    def _s(params, system=system):
        return _sysflags_set(system, params)


for _sys in SYSFLAGS:
    _register(_sys)


def tile_flag_sections(systems: list[str], label: str) -> list[dict]:
    """The 'settings' sections to append to a standalone tile whose `systems`
    carry policy-flag toggles. One section per warn-bearing system the tile
    drives (dolphin drives wii + gc, so it gets two)."""
    flagged = [s for s in systems if s in SYSFLAGS]
    secs = []
    for s in flagged:
        wii = (s == "wii")
        # Title keys off the number of EMITTED sections, not len(systems): a tile
        # with two present systems but one warn-bearing (dolphin: wii + a
        # RetroArch gc) still gets the clean "<label> controller options".
        secs.append({
            "label": "Controller options" if wii else "X-Arcade warning",
            "sublabel": ("DolphinBar / Sinden gun / hands-off / warning" if wii
                         else "warn when the controller set is wrong"),
            "kind": "settings", "arg": ns_for(s),
            "title": (label + " controller options" if len(flagged) == 1
                      else label + " (" + s + ") controller options"),
        })
    return secs
