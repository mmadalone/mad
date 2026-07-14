"""rpcs3patch.* — PlayStation 3 (RPCS3) per-game "Manage patches" editor.

A game-scoped page (a ``pergame_settings`` leaf, titleid = SERIAL) that lists the patches
RPCS3's ``patches/patch.yml`` database carries for the picked game and lets you enable them
(and pick a value for a "Configurable Values" patch, e.g. an aspect ratio). Toggles/values
are written to ``~/.config/rpcs3/patch_config.yml`` (config ROOT, not patches/) -- the exact
file RPCS3 reads at game boot -- via
:mod:`rpcs3_patches` (format + parse quirks documented there). ONLY this game's entries are
touched; other games' enabled patches are preserved.

Rendering (reuses the shipped GuiMadPageEmuSettings groups/rows, like rpcs3pg):
  * a patch = an On/Off enum row; simple patches cluster under a "Patches" group.
  * a patch with a ``Group`` clusters with its alternatives under that group name.
  * a configurable patch gets its OWN group: an "Enabled" toggle + a value picker per param
    (enum -> the Allowed Values; range -> preset stops incl. min/max/default).
Buffered X=Save / Y=Cancel: ``.get`` returns buffered:true; ``.set`` STAGES into an in-memory
desired-state; ``.save`` merges it into a FRESH read of patch_config.yml (so another game's
entries are never clobbered) + one-time .bak + atomic + staterev bump; ``.cancel`` reloads.
Writes are refused while RPCS3 runs (its Patch Manager rewrites patch_config.yml).
"""
from __future__ import annotations

import copy

from .. import proc_guard
from . import rpcs3_patches as rp
from .rpc import RpcError, method

_PROC = "rpcs3"
_LABEL = "RPCS3 (PS3)"


def _running() -> bool:
    return proc_guard.emulator_running(_PROC)


def _check(serial: str) -> None:
    if not rp.is_serial(serial or ""):
        raise RpcError("EINVAL", f"bad game id {serial!r}")


# ── buffered desired-state (one serial at a time) ─────────────────────────────
# state/disk: {desc: {"enabled": bool, "vals": {param: num}}}
_buf: dict = {"serial": None, "state": None, "disk": None, "dirty": False}


def _reload(serial: str) -> None:
    patches = rp.patches_for(serial)
    disk = rp.state_from_config(serial, patches, rp.read_config())
    _buf.update(serial=serial, state=copy.deepcopy(disk), disk=disk, dirty=False)


def _ensure(serial: str) -> None:
    # Reload ONLY when the buffered serial changes -- NOT on dirtiness. A value picked while a
    # patch is Off is (correctly) non-dirty, so gating the reload on dirty would discard that
    # staged pick on the very next action (e.g. enabling the patch). Keeping the same-serial
    # buffer is safe: _patch_save always merges into a FRESH read_config(), so a stale buffer
    # can never clobber another game.
    if _buf["serial"] != serial:
        _reload(serial)


def _norm(state: dict) -> dict:
    """Dirty-comparison projection: a DISABLED patch's staged value is irrelevant (it is never
    written), so ignore vals unless enabled. Prevents a value change on an Off patch from
    marking the buffer dirty (which would 'Save' nothing yet flash Saved and discard the pick)."""
    return {d: (bool(v.get("enabled")), dict(v.get("vals") or {}) if v.get("enabled") else {})
            for d, v in (state or {}).items()}


def _recompute_dirty() -> None:
    _buf["dirty"] = _norm(_buf["state"]) != _norm(_buf["disk"])


# ── row / group builders ──────────────────────────────────────────────────────
def _group_of(p: dict):
    """(group-key, title, note, own_group) for a patch. own_group -> the patch has its own
    group (a configurable patch), so its enable row reads "Enabled"."""
    if p.get("group"):
        return ("grp", p["group"]), p["group"], "Alternatives — enable the one you want.", False
    if p.get("cfg"):
        return ("cfg", p["desc"]), p["desc"], "", True
    return ("simple",), "Patches", "", False


def _value_row(desc: str, param: str, spec: dict, label: str, current) -> dict | None:
    opts, default_num, is_long = rp.value_options(spec)
    if not opts:
        return None
    labels = [lbl for lbl, _ in opts]
    nums = [n for _, n in opts]
    cur = current if current is not None else default_num
    idx = next((i for i, n in enumerate(nums) if rp._close(n, cur)), None)
    if idx is None and cur is not None:
        labels = labels + [f"(current: {rp._fmt(cur, is_long)})"]
        idx = len(nums)
    return {"key": f"cv::{desc}::{param}", "label": label, "type": "enum",
            "options": labels, "value": idx or 0}


def _patch_get(serial: str) -> dict:
    _check(serial)
    _ensure(serial)
    patches = rp.patches_for(serial)
    state = _buf["state"]
    groups: list = []
    index_by_key: dict = {}
    for p in patches:
        desc = p["desc"]
        gkey, gtitle, gnote, own_group = _group_of(p)
        grp = index_by_key.get(gkey)
        if grp is None:
            grp = index_by_key[gkey] = {"title": gtitle, "note": gnote, "settings": []}
            groups.append(grp)
        st = state.get(desc) or {"enabled": False, "vals": {}}
        grp["settings"].append({
            "key": f"en::{desc}", "label": ("Enabled" if own_group else desc),
            "type": "enum", "options": ["Off", "On"], "value": 1 if st.get("enabled") else 0})
        params = p.get("cfg") or {}
        for param, spec in params.items():
            vlabel = ("Value" if (own_group and len(params) == 1) else param)
            row = _value_row(desc, param, spec, vlabel, (st.get("vals") or {}).get(param))
            if row is not None:
                grp["settings"].append(row)
    if not patches:
        note = (f"No patches in RPCS3's database (patch.yml) for this game (serial {serial}). "
                "Patches are added by updating patch.yml.")
    else:
        note = ("Enable patches for this game. A configurable patch also has a value picker. "
                "Changes are staged; press Save. Applied by RPCS3 at the next launch.")
    return {"exists": True, "running": _running(), "buffered": True,
            "dirty": _buf["dirty"], "note": note, "groups": groups}


def _patch_lookup(serial: str, key: str):
    """(patch dict, param|None) for a row key, or raise EINVAL."""
    patches = {p["desc"]: p for p in rp.patches_for(serial)}
    if key.startswith("en::"):
        desc = key[4:]
        p = patches.get(desc)
        if p is None:
            raise RpcError("EINVAL", f"{key!r} is not a known patch")
        return p, None
    if key.startswith("cv::"):
        # rpartition (split at the LAST "::") so a "::" inside a description can't misparse;
        # param names carry no "::". Membership is validated below regardless.
        desc, _, param = key[4:].rpartition("::")
        p = patches.get(desc)
        if p is None or param not in (p.get("cfg") or {}):
            raise RpcError("EINVAL", f"{key!r} is not a known patch value")
        return p, param
    raise RpcError("EINVAL", f"{key!r} is not an editable row")


def _patch_set(params: dict) -> dict:
    if _running():
        raise RpcError("EBUSY", f"{_LABEL} is running — close it first "
                                "(its Patch Manager rewrites patch_config.yml).")
    serial = params.get("titleid") or ""
    _check(serial)
    _ensure(serial)
    key, value = params["key"], params["value"]
    p, param = _patch_lookup(serial, key)
    desc = p["desc"]
    st = _buf["state"].setdefault(desc, {"enabled": False, "vals": {}})
    try:
        n = int(float(value))
    except (TypeError, ValueError):
        raise RpcError("EINVAL", f"bad value {value!r} for {key}")
    if param is None:                                  # enable toggle
        st["enabled"] = n >= 1
        echo = 1 if st["enabled"] else 0
    else:                                              # configurable value picker
        opts, _default, _is_long = rp.value_options(p["cfg"][param])
        if 0 <= n < len(opts):
            st.setdefault("vals", {})[param] = opts[n][1]
            echo = n
        else:                                          # off-list "(current: …)" slot -> no-op
            echo = n
    _recompute_dirty()
    return {"key": key, "dirty": _buf["dirty"], "value": echo}


def _patch_save(serial: str) -> dict:
    _check(serial)
    if _running():
        raise RpcError("EBUSY", f"{_LABEL} is running — close it first "
                                "(its Patch Manager rewrites patch_config.yml).")
    from .. import staterev
    if _buf["serial"] != serial or not _buf["dirty"]:
        if _buf["serial"] == serial:                   # only clear OUR serial's flag, never
            _buf["dirty"] = False                      # another buffered serial's staged edits
        return {"saved": False}
    base = rp.read_config()
    if base is None:                                   # file present but unparseable
        raise RpcError("EIO", "patch_config.yml is present but couldn't be parsed; refusing to "
                              "overwrite it (it would drop other games' patches). Fix or move it "
                              "aside, then retry.")
    existed = rp._PATCH_CONFIG.is_file()
    patches = rp.patches_for(serial)
    cfg = rp.apply_state(base, patches, _buf["state"])
    if cfg or existed:                                 # don't create a stray empty {} file
        rp.write_config(cfg)
    _buf["disk"] = copy.deepcopy(_buf["state"])
    _buf["dirty"] = False
    staterev.bump("config")
    return {"saved": True}


def _patch_cancel(serial: str) -> dict:
    _check(serial)
    _reload(serial)
    return {"cancelled": True}


@method("rpcs3patch.get", slow=True)
def _get(params):
    tid = params.get("titleid")
    if not tid:
        raise RpcError("EINVAL", "titleid required")
    return _patch_get(tid)


@method("rpcs3patch.set", slow=True)
def _set(params):
    return _patch_set(params)


@method("rpcs3patch.save", slow=True)
def _save(params):
    tid = params.get("titleid")
    if not tid:
        raise RpcError("EINVAL", "titleid required")
    return _patch_save(tid)


@method("rpcs3patch.cancel", slow=True)
def _cancel(params):
    tid = params.get("titleid")
    if not tid:
        raise RpcError("EINVAL", "titleid required")
    return _patch_cancel(tid)
