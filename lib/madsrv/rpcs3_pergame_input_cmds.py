"""rpcs3pgin.* — PER-GAME input (button/stick/trigger map) for RPCS3, keyed by disc SERIAL.

Mirrors the global rpcs3.input_ page (same buttons / d-pad / analog-stick capture rows, same
token validation via rpcs3_input_cmds._token_for) but stores a per-serial, per-player override
in OUR JSON store. The launch router (lib/switch_bind) layers these over the global map at game
start, TRANSIENTLY: Default.yml is snapshotted and reverted on exit, so a per-game remap never
persists. Up to 4 players. RPCS3 doesn't honor a per-game input file natively (input is global),
so — exactly like PCSX2 per-game input — MAD owns the intent and applies it at launch.

Store: {serial: {context: {player_str: {ps3_key: sdl_token}}}}, context "docked" | "handheld" (a
legacy flat {serial: {player_str: {...}}} entry reads as docked and migrates on the next save). A
row with no per-game override shows the resolved GLOBAL binding FOR THAT CONTEXT (the global MAD
override, else the canonical SDL default) and inherits it at launch. No EBUSY guard: the store is
decoupled from RPCS3's live config and applied at the NEXT launch, so editing it any time is safe.
The On-the-go PS3 door opens this page with context=handheld, the docked Standalones door with
context=docked -- handheld is its own axis and never inherits the docked per-game map.
"""
from __future__ import annotations

import copy
import json
import re
import shutil
import sys
import threading

from .. import handheld_input, mad_paths, rpcs3_cfg
from . import cfgutil, rpcs3_games
from .input_buffer import InputBuffer
from .rpcs3_input_cmds import (_BUTTON_KEYS, _BUTTONS, _DEFAULT_CONFIG, _display, _DPAD,
                               _DPAD_KEYS, _STICK_KEYS, _STICKS, _token_for)
from .rpc import RpcError, method

_STORE = mad_paths.storage("rpcs3", "pergame-input.json")
_SERIAL_RE = re.compile(r"^[A-Z]{4}[0-9]{5}\Z")
_LOCK = threading.Lock()
_PLAYERS_MAX = 4
_PLAYER_IDS = {str(n) for n in range(1, _PLAYERS_MAX + 1)}
_ALL_KEYS = _BUTTON_KEYS | _DPAD_KEYS | _STICK_KEYS
# Valid stored tokens = the RPCS3 SDL source tokens the mappable keys can hold (every capture from
# _token_for lands in this universe of button/d-pad/stick tokens). A hand-edited garbage token in
# the JSON store is dropped by _clean_entry so it can never reach the transient launch config.
_VALID_TOKENS = {t for k in _ALL_KEYS if isinstance((t := _DEFAULT_CONFIG.get(k)), str) and t}


# ── store ─────────────────────────────────────────────────────────────────────
def _load() -> dict:
    try:
        d = json.loads(_STORE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except OSError:
        return {}
    except ValueError:
        # Corrupt store (external / hand edit): preserve it for recovery rather than silently
        # overwriting every other game's overrides on the next save (rule #5: never destroy data).
        # Name the backup by a content hash so EACH DISTINCT corruption is preserved (not just the
        # first) and the same corruption isn't re-copied on every load.
        try:
            import hashlib
            digest = hashlib.sha1(_STORE.read_bytes()).hexdigest()[:8]
            bad = _STORE.with_name(f"{_STORE.name}.{digest}.bad")
            if not bad.exists():
                shutil.copy2(_STORE, bad)
                print(f"rpcs3pgin: {_STORE.name} corrupt; backed up to {bad.name}, starting fresh",
                      file=sys.stderr)
        except OSError:
            pass
        return {}


def _save(data: dict) -> None:
    _STORE.parent.mkdir(parents=True, exist_ok=True)
    cfgutil.atomic_write(_STORE, json.dumps(data, indent=2, sort_keys=True))


def _clean_entry(e) -> dict:
    """{player_str: {key: token}} with only valid players/keys/tokens; empty players dropped."""
    if not isinstance(e, dict):
        return {}
    out = {}
    for pstr, binds in e.items():
        if pstr in _PLAYER_IDS and isinstance(binds, dict):
            clean = {k: v for k, v in binds.items() if k in _ALL_KEYS and v in _VALID_TOKENS}
            if clean:
                out[pstr] = clean
    return out


_CONTEXTS = ("docked", "handheld")


def _is_context_keyed(entry) -> bool:
    """True if `entry` is context-keyed (every top-level key is a context). An empty entry counts
    as new; a legacy flat entry keyed by player number ("1".."4") is False."""
    return isinstance(entry, dict) and all(k in _CONTEXTS for k in entry)


def _entry_slices(entry) -> dict:
    """Every context's clean per-player binds, normalised to {context: {player_str: {key: token}}}.
    A legacy flat entry ({player_str: {...}}) folds under "docked". Empty/junk contexts dropped."""
    if not isinstance(entry, dict):
        return {}
    if _is_context_keyed(entry):
        return {c: s for c in _CONTEXTS if (s := _clean_entry(entry.get(c)))}
    flat = _clean_entry(entry)
    return {"docked": flat} if flat else {}


def _entry_slice(entry, context) -> dict:
    """The clean per-player binds for one context ({player_str: {key: token}}); legacy flat =
    docked, unset context => {} (=> the game inherits the global map only)."""
    return _entry_slices(entry).get(handheld_input.normalize(context), {})


def binds_for(serial: str, context="docked") -> dict:
    """{player_str: {key: token}} for a serial+context (clean), or {}. Public: the launch router
    (lib/switch_bind) layers these over the global map (per-game wins). Handheld never inherits a
    docked per-game map -- an unset handheld context yields {} (=> global/stock only)."""
    if not serial or not _SERIAL_RE.match(serial):
        return {}
    return _entry_slice(_load().get(serial), context)


# ── helpers ────────────────────────────────────────────────────────────────────
def _serial(params) -> str:
    s = params.get("titleid") or ""
    if not _SERIAL_RE.match(s):
        raise RpcError("EINVAL", f"bad game id {s!r}")
    return s


def _player(params) -> str:
    p = str(params.get("player") or "1")
    return p if p in _PLAYER_IDS else "1"


def _ctx(params) -> str:
    """The docked/handheld context this page targets (params["context"], default docked). The
    On-the-go PS3 door sends "handheld"; the docked Standalones door omits it."""
    return handheld_input.normalize(params.get("context", "docked"))


def _global_source(player: int, key: str, context="docked") -> str:
    """The resolved GLOBAL binding for player+key in `context` = the global MAD per-player override
    for that context, else the canonical SDL default. The value a per-game row inherits when it has
    no per-game override -- a handheld per-game row inherits the handheld global, not docked."""
    ov = rpcs3_cfg.load_overrides(context=context).get(player, {})
    return ov.get(key) or _DEFAULT_CONFIG.get(key) or ""


# ── buffered editor plumbing (X=Save / Y=Cancel) ────────────────────────────────
def _apply(entry: dict, edit: dict) -> dict:
    """Apply one staged edit to a serial's ENTRY ({player_str: {key: token}}) in memory. Pure:
    no disk I/O, no bump. Replayed onto a FRESH store read by _buf_flush so a foreign edit to the
    entry's other players survives."""
    player, key = edit["player"], edit["id"]
    if edit["op"] == "clear":
        slot = entry.get(player)
        if isinstance(slot, dict):
            slot.pop(key, None)
            if not slot:
                entry.pop(player, None)
        return entry
    token = _token_for(key, edit["kind"], str(edit.get("value", "")))   # validates; raises EINVAL
    entry.setdefault(player, {})[key] = token
    return entry


def _buf_load(ctx: tuple) -> dict:
    (serial, context) = ctx
    with _LOCK:
        return copy.deepcopy(_entry_slice(_load().get(serial), context))


def _buf_apply_edit(entry: dict, edit: dict):
    return _apply(entry, edit), edit


def _buf_flush(ctx: tuple, disk: dict, edits: list) -> dict:
    (serial, context) = ctx
    context = handheld_input.normalize(context)
    with _LOCK:
        data = _load()                                  # FRESH whole store (foreign games survive)
        slices = _entry_slices(data.get(serial))        # {context: slice}; the OTHER context preserved
        entry = copy.deepcopy(slices.get(context, {}))  # the slice we edit ({player_str: {key: token}})
        for edit in edits:                              # replay only OUR edits onto the fresh slice
            entry = _apply(entry, edit)
        entry = _clean_entry(entry)
        if entry:
            slices[context] = entry
        else:
            slices.pop(context, None)                   # emptied this context -> drop it
        if slices:
            data[serial] = {c: slices[c] for c in _CONTEXTS if slices.get(c)}
        else:
            data.pop(serial, None)                      # both contexts empty -> drop (inherits global)
        _save(data)
    return entry


_buf = InputBuffer(load=_buf_load, apply_edit=_buf_apply_edit, flush=_buf_flush)


# ── RPC ─────────────────────────────────────────────────────────────────────────
@method("rpcs3pgin.input_get", slow=True)
def _input_get(params):
    serial = _serial(params)
    player = _player(params)
    pint = int(player)
    context = _ctx(params)
    entry = _buf.get((serial, context))                 # buffer-over-disk: reflects staged edits
    binds = entry.get(player, {}) if isinstance(entry, dict) else {}

    def row(key, label, kind):
        tok = binds.get(key) or _global_source(pint, key, context)
        return {"id": key, "label": label, "kind": kind,
                "value": _display(tok), "capturable": True}   # combo-aware (global PS combo shows as "Select + Start")

    groups = [
        {"title": "Buttons", "binds": [row(k, l, "btn") for k, l in _BUTTONS]},
        {"title": "D-pad", "binds": [row(k, l, "hat") for k, l in _DPAD]},
        {"title": "Analog sticks", "binds": [row(k, l, "axis") for k, l in _STICKS]},
    ]
    players = [{"id": str(n), "label": f"Player {n}"} for n in range(1, _PLAYERS_MAX + 1)]
    note = (f"Per-game remap for Player {player} — applied over your global controller map when "
            "you launch THIS game, and reverted on exit. Blank = inherit the global mapping.")
    # No running/EBUSY gate: writes only our JSON store (never RPCS3's config); applied next launch.
    return {"running": False, "note": note, "groups": groups, "clearable": True,
            "players": players, "player": player, "buffered": True, "dirty": _buf.dirty}


@method("rpcs3pgin.input_set", slow=True)
def _input_set(params):
    serial = _serial(params)
    key, kind = params.get("id", ""), params.get("kind", "btn")
    player = _player(params)
    _buf.set((serial, _ctx(params)), {"op": "set", "player": player, "id": key, "kind": kind,
                                      "value": str(params.get("value", ""))})
    tok = _buf.working.get(player, {}).get(key, "")
    disp = _display(tok)
    return {"id": key, "value": disp, "message": f"{key} → {disp}", "dirty": _buf.dirty}


@method("rpcs3pgin.input_clear", slow=True)
def _input_clear(params):
    """Unbind one per-game button — the 'focus a row, press Start' clear. Stages removal of the
    per-game remap so the button inherits the global binding again; committed on Save."""
    serial = _serial(params)
    key = params.get("id") or params.get("key") or ""
    if key not in _ALL_KEYS:
        raise RpcError("EINVAL", f"{key!r} is not a remappable RPCS3 input")
    player = _player(params)
    context = _ctx(params)
    _buf.set((serial, context), {"op": "clear", "player": player, "id": key})
    src = _global_source(int(player), key, context)
    return {"id": key, "value": _display(src),
            "message": f"{key} reset to global", "dirty": _buf.dirty}


@method("rpcs3pgin.input_save", slow=True)
def _input_save(params):
    return {"saved": _buf.save((_serial(params), _ctx(params))), "dirty": _buf.dirty}


@method("rpcs3pgin.input_cancel", slow=True)
def _input_cancel(params):
    _buf.cancel((_serial(params), _ctx(params)))
    return {"cancelled": True, "dirty": _buf.dirty}


@method("rpcs3pgin.games", slow=True)
def _games(params):
    store = _load()
    out = []
    for g in rpcs3_games.games():
        override = bool(_entry_slices(store.get(g["key"])))   # any context = a custom input badge
        out.append({"titleid": g["key"], "name": g["name"], "stem": rpcs3_games.stem_of(g["path"]),
                    "override": override, "summary": "Custom input" if override else ""})
    return {"games": out, "system": "ps3"}
