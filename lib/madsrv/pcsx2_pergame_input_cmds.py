"""pcsx2pgin.* — the PER-GAME input STORE for standard PCSX2 + the per-game Controllers page.

The store (``~/Emulation/storage/pcsx2/pergame-input.json``, keyed by disc ``<SERIAL>_<CRC>``)
carries each game's input overrides, applied to the GLOBAL ini at launch, transiently
(snapshotted + reverted on exit) — see lib/switch_bind.py:
  usb1 / usb2       USB port off ("None") or inherit (absent)
  pad2              Player 2 on/off (absent = inherit)
  pads              per-game pad->player TYPE order (the Controllers reorder page below)
  profiles          {"docked": <stem>, "handheld": <stem>} — the per-game input-profile picks,
                    EDITED by lib/madsrv/pcsx2_profile_cmds (pcsx2profpg / pcsx2profpghh)
  binds             LEGACY per-button remaps: tolerated + counted in _is_empty so an old entry
                    is never auto-pruned (never destroy user data), but NO LONGER read at launch
                    — input profiles superseded the per-button editors (2026-08-04).

CRITICAL context (why profiles are copied, not referenced): PCSX2 does not honor
``[Pad]``/``[USB1]``/``[USB2]`` in a per-game gamesettings ini (input is global-only, verified
in VMManager.cpp/InputManager.cpp), and native ``[Pad] InputProfileName`` would replace the
whole input layer and bypass our launch-time pad calibration. So per-game input intent lives in
THIS store and the launch binder applies it to the GLOBAL ini transiently.

This module owns the store file (other modules import its _load/_save/_LOCK/_normalize_entry)
plus the per-game Controllers page (pads_get / pads_set_order for GuiMadPagePergamePads).
There is NO EBUSY guard by design: the store is decoupled from PCSX2's live config and applied
at the game's next launch (test_no_running_guard_store_edits_always_work).
"""
from __future__ import annotations

import json
import re
import shutil
import sys
import threading

from .. import mad_config, mad_paths, staterev
from . import cfgutil, pads_cmds
from .rpc import RpcError, method

_STORE = mad_paths.storage("pcsx2", "pergame-input.json")
_KEY_RE = re.compile(r"^[A-Z]{3,4}-\d{3,5}_[0-9A-F]{8}$")
_LOCK = threading.Lock()


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


# ── entry shape ───────────────────────────────────────────────────────────────
# LEGACY binds are context-keyed: entry["binds"] = {"docked": {player: {key: src}}, ...};
# a pre-handheld flat binds ({player: {...}}) reads/migrates under "docked". They are inert at
# launch but preserved forever. Selectors (usb1/usb2/pad2/pads) and profiles are entry-level.
_CONTEXTS = ("docked", "handheld")


def _is_context_keyed(binds) -> bool:
    """True if `binds` is context-keyed (every top-level key is a context). Empty counts as new;
    a legacy flat map keyed by player number ("1", "2", ...) is False."""
    return isinstance(binds, dict) and all(k in _CONTEXTS for k in binds)


def _all_binds(e: dict) -> dict:
    """Every context's clean per-player LEGACY binds, normalised to {context: {player: {key:
    src}}}. A legacy flat `binds` folds under "docked". Non-dict / empty cruft dropped. Only
    for emptiness checks now — the launch path no longer reads binds."""
    binds = e.get("binds")
    if not isinstance(binds, dict):
        return {}
    if _is_context_keyed(binds):
        out = {}
        for c in _CONTEXTS:
            s = binds.get(c)
            if isinstance(s, dict):
                clean = {p: v for p, v in s.items() if isinstance(v, dict) and v}
                if clean:
                    out[c] = clean
        return out
    clean = {p: v for p, v in binds.items() if isinstance(v, dict) and v}
    return {"docked": clean} if clean else {}


def _profiles(e: dict) -> dict:
    """The entry's valid per-context input-profile picks ({context: stem}), husk-tolerant
    (a non-dict `profiles` or non-string/blank stems read as absent). See pcsx2_profile_cmds."""
    profiles = e.get("profiles")
    if not isinstance(profiles, dict):
        return {}
    return {c: v.strip() for c, v in profiles.items()
            if c in _CONTEXTS and isinstance(v, str) and v.strip()}


def _normalize_entry(entry: dict) -> dict:
    """Ensure entry['binds'] is context-keyed ({context: {player: {...}}}); migrate a legacy flat
    binds under 'docked' (lossless). A corrupt non-dict `profiles` husk is healed away.
    Selectors untouched. In place; returns entry."""
    binds = entry.get("binds")
    if isinstance(binds, dict) and not _is_context_keyed(binds):
        flat = {p: v for p, v in binds.items() if isinstance(v, dict) and v}
        if flat:
            entry["binds"] = {"docked": flat}
        else:
            entry.pop("binds", None)
    if "profiles" in entry and not isinstance(entry.get("profiles"), dict):
        entry.pop("profiles", None)
    return entry


def _is_empty(e: dict) -> bool:
    return (e.get("usb1") is None and e.get("usb2") is None and e.get("pad2") is None
            and not e.get("pads") and not _all_binds(e) and not _profiles(e))


def _has_input_override(e: dict) -> bool:
    """True if the entry carries a per-game INPUT override (USB port / Player 2 / input
    profile) — the '• custom' badge on the Per-game INPUT picker. A pad-ORDER-only entry (from
    the per-game controllers page) is NOT an input override, so it must not badge there.
    LEGACY `binds` deliberately do NOT badge: they no longer apply at launch (superseded by
    input profiles), so advertising them would claim a remap that isn't in effect. They still
    count in _is_empty so an old entry is never auto-pruned (rule: never destroy user data)."""
    return (e.get("usb1") is not None or e.get("usb2") is not None
            or e.get("pad2") is not None or bool(_profiles(e)))


def load_entry(titleid: str) -> dict | None:
    """The per-game input override for one game (or None). Public: the launch-time router
    (lib/switch_bind.py) reads it to apply USB/Pad2/pad-order/profiles to the global ini at
    game start."""
    if not titleid or not _KEY_RE.match(titleid):
        return None
    e = _load().get(titleid)
    return e if isinstance(e, dict) and not _is_empty(e) else None


def _titleid(params) -> str:
    tid = params.get("titleid") or ""
    if not _KEY_RE.match(tid):
        raise RpcError("EINVAL", f"bad game id {tid!r}")
    return tid


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
