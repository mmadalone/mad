"""pcsx2pgin.* — PER-GAME input for standard PCSX2, keyed by disc serial+CRC.

PCSX2 does not honor `[Pad]`/`[USB1]`/`[USB2]` in a per-game gamesettings ini (input is
global-only, verified in VMManager.cpp/InputManager.cpp), and its native per-game route
(Input Profiles) would replace the whole input layer and bypass our launch-time pad
calibration. So per-game input intent lives in OUR store and the router applies it to the
GLOBAL ini at launch, transiently (snapshotted + reverted on exit) — see lib/switch_bind.py.

This module is only the editor. It mirrors the global input page (pcsx2_input_cmds): the
same button / d-pad / stick capture rows via input_translate, plus three global-scope
SELECTORS the input-map page renders — USB Port 1, USB Port 2 (device or None=off) and
Player 2 (on/off). Everything is per game (titleid = "<SERIAL>_<CRC>"); nothing here writes
a PCSX2 file. v1 covers Players 1-2; pad->player physical assignment is a later pass.
"""
from __future__ import annotations

import copy
import json
import re
import shutil
import sys
import threading
from pathlib import Path

from .. import mad_config, mad_paths, pcsx2_cfg, staterev
from . import cfgutil, pads_cmds, pcsx2_games
from .input_buffer import InputBuffer
from .input_translate import (parse_axis_token, pcsx2_axis_source, pcsx2_dpad_source,
                              sdl_button_source, sdl_source_label)
from .pcsx2_input_cmds import _BUTTONS, _DPAD, _DPAD_KEYS, _STICK_KEYS, _STICKS
from .rpc import RpcError, method

_STORE = mad_paths.storage("pcsx2", "pergame-input.json")
_GLOBAL_INI = Path("~/.config/PCSX2/inis/PCSX2.ini").expanduser()
_KEY_RE = re.compile(r"^[A-Z]{3,4}-\d{3,5}_[0-9A-F]{8}$")
_LOCK = threading.Lock()

_PLAYERS = [{"id": "1", "label": "Player 1"}, {"id": "2", "label": "Player 2"}]
_PLAYER_IDS = {"1", "2"}

# Bind rows: reuse the global page's buttons / d-pad / sticks, but present L2/R2 as ANALOG
# TRIGGER rows (pull the trigger -> +LeftTrigger / +RightTrigger; also how X-Arcade LT/RT and
# most pads expose them) rather than digital buttons, so a full pull registers and sticks +
# triggers are both rebindable per game. L2/R2 dropped from Buttons to avoid a same-key double row.
_PG_BUTTONS = [(k, l) for k, l in _BUTTONS if k not in ("L2", "R2")]
_PG_BUTTON_KEYS = {k for k, _ in _PG_BUTTONS}
_TRIGGERS = [("L2", "L2 (analog)"), ("R2", "R2 (analog)")]
_AXIS_KEYS = _STICK_KEYS | {"L2", "R2"}

# USB port selector = enable/disable the port. "" = inherit the global (which already carries the
# device AND its bind block, e.g. a globally-configured GunCon2 on USB1); "None" = force the port
# OFF for this game. We deliberately do NOT offer enabling a specific device here: PCSX2 needs that
# device's full bind block, which the dedicated lightgun pages (pcsx2x6/ps2guncon) own; writing only
# Type= would leave an unbound, unusable device. So v1 is a clean per-game port on/off.
_USB_OPTS = [{"value": "", "label": "Inherit global"},
             {"value": "None", "label": "None (port off)"}]
_USB_VALUES = {o["value"] for o in _USB_OPTS}
_PAD2_OPTS = [{"value": "", "label": "Inherit global"},
             {"value": "on", "label": "On"},
             {"value": "off", "label": "Off"}]
_PAD2_VALUES = {o["value"] for o in _PAD2_OPTS}
_SELECTOR_KEYS = {"usb1", "usb2", "pad2"}


# ── store ─────────────────────────────────────────────────────────────────────
def _load() -> dict:
    try:
        d = json.loads(_STORE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except OSError:
        return {}
    except ValueError:
        # Corrupt store (external / hand edit): preserve it for recovery instead of silently
        # overwriting every other game's overrides on the next save (rule #5: never destroy data).
        try:
            bad = _STORE.with_name(_STORE.name + ".bad")
            if not bad.exists():
                shutil.copy2(_STORE, bad)
            print(f"pcsx2pgin: {_STORE.name} is corrupt; backed up to {bad.name}, starting fresh",
                  file=sys.stderr)
        except OSError:
            pass
        return {}


def _save(data: dict) -> None:
    _STORE.parent.mkdir(parents=True, exist_ok=True)
    cfgutil.atomic_write(_STORE, json.dumps(data, indent=2, sort_keys=True))


def _entry_binds(e: dict) -> dict:
    """The entry's per-player binds as a clean {player: {key: source}}, ignoring any non-dict
    cruft from a hand-edited store (mirrors pcsx2_cfg.load_input_overrides' isinstance filtering)."""
    binds = e.get("binds")
    if not isinstance(binds, dict):
        return {}
    return {p: v for p, v in binds.items() if isinstance(v, dict) and v}


def _is_empty(e: dict) -> bool:
    return (e.get("usb1") is None and e.get("usb2") is None and e.get("pad2") is None
            and not e.get("pads") and not _entry_binds(e))


def _has_input_override(e: dict) -> bool:
    """True if the entry carries a per-game INPUT override (USB port / Player 2 / button
    remap) — the '• custom' badge on the Per-game INPUT picker. A pad-ORDER-only entry (from
    the per-game controllers page) is NOT an input override, so it must not badge there."""
    return (e.get("usb1") is not None or e.get("usb2") is not None
            or e.get("pad2") is not None or bool(_entry_binds(e)))


def load_entry(titleid: str) -> dict | None:
    """The per-game input override for one game (or None). Public: the launch-time router
    (lib/switch_bind.py) reads it to apply USB/Pad2/binds to the global ini at game start."""
    if not titleid or not _KEY_RE.match(titleid):
        return None
    e = _load().get(titleid)
    return e if isinstance(e, dict) and not _is_empty(e) else None


# ── helpers ────────────────────────────────────────────────────────────────────
def _titleid(params) -> str:
    tid = params.get("titleid") or ""
    if not _KEY_RE.match(tid):
        raise RpcError("EINVAL", f"bad game id {tid!r}")
    return tid


def _player(params) -> str:
    p = str(params.get("player") or "1")
    return p if p in _PLAYER_IDS else "1"


def _global_source(player: int, key: str) -> str:
    """The resolved GLOBAL binding for this player+button = the baked DualShock2 default
    layered with any global per-player remap. This is the value a per-game row inherits."""
    ov = pcsx2_cfg.load_input_overrides(_GLOBAL_INI).get(player, {})
    return ov.get(key) or pcsx2_cfg.baked_default_sources().get(key, "")


def _selectors(entry: dict) -> list:
    usb1, usb2, pad2 = entry.get("usb1"), entry.get("usb2"), entry.get("pad2")
    return [
        {"key": "usb1", "label": "USB Port 1", "scope": "global", "dependent": False,
         "value": usb1 or "", "options": _USB_OPTS},
        {"key": "usb2", "label": "USB Port 2", "scope": "global", "dependent": False,
         "value": usb2 or "", "options": _USB_OPTS},
        {"key": "pad2", "label": "Player 2 pad", "scope": "global", "dependent": False,
         "value": ("" if pad2 is None else ("on" if pad2 else "off")), "options": _PAD2_OPTS},
    ]


# ── buffered editor plumbing (X=Save / Y=Cancel) ────────────────────────────────
# Edits to the input REMAP + SELECTORS stage in a module-level InputBuffer and only reach the
# JSON store on pcsx2pgin.input_save; pcsx2pgin.input_cancel drops them. The WORKING copy is ONE
# game's entry (a dict spanning both players' binds + the USB/Pad2 selectors); ctx = (titleid,)
# so switching game reloads. pads_get / pads_set_order stay IMMEDIATE (a different per-game
# feature) and share the store via _LOCK. There is NO EBUSY guard here by design: the store is
# decoupled from PCSX2's live config and applied by the router at the game's next launch, so
# editing it any time is safe (test_no_running_guard_store_edits_always_work).
def _pg_compute_source(key: str, kind: str, params) -> str:
    """Validate one per-game capture and return its SDL `<source>` (pure). Same rows as the
    global page but L2/R2 are analog-trigger axes (in _AXIS_KEYS), not digital buttons."""
    if key in _DPAD_KEYS and kind == "hat":
        source = pcsx2_dpad_source(str(params.get("value", "")))
        if source is None:
            raise RpcError("EINVAL", "press a d-pad direction")
    elif key in _AXIS_KEYS and kind == "axis":         # sticks + analog triggers (L2/R2)
        parsed = parse_axis_token(str(params.get("value", "")))
        if parsed is None:
            raise RpcError("EINVAL", "push the stick, or pull the trigger, in that direction")
        source = pcsx2_axis_source(*parsed)
        if source is None:
            raise RpcError("EINVAL", "that axis can't be mapped")
    elif key in _PG_BUTTON_KEYS and kind == "btn":
        try:
            code = int(params["value"])
        except (KeyError, ValueError, TypeError):
            raise RpcError("EINVAL", "missing or invalid button code")
        source = sdl_button_source(code)
        if source is None:
            raise RpcError("EINVAL", "that input can't be mapped — press a face, shoulder, "
                                    "trigger, stick-click, Select or Start button")
    else:
        raise RpcError("EINVAL", f"{key!r} is not a remappable PCSX2 input")
    return source


def _pg_apply(entry: dict, edit: dict) -> dict:
    """Apply one staged edit to a single game's ENTRY dict (in memory only). Pure: no disk I/O,
    no bump, no title prune (pruning an emptied entry is reconciled at flush). Replayed onto a
    FRESH store read by _buf_flush so a foreign edit to the entry's OTHER keys (e.g. pad order)
    survives."""
    op = edit["op"]
    if op == "selector":                                # validated here so it fires at save too
        key = edit["key"]
        value = str(edit.get("value", "")).strip()
        if key not in _SELECTOR_KEYS:
            raise RpcError("EINVAL", f"unknown selector {key!r}")
        if key in ("usb1", "usb2"):
            if value not in _USB_VALUES:
                raise RpcError("EINVAL", f"bad USB type {value!r}")
            store_val = value or None
        else:                                           # pad2
            if value not in _PAD2_VALUES:
                raise RpcError("EINVAL", f"bad Player 2 value {value!r}")
            store_val = None if value == "" else (value == "on")
        if store_val is None:
            entry.pop(key, None)
        else:
            entry[key] = store_val
        return entry
    player, key = edit["player"], edit["id"]
    if op == "clear":
        binds = entry.get("binds") if isinstance(entry, dict) else None
        if isinstance(binds, dict) and isinstance(binds.get(player), dict):
            binds[player].pop(key, None)
            if not binds[player]:
                del binds[player]
            if not binds:
                entry.pop("binds", None)
        return entry
    # op == "set"
    source = _pg_compute_source(key, edit["kind"], edit)
    if not isinstance(entry.get("binds"), dict):        # heal a hand-corrupted entry
        entry["binds"] = {}
    if not isinstance(entry["binds"].get(player), dict):
        entry["binds"][player] = {}
    entry["binds"][player][key] = source
    return entry


def _buf_load(ctx: tuple) -> dict:
    (titleid,) = ctx
    with _LOCK:
        e = _load().get(titleid)
        return copy.deepcopy(e) if isinstance(e, dict) else {}


def _buf_apply_edit(entry: dict, edit: dict):
    return _pg_apply(entry, edit), edit


def _buf_flush(ctx: tuple, disk: dict, edits: list) -> dict:
    (titleid,) = ctx
    with _LOCK:
        data = _load()                                  # FRESH whole store (foreign entries survive)
        entry = data.get(titleid)
        entry = entry if isinstance(entry, dict) else {}
        for edit in edits:                              # replay only OUR edits onto the fresh entry
            entry = _pg_apply(entry, edit)
        if _is_empty(entry):                            # reconcile the selector_set prune HERE
            data.pop(titleid, None)
            entry = {}
        else:
            data[titleid] = entry
        _save(data)
    return entry


_buf = InputBuffer(load=_buf_load, apply_edit=_buf_apply_edit, flush=_buf_flush)


# ── RPC ─────────────────────────────────────────────────────────────────────────
@method("pcsx2pgin.input_get", slow=True)
def _input_get(params):
    tid = _titleid(params)
    player = _player(params)
    pint = int(player)
    entry = _buf.get((tid,))                            # buffer-over-disk: reflects staged edits
    binds = _entry_binds(entry).get(player, {})

    def row(key, label, kind):
        src = binds.get(key) or _global_source(pint, key)
        return {"id": key, "label": label, "kind": kind,
                "value": sdl_source_label(src) if src else "—", "capturable": True}

    groups = [
        {"title": "Buttons", "binds": [row(k, l, "btn") for k, l in _PG_BUTTONS]},
        {"title": "D-pad", "binds": [row(k, l, "hat") for k, l in _DPAD]},
        {"title": "Analog sticks", "binds": [row(k, l, "axis") for k, l in _STICKS]},
        {"title": "Triggers", "binds": [row(k, l, "axis") for k, l in _TRIGGERS]},
    ]
    # No running/EBUSY gate: this writes only our own JSON store (never PCSX2's config); it is
    # applied by the router at the game's NEXT launch, so editing it any time is harmless.
    note = (f"Per-game input for Player {player}. USB ports, Player 2 and button remaps here apply "
            "only to this game, set at launch and reverted on exit. Blank = inherit the global. It "
            "takes effect at the game's next launch.")
    return {"running": False, "note": note, "groups": groups, "clearable": True,
            "selectors": _selectors(entry), "players": _PLAYERS, "player": player,
            "buffered": True, "dirty": _buf.dirty}


@method("pcsx2pgin.input_set", slow=True)
def _input_set(params):
    tid = _titleid(params)
    key, kind = params.get("id", ""), params.get("kind", "btn")
    player = _player(params)
    _buf.set((tid,), {"op": "set", "player": player, "id": key, "kind": kind,
                      "value": str(params.get("value", ""))})   # stage (validated by _pg_apply)
    source = _buf.working.get("binds", {}).get(player, {}).get(key, "")
    return {"id": key, "value": sdl_source_label(source),
            "message": f"{key} → {sdl_source_label(source)}", "dirty": _buf.dirty}


@method("pcsx2pgin.input_clear", slow=True)
def _input_clear(params):
    """Unbind one per-game button — the "focus a row, press Start" clear. Stages removal of the
    per-game remap so the button inherits the global binding again; committed on Save."""
    tid = _titleid(params)
    key = params.get("id") or params.get("key") or ""
    if key not in _PG_BUTTON_KEYS and key not in _DPAD_KEYS and key not in _AXIS_KEYS:
        raise RpcError("EINVAL", f"{key!r} is not a remappable PCSX2 input")
    player = _player(params)
    _buf.set((tid,), {"op": "clear", "player": player, "id": key})   # stage; no disk write
    src = _global_source(int(player), key)
    return {"id": key, "value": sdl_source_label(src) if src else "—",
            "message": f"{key} reset to global", "dirty": _buf.dirty}


@method("pcsx2pgin.selector_set", slow=True)
def _selector_set(params):
    tid = _titleid(params)
    key = params.get("key", "")
    value = str(params.get("value", "")).strip()
    # Validated (and the empty-entry prune reconciled) inside the buffer; no disk write here.
    _buf.set((tid,), {"op": "selector", "key": key, "value": value})
    return {"key": key, "value": value, "dirty": _buf.dirty}


@method("pcsx2pgin.input_save", slow=True)
def _input_save(params):
    return {"saved": _buf.save((_titleid(params),)), "dirty": _buf.dirty}


@method("pcsx2pgin.input_cancel", slow=True)
def _input_cancel(params):
    _buf.cancel((_titleid(params),))
    return {"cancelled": True, "dirty": _buf.dirty}


@method("pcsx2pgin.games", slow=True)
def _games(params):
    store = _load()

    def _ovr(key):
        e = store.get(key)
        return _has_input_override(e) if isinstance(e, dict) else False

    out = []
    for g in pcsx2_games.games():
        override = _ovr(g["key"])
        stem = Path(g["path"]).stem if g.get("path") else ""  # ES-DE FileData getStem parity
        out.append({"titleid": g["key"], "name": g["name"], "stem": stem,
                    "override": override, "summary": "Custom input" if override else ""})
    return {"games": out, "system": "ps2"}


# ── per-game pad order: which controller TYPE is which player (Phase 2 v2) ──────────
# Stored as a per-game type-priority list under entry["pads"] in the SAME store. The launch
# router (lib/switch_bind.py) feeds it into pads_cmds._ordered as an override, applied BEFORE
# the managed_players truncation so it can promote a pad into the top-N. The reorder page (fork
# GuiMadPagePergamePads) drives pads_get / pads_set_order. Type-level, exactly like the global
# pads->players page: two identical-model pads still fall to SDL-index order between themselves.
_PADS_EMU = "pcsx2"


def _pad_rows(order: list | None, pump: bool = True) -> list:
    """[{id, vidpid, connected, label}] for the reorder list — the selectable controller TYPES
    (KNOWN_PADS plus any connected class), connected ones flagged. Mirrors pads_cmds._pads_get
    row-building (reuses its helpers). Row ORDER = the per-game `order` if given (its classes
    first, then the rest in the global display order), else the global order."""
    real = pads_cmds._real_pads(pump=pump)
    connected = {d.vidpid for d in real}
    connected_order = list(dict.fromkeys(d.vidpid for d in real))
    labels = pads_cmds._pad_labels(real)
    base = pads_cmds._type_universe(_PADS_EMU, connected_order)   # global order, rest appended
    if order:
        keys = list(dict.fromkeys(str(x) for x in order))
        # Keep a per-game-pinned class VISIBLE even while disconnected + unknown, so a re-Apply
        # (which resends only the shown rows) can't silently drop a pin whose pad is unplugged.
        # Mirrors _type_universe's retention of the GLOBAL stored order, but for the per-game
        # order. Skip the handheld/virtual classes (never pinnable in the first place).
        seen = set(base)
        for vp in keys:
            if vp not in seen and vp not in pads_cmds._EXCLUDE_TYPE:
                base.append(vp)
                seen.add(vp)
        rank = {vp: i for i, vp in enumerate(keys)}
        base_index = {vp: i for i, vp in enumerate(base)}
        base = sorted(base, key=lambda vp: (rank.get(vp, len(rank)), base_index[vp]))
    rows = []
    for vp in base:
        known = mad_config.KNOWN_PADS.get(vp)
        name = mad_config.PAD_SHORT.get(vp) or known
        inst = next((labels.get(d.index) for d in real if d.vidpid == vp), None)
        if inst and inst != known:          # the identified X-Arcade shows its port-aware label
            name = inst
        if not name:
            name = inst or vp
        rows.append({"id": vp, "vidpid": vp, "connected": vp in connected,
                     "label": name + ("  ●" if vp in connected else "")})
    return rows


@method("pcsx2pgin.pads_get", slow=True)
def _pads_get(params):
    tid = _titleid(params)
    e = _load().get(tid)
    order = e.get("pads") if isinstance(e, dict) and isinstance(e.get("pads"), list) else None
    n = pads_cmds.managed_players(_PADS_EMU)
    slots = "Player 1" if n == 1 else f"Players 1 to {n}"
    caption = ("Set the controller order for THIS game (top = Player 1). At launch the top "
               f"connected types fill {slots}; every other game keeps your global order. Two "
               "pads of the same model can't be split here (SDL order decides).  ● = connected now.")
    return {"titleid": tid, "label": "PCSX2", "players": n,
            "pads": _pad_rows(order), "note": caption, "caption": caption}


@method("pcsx2pgin.pads_set_order")
def _pads_set_order(params):
    tid = _titleid(params)
    order = [str(x) for x in (params.get("order") or [])]
    # Inherit-drop: if the applied order matches the global order over the same classes, store
    # nothing — so dragging back to the global arrangement clears the per-game override. pump=False:
    # SDL is already warm from the pads_get that populated the page, so read without a fresh pump.
    inherit_ids = [r["id"] for r in _pad_rows(None, pump=False)]
    with _LOCK:
        data = _load()
        e = data.setdefault(tid, {})
        if order and order != inherit_ids:
            e["pads"] = order
        else:
            e.pop("pads", None)
        if _is_empty(e):                                  # keep the store + picker badge tidy
            data.pop(tid, None)
        _save(data)
    staterev.bump("config")
    return {"titleid": tid, "order": order,
            "message": "Saved. Applied when you launch this game from ES-DE."}
