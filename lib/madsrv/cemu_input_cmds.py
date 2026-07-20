"""cemu_input.* - the Cemu (Wii U) family x context input-assignment page.

FAMILY-FIRST (simpler than the RetroArch profile-first raprof editor, because Cemu profiles are
device-agnostic): one page per launch CONTEXT ("docked" | "handheld"), each a "settings" page
rendered by the generic GuiMadPageEmuSettings under its OWN namespace -- "cemu_input_docked" and
"cemu_input_handheld" (EmuSettings does not thread a context param, so the door bakes the context
into the namespace it opens), buffered X=Save / Y=Cancel. Groups:

  "Family input"  - one bool switch: seating_enabled (the master opt-in for lib/cemu_seat).
  "<Docked|Handheld> map" - one ENUM row per controller family: which native
                            controllerProfiles/<stem>.xml is that family's map in this context.
                            Index 0 = "(leave resting)" = unset (that slot is left untouched).

The DOOR fixes the context: the docked Cemu tile Input row opens context="docked"; the on-the-go
Wii U Input door opens context="handheld". There is no in-page docked/handheld toggle.

EVERY write touches ONLY controller-policy.local.toml (the base profile_map ships empty). Editing a
family writes/removes ONE key under [backends.cemu.profile_map.<context>]; the maps are not shared
across profiles (unlike RA's [ra_profile_map]), so a net-changed write is self-contained.

Phase 2 (deferred) adds live per-button rebinding of a profile's <mappings> on a separate
GuiMadPageEmuInputMap leaf; this page is assignment only and reuses your existing profiles.
"""
from __future__ import annotations

import copy
import re
from pathlib import Path

from .. import localpolicy, mad_config
from ..policy import LOCAL, load_merged
from .input_buffer import InputBuffer
from .rpc import RpcError, method

_CONTEXTS = ("docked", "handheld")
_UNSET_LABEL = "(leave resting)"
_GAMEPAD_FAMILY = "Steam Deck"


def _families() -> list[str]:
    # Exclude X-Arcade: routing.family_of (which cemu_seat uses at launch) classifies the cab as
    # "Xbox" and NEVER "X-Arcade" (only family_token_of splits it out), so an "X-Arcade" row would be a
    # dead assignment the binder can never apply. Assign the cab via the "Xbox" row instead.
    return [f for f in mad_config.KNOWN_FAMILIES if f != "X-Arcade"]


def _cemu_cfg(merged: dict) -> dict:
    be = merged.get("backends") if isinstance(merged.get("backends"), dict) else {}
    cfg = be.get("cemu") if isinstance(be, dict) else None
    return cfg if isinstance(cfg, dict) else {}


def _config_dir(cfg: dict) -> Path:
    return Path(str(cfg.get("config_dir", "~/.config/Cemu/controllerProfiles"))).expanduser()


def _profile_stems(cfg: dict) -> list[str]:
    """Named profiles the user can assign: every controllerProfiles/*.xml EXCEPT the active slot
    files controller0..7.xml (those are the router-managed targets, not templates)."""
    cfg_dir = _config_dir(cfg)
    if not cfg_dir.is_dir():
        return []
    out = []
    for p in sorted(cfg_dir.glob("*.xml")):
        if re.fullmatch(r"controller[0-7]", p.stem):
            continue
        out.append(p.stem)
    return out


# ── buffered working copy (a nested dict; InputBuffer deep-compares it) ────────────────
def _load_working(ctx):
    (context,) = ctx
    merged = load_merged()
    cfg = _cemu_cfg(merged)
    pm = cfg.get("profile_map") if isinstance(cfg.get("profile_map"), dict) else {}
    slice_ = pm.get(context) if isinstance(pm.get(context), dict) else {}
    assign = {fam: str(slice_.get(fam, "") or "") for fam in _families()}
    # Options: (leave resting) + every profile stem, PLUS any assigned stem whose file is now
    # missing (so a stale assignment still renders as itself rather than silently reading as unset).
    stems = _profile_stems(cfg)
    extra = [s for s in assign.values() if s and s not in stems]
    options = [_UNSET_LABEL] + sorted(set(stems) | set(extra))
    return {"seating": bool(cfg.get("seating_enabled", False)), "assign": assign, "options": options}


def _apply_edit(working, edit):
    key = edit["key"]
    val = edit.get("value", "")
    if key == "seating_enabled":
        working["seating"] = (str(val) == "1")
    elif key.startswith("family:"):
        fam = key[len("family:"):]
        if fam not in working["assign"]:
            raise RpcError("EINVAL", f"unknown family {fam!r}")
        options = working["options"]
        try:
            idx = int(float(val))
        except (TypeError, ValueError):
            raise RpcError("EINVAL", f"bad option index {val!r}")
        working["assign"][fam] = "" if idx <= 0 or idx >= len(options) else options[idx]
    else:
        raise RpcError("EINVAL", f"unknown key {key!r}")
    return working, edit


def _flush(ctx, disk, edits):
    (context,) = ctx
    final = copy.deepcopy(disk)
    for e in edits:
        final, _ = _apply_edit(final, e)
    data = localpolicy.load(LOCAL)
    be = data.setdefault("backends", {}).setdefault("cemu", {})

    if (any(e["key"] == "seating_enabled" for e in edits)
            and final["seating"] != disk["seating"]):
        be["seating_enabled"] = bool(final["seating"])

    # Write ONLY families that NET-CHANGED vs the load-time snapshot (a toggle-there-and-back is a
    # no-op). "" clears the key (base profile_map is empty, so a cleared local key = unset).
    changed = {e["key"][len("family:"):] for e in edits if e["key"].startswith("family:")}
    if changed:
        pm = be.setdefault("profile_map", {}).setdefault(context, {})
        for fam in changed:
            if final["assign"].get(fam, "") == disk["assign"].get(fam, ""):
                continue
            stem = final["assign"].get(fam, "")
            if stem:
                pm[fam] = stem
            else:
                pm.pop(fam, None)
    localpolicy.dump(LOCAL, data)      # atomic write + staterev.bump("config")
    return _load_working(ctx)


_buf = InputBuffer(load=_load_working, apply_edit=_apply_edit, flush=_flush)


# ── render ────────────────────────────────────────────────────────────────────────────
def _render(context: str, working, dirty: bool) -> dict:
    options = working["options"]

    def _idx(stem):
        try:
            return options.index(stem) if stem else 0
        except ValueError:
            return 0

    fam_rows = [{"key": f"family:{f}", "label": f, "type": "enum",
                 "options": list(options), "value": _idx(working["assign"].get(f, ""))}
                for f in _families()]
    seat_row = {"key": "seating_enabled", "label": "Let MAD set input by controller",
                "type": "bool", "value": bool(working["seating"])}
    ctx_label = "Docked" if context == "docked" else "Handheld"
    return {
        "exists": True, "buffered": True, "dirty": dirty,
        "note": (f"Assign each controller family its {ctx_label.lower()} input profile. The layout "
                 f"follows the controller, not the player slot. '{_GAMEPAD_FAMILY}' is Controller 1 "
                 "(the Wii U GamePad). Leave a family on '(leave resting)' to not touch its slot. "
                 "Changes are staged - press X to save."),
        "groups": [
            {"title": "Family input", "note": "", "settings": [seat_row]},
            {"title": f"{ctx_label} map", "note": "", "settings": fam_rows},
        ],
    }


# ── buffered RPCs, ONE namespace per context. Getters registered WITHOUT cache=("config",):
#    the buffer is the source of truth mid-edit (per the InputBuffer caller contract). ──────
def _register(ns: str, context: str):
    @method(f"{ns}.get", slow=True)
    def _get(params, _c=context):
        return _render(_c, _buf.get((_c,)), _buf.dirty)

    @method(f"{ns}.set", slow=True)
    def _set(params, _c=context):
        key = params.get("key")
        if not key:
            raise RpcError("EINVAL", "key required")
        _buf.set((_c,), {"key": str(key), "value": str(params.get("value", ""))})
        return {"dirty": _buf.dirty}

    @method(f"{ns}.save", slow=True)
    def _save(params, _c=context):
        saved = _buf.save((_c,))
        return {"saved": saved, "message": "Saved." if saved else "Nothing to save."}

    @method(f"{ns}.cancel", slow=True)
    def _cancel(params, _c=context):
        _buf.cancel((_c,))
        return {"message": "Reverted to saved."}


_register("cemu_input_docked", "docked")
_register("cemu_input_handheld", "handheld")
