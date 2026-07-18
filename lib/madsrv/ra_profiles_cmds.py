"""raprof.* - the RetroArch input-PROFILE editor backend (P3).

Two surfaces over the same store (lib/ra_profiles.py's pure transforms):

  * DIRECT-WRITE  raprof.list / create / delete / reset - mirror policy_cmds:
    load(LOCAL) -> mutate -> localpolicy.dump(LOCAL) -> return the merged view.
  * BUFFERED per-profile detail  raprof.get / set / save / cancel - rendered by
    the generic GuiMadPageEmuSettings (ns="raprof", ctxKey="profile"). Groups:
      "Used by"  - one bool switch per controller family (assign this profile)
      "Hotkeys"  - the 6 hotkey rows as semantic-token enum pickers
      "Options"  - analog-stick-as-D-pad
    Edits stage in an InputBuffer keyed on the profile name; save flushes them to
    controller-policy.local.toml in ONE write (X=Save / Y=Cancel).

EVERY write touches ONLY controller-policy.local.toml; the base seed is read-only.
A base-seeded profile can be edited (shadowed) or "reset" (shadow dropped) but never
deleted - only user-made profiles delete (routing.deep_merge cannot remove a base key).
"""
from __future__ import annotations

import copy
import tomllib

from .. import localpolicy, mad_config, ra_profiles
from ..policy import LOCAL, POLICY, load_merged
from .input_buffer import InputBuffer
from .rpc import RpcError, method

# ── the semantic RetroPad token vocabulary offered per hotkey row, in display order ──
# (token, label). "" = deliberately unbound. mbtn:3 = the X-Arcade trackball red button.
_TOKENS: list[tuple[str, str]] = [
    ("",       "(unbound)"),
    ("l3",     "L3 - left stick click"),
    ("r3",     "R3 - right stick click"),
    ("l",      "L1 - left bumper"),
    ("r",      "R1 - right bumper"),
    ("l2",     "L2 - left trigger"),
    ("r2",     "R2 - right trigger"),
    ("a",      "A / Cross - bottom face"),
    ("b",      "B / Circle - right face"),
    ("x",      "X / Square - left face"),
    ("y",      "Y / Triangle - top face"),
    ("select", "Select / Back"),
    ("start",  "Start"),
    ("up",     "D-pad Up"),
    ("down",   "D-pad Down"),
    ("left",   "D-pad Left"),
    ("right",  "D-pad Right"),
    ("mbtn:3", "Mouse button 3 - X-Arcade trackball"),
]
_TOKEN_ORDER = [t for t, _ in _TOKENS]
_TOKEN_LABELS = [lbl for _, lbl in _TOKENS]

_HK_LABELS = {
    "modifier": "Modifier (hold)", "rewind": "Rewind", "fast_forward": "Fast-forward",
    "slowmotion": "Slow-motion", "menu": "Menu", "quit": "Quit",
}
# RetroArch input_playerN_analog_dpad_mode: 0 None, 1 Left stick, 2 Right stick.
_ANALOG_DPAD = ["Off", "Left stick as D-pad", "Right stick as D-pad"]

# ── the lightgun gun-bind vocabulary (RAW values: mouse buttons + keyboard keys), in display order ──
# A gun bind is NOT a gamepad token: trigger/reload are MOUSE buttons, aux/d-pad/start/select are
# KEYBOARD keys (and they diverge per player -- 2-player keyboard setups still use RA's per-game
# binds). "" = inherit the working global cfg (the resolver emits nothing).
_GUN_TOKENS: list[tuple[str, str]] = (
    [("",       "(inherit global cfg)"),
     ("mbtn:1", "Mouse 1 (left / trigger)"),
     ("mbtn:2", "Mouse 2 (right / reload)"),
     ("mbtn:3", "Mouse 3 (middle)"),
     ("up",     "Key: Up"), ("down", "Key: Down"), ("left", "Key: Left"), ("right", "Key: Right"),
     ("enter",  "Key: Enter"), ("escape", "Key: Escape"),
     ("space",  "Key: Space"), ("tab", "Key: Tab")]
    + [(c, f"Key: {c.upper()}") for c in "abcdefghijklmnopqrstuvwxyz"]
    + [(f"num{d}", f"Key: {d}") for d in list(range(1, 10)) + [0]]
)
_GUN_TOKEN_ORDER = [t for t, _ in _GUN_TOKENS]
_GUN_TOKEN_LABELS = [lbl for _, lbl in _GUN_TOKENS]
_GUN_LABELS = {
    "trigger": "Trigger", "offscreen_shot": "Reload / off-screen", "aux_a": "Aux A", "aux_b": "Aux B",
    "aux_c": "Aux C", "dpad_up": "D-pad Up", "dpad_down": "D-pad Down", "dpad_left": "D-pad Left",
    "dpad_right": "D-pad Right", "select": "Select / Coin", "start": "Start",
}
# mouse_index: option 0 = auto-detect / inherit, then Mouse 0..15. The router auto-detects Sinden; a
# picked "Mouse N" is the manual override the router uses ONLY when auto-detect does not fire.
_MOUSE_INDEX_OPTS = ["(auto-detect / inherit)"] + [f"Mouse {i}" for i in range(16)]
_DECK_PROFILE = "Deck"     # the handheld Deck-pad profile carries no lightgun section


def _base_policy() -> dict:
    try:
        with open(POLICY, "rb") as f:
            return tomllib.load(f)
    except Exception:
        return {}


def _families() -> list[str]:
    return list(mad_config.KNOWN_FAMILIES)


# ── the buffered working copy: a nested dict (InputBuffer deep-compares it) ──────────

def _load_working(ctx):
    (name,) = ctx
    merged = load_merged()
    prof = ra_profiles.get_profile(merged, name) or {}
    hk = prof.get("hotkeys") if isinstance(prof.get("hotkeys"), dict) else {}
    hotkeys = {f: str(hk.get(f, "") or "") for f, _ in ra_profiles.HOTKEYS}
    settings = {}
    src = prof.get("settings") if isinstance(prof.get("settings"), dict) else {}
    if src.get("analog_dpad_mode") not in (None, ""):
        settings["analog_dpad_mode"] = str(src["analog_dpad_mode"])
    pmap = merged.get("ra_profile_map") if isinstance(merged.get("ra_profile_map"), dict) else {}
    families = {fam: (pmap.get(fam) == name) for fam in _families()}
    lg_src = prof.get("lightgun") if isinstance(prof.get("lightgun"), dict) else {}
    lightgun = {n: str(lg_src.get(n, "") or "") for n in ra_profiles._GUN_BINDS}
    lightgun["mouse_index"] = str(lg_src.get("mouse_index", "") or "")
    return {"hotkeys": hotkeys, "settings": settings, "families": families, "lightgun": lightgun}


def _token_from_index(value, current: str) -> str:
    try:
        idx = int(float(value))
    except (TypeError, ValueError):
        raise RpcError("EINVAL", f"bad hotkey index {value!r}")
    if 0 <= idx < len(_TOKEN_ORDER):
        return _TOKEN_ORDER[idx]
    # The trailing "(current: <raw escape>)" slot (index == len(_TOKEN_ORDER)): keep the field's
    # current value. KNOWN LIMITATION (P3, accepted, LOW): EmuSettings fixes the enum options at
    # page-build time, so after editing THIS field in-session the slot's label still reads the
    # ORIGINAL escape while `current` is the edited value -- re-picking it stores the edited value,
    # not the label. Only reachable for a hand-authored raw escape (btn:N/axis:+N/hat) that no
    # shipped profile uses; cancel-before-save recovers it.
    return str(current or "")


def _gun_token_from_index(value, current: str) -> str:
    """Index into _GUN_TOKENS -> the gun token; the trailing '(current: <raw>)' slot keeps current."""
    try:
        idx = int(float(value))
    except (TypeError, ValueError):
        raise RpcError("EINVAL", f"bad gun bind index {value!r}")
    if 0 <= idx < len(_GUN_TOKEN_ORDER):
        return _GUN_TOKEN_ORDER[idx]
    return str(current or "")


def _mouse_index_from_index(value) -> str:
    """Enum index -> stored mouse_index. Option 0 = auto-detect/inherit (clears); option i>=1 = Mouse i-1."""
    try:
        idx = int(float(value))
    except (TypeError, ValueError):
        raise RpcError("EINVAL", f"bad mouse_index {value!r}")
    return "" if idx <= 0 else str(idx - 1)


def _apply_edit(working, edit):
    key, value = edit["key"], edit["value"]
    if key.startswith("hotkey:"):
        field = key[len("hotkey:"):]
        if field not in {f for f, _ in ra_profiles.HOTKEYS}:
            raise RpcError("EINVAL", f"unknown hotkey {field!r}")
        working["hotkeys"][field] = _token_from_index(value, working["hotkeys"].get(field, ""))
    elif key.startswith("gun:"):
        gname = key[len("gun:"):]
        if gname not in ra_profiles._GUN_BIND_KEYS:
            raise RpcError("EINVAL", f"unknown lightgun bind {gname!r}")
        working["lightgun"][gname] = _gun_token_from_index(value, working["lightgun"].get(gname, ""))
    elif key == "mouse_index":
        working["lightgun"]["mouse_index"] = _mouse_index_from_index(value)
    elif key.startswith("family:"):
        working["families"][key[len("family:"):]] = (str(value) == "1")
    elif key == "setting:analog_dpad_mode":
        try:
            idx = int(float(value))
        except (TypeError, ValueError):
            raise RpcError("EINVAL", f"bad analog_dpad index {value!r}")
        if idx <= 0:
            working["settings"].pop("analog_dpad_mode", None)   # Off == cleared (keeps local lean)
        else:
            working["settings"]["analog_dpad_mode"] = str(idx)
    else:
        raise RpcError("EINVAL", f"unknown key {key!r}")
    return working, edit


def _flush(ctx, disk, edits):
    (name,) = ctx
    final = copy.deepcopy(disk)
    for e in edits:
        final, _ = _apply_edit(final, e)
    data = localpolicy.load(LOCAL)
    # Write ONLY keys that NET-CHANGED versus the load-time snapshot (`disk`). InputBuffer keeps
    # every staged edit, so a toggle-on-then-off leaves an edit in the list with no net change;
    # writing it anyway pollutes local -- and for a FAMILY it is worse than pollution: unassign("")
    # into the SHARED [ra_profile_map] would clobber a DIFFERENT profile's base assignment.
    hotkeys = {f: final["hotkeys"][f]
               for f in {e["key"][len("hotkey:"):] for e in edits if e["key"].startswith("hotkey:")}
               if final["hotkeys"].get(f, "") != disk["hotkeys"].get(f, "")}
    if hotkeys:
        ra_profiles.set_hotkeys(data, name, hotkeys)
    if (any(e["key"] == "setting:analog_dpad_mode" for e in edits)
            and final["settings"].get("analog_dpad_mode") != disk["settings"].get("analog_dpad_mode")):
        ra_profiles.set_setting(data, name, "analog_dpad_mode",
                                final["settings"].get("analog_dpad_mode"))
    for fam in {e["key"][len("family:"):] for e in edits if e["key"].startswith("family:")}:
        if final["families"].get(fam) == disk["families"].get(fam):
            continue                       # net-zero toggle: never touch the shared map
        if final["families"].get(fam):
            ra_profiles.assign_family(data, fam, name)
        else:
            ra_profiles.unassign_family(data, fam)
    gun = {n: final["lightgun"][n]
           for n in {e["key"][len("gun:"):] for e in edits if e["key"].startswith("gun:")}
           if final["lightgun"].get(n, "") != disk["lightgun"].get(n, "")}
    for gname, tok in gun.items():
        ra_profiles.set_lightgun(data, name, gname, tok)
    if (any(e["key"] == "mouse_index" for e in edits)
            and final["lightgun"].get("mouse_index", "") != disk["lightgun"].get("mouse_index", "")):
        ra_profiles.set_mouse_index(data, name, final["lightgun"].get("mouse_index", ""))
    localpolicy.dump(LOCAL, data)      # atomic write + staterev.bump("config")
    return _load_working(ctx)


_buf = InputBuffer(load=_load_working, apply_edit=_apply_edit, flush=_flush)


def _token_row(field: str, token: str) -> dict:
    tok = str(token or "")
    label = _HK_LABELS.get(field, field)
    if tok in _TOKEN_ORDER:
        return {"key": f"hotkey:{field}", "label": label, "type": "enum",
                "options": list(_TOKEN_LABELS), "value": _TOKEN_ORDER.index(tok)}
    # a raw escape (e.g. "btn:5") the vocabulary can't name: keep it, never discard it
    return {"key": f"hotkey:{field}", "label": label, "type": "enum",
            "options": list(_TOKEN_LABELS) + [f"(current: {tok})"], "value": len(_TOKEN_LABELS)}


def _gun_row(gname: str, token: str) -> dict:
    label = _GUN_LABELS.get(gname, gname)
    tok = str(token or "")
    if tok in _GUN_TOKEN_ORDER:
        return {"key": f"gun:{gname}", "label": label, "type": "enum",
                "options": list(_GUN_TOKEN_LABELS), "value": _GUN_TOKEN_ORDER.index(tok)}
    # a raw escape / key the vocabulary doesn't list: keep it, never discard it
    return {"key": f"gun:{gname}", "label": label, "type": "enum",
            "options": list(_GUN_TOKEN_LABELS) + [f"(current: {tok})"], "value": len(_GUN_TOKEN_LABELS)}


def _mouse_index_row(value: str) -> dict:
    raw = str(value or "").strip()
    try:
        idx = int(float(raw)) if raw else -1
    except (TypeError, ValueError):
        idx = -1
    val = (idx + 1) if 0 <= idx <= 15 else 0     # option 0 = auto-detect / inherit
    return {"key": "mouse_index", "label": "Mouse index (force a specific mouse)", "type": "enum",
            "options": list(_MOUSE_INDEX_OPTS), "value": val}


def _render(name: str, working, dirty: bool, shipped: bool) -> dict:
    fam = [{"key": f"family:{f}", "label": f, "type": "bool",
            "value": bool(working["families"].get(f))} for f in _families()]
    hk = [_token_row(field, working["hotkeys"].get(field, "")) for field, _ in ra_profiles.HOTKEYS]
    adp = working["settings"].get("analog_dpad_mode")
    try:
        adp_idx = max(0, min(len(_ANALOG_DPAD) - 1, int(float(adp)))) if adp not in (None, "") else 0
    except (TypeError, ValueError):
        adp_idx = 0
    opt = [{"key": "setting:analog_dpad_mode", "label": "Analog stick as D-pad", "type": "enum",
            "options": list(_ANALOG_DPAD), "value": adp_idx}]
    # A shipped profile can be RESET (drop the local shadow) but never deleted; a user-made one is
    # deletable. Rendered as an action button (fires its own RPC), not a buffered setting.
    if shipped:
        action = {"type": "action", "key": "reset", "label": "Reset to shipped defaults",
                  "rpc": "raprof.reset", "args": {"profile": name}}
    else:
        action = {"type": "action", "key": "delete", "label": "Delete this profile",
                  "rpc": "raprof.delete", "args": {"profile": name}}
    groups = [
        {"title": "Used by",
         "note": "Which controller families use this profile (a family uses exactly one).",
         "settings": fam},
        {"title": "Hotkeys", "note": "", "settings": hk},
    ]
    # Lightgun on every profile EXCEPT the handheld Deck pad (a gun makes no sense there).
    if name != _DECK_PROFILE:
        gun_rows = [_gun_row(n, working["lightgun"].get(n, "")) for n in ra_profiles._GUN_BINDS]
        gun_rows.append(_mouse_index_row(working["lightgun"].get("mouse_index", "")))
        groups.append({
            "title": "Lightgun",
            "note": "Gun games only. Left on 'inherit', your working global setup is untouched. "
                    "Trigger/reload are mouse buttons; aux/d-pad/start/select are keyboard keys "
                    "(shared across players -- 2-player keyboard binds use RA's per-game menu). "
                    "Mouse index auto-detects Sinden; set it only to force a specific mouse.",
            "settings": gun_rows})
    groups += [
        {"title": "Options", "note": "", "settings": opt},
        {"title": "", "note": "", "settings": [action]},
    ]
    return {
        "exists": True, "buffered": True, "dirty": dirty,
        "note": "Hotkeys follow whichever pad the router seats on P1. "
                "Hold the Modifier, then press one of the others. Changes are staged - press X to save.",
        "groups": groups,
    }


# ── buffered detail RPCs (registered WITHOUT cache=("config",): the buffer is truth) ──

def _require_exists(name):
    """The profile must still exist in the merged view. Guards the buffered write path: a detail page
    left open after its profile was Deleted (the reused EmuSettings page does not auto-pop) must not
    be able to re-create the profile or write a dangling [ra_profile_map] row via a further edit."""
    if not name:
        raise RpcError("EINVAL", "profile required")
    if name not in set(ra_profiles.list_profiles(load_merged())):
        raise RpcError("ENOENT", f"no profile named {name!r}")


@method("raprof.get", slow=True)
def _get(params):
    name = params.get("profile")
    _require_exists(name)
    working = _buf.get((name,))
    return _render(name, working, _buf.dirty, ra_profiles.is_shipped(_base_policy(), name))


@method("raprof.set", slow=True)
def _set(params):
    name, key = params.get("profile"), params.get("key")
    if not key:
        raise RpcError("EINVAL", "key required")
    _require_exists(name)
    _buf.set((name,), {"key": str(key), "value": str(params.get("value", ""))})
    return {"dirty": _buf.dirty}


@method("raprof.save", slow=True)
def _save(params):
    name = params.get("profile")
    _require_exists(name)
    saved = _buf.save((name,))
    return {"saved": saved, "message": "Saved." if saved else "Nothing to save."}


@method("raprof.cancel", slow=True)
def _cancel(params):
    name = params.get("profile")
    if not name:
        raise RpcError("EINVAL", "profile required")
    _buf.cancel((name,))
    return {"message": "Reverted to saved."}


# ── direct-write list / create / delete / reset ──────────────────────────────────────

@method("raprof.list", cache=("config",))
def _list(params):
    from .systems_cmds import resolve_art
    merged = load_merged()
    base = _base_policy()
    shadow = localpolicy.load(LOCAL).get("ra_profiles")
    shadow = shadow if isinstance(shadow, dict) else {}
    profiles = []
    for n in ra_profiles.list_profiles(merged):
        # The 5 shipped profiles (Arcade/Gamepad/Retro/Pro/Deck) have a router-config icon named for
        # them; a user-made profile has none (empty art -> no tile picture). Case-folded to match the
        # icon filenames (arcade.png etc.).
        art = resolve_art([f"icons/{n.lower()}.png"])
        profiles.append({"name": n, "shipped": ra_profiles.is_shipped(base, n),
                         "shadowed": n in shadow, "art": [art] if art else []})
    new_art = resolve_art(["icons/new-profile.png"])
    return {"profiles": profiles, "families": _families(), "merged": merged,
            "new_art": [new_art] if new_art else []}


@method("raprof.create")
def _create(params):
    data = localpolicy.load(LOCAL)
    try:
        stored = ra_profiles.create_profile(data, params.get("name", ""), load_merged())
    except ValueError as exc:
        raise RpcError("EINVAL", str(exc))
    localpolicy.dump(LOCAL, data)
    return {"created": stored, "merged": load_merged()}


@method("raprof.delete")
def _delete(params):
    name = params.get("profile", "")
    if not name:
        raise RpcError("EINVAL", "profile required")
    base = _base_policy()
    if not base.get("ra_profiles"):
        # Base unreadable/empty -> we cannot prove `name` is user-made. Refuse rather than fail OPEN:
        # a fail-open delete drops the profile's LOCAL shadow (the user's edits) while the base copy
        # survives and reappears -- a silent no-op delete that also loses edits.
        raise RpcError("EIO", "base policy is unreadable; refusing to delete")
    if ra_profiles.is_shipped(base, name):
        raise RpcError("EINVAL", f"{name!r} is a shipped profile; reset it instead of deleting")
    data = localpolicy.load(LOCAL)
    ra_profiles.delete_profile(data, name)
    localpolicy.dump(LOCAL, data)
    if _buf.ctx == (name,):
        _buf.reset()
    return {"deleted": name, "merged": load_merged(), "message": f"Deleted {name}."}


@method("raprof.reset")
def _reset(params):
    name = params.get("profile", "")
    data = localpolicy.load(LOCAL)
    ra_profiles.reset_profile(data, name)
    localpolicy.dump(LOCAL, data)
    if _buf.ctx == (name,):
        _buf.reset()
    return {"reset": name, "merged": load_merged(), "message": f"Reset {name} to shipped."}
