"""pcsx2x6 Hotkeys (per member) -- the flat [Hotkeys] section of each fork ini. Reuses the standard
pcsx2hk logic (action list, chord token rendering, unknown-key preservation, key validation, the pure
edit-applier + buffer factory) from pcsx2_hotkeys_cmds, pointed at the fork inis + the pcsx2x6 process
guard. Namespaces x6a_hk / x6r_hk.

Buffered X=Save / Y=Cancel editor: input_set/input_clear only STAGE (nothing hits disk); input_save
commits (once, bumping staterev "config"); input_cancel reverts. Byte-preserving, multi-line aware
(collapse alt lines on rebind), unknown/foreign [Hotkeys] keys preserved. Refuses while pcsx2x6 runs
(it rewrites its ini on exit) -- the guard fires at both stage and save.

KEYING: x6a (arcade) and x6r (retail) write DIFFERENT inis, so each prefix gets its OWN InputBuffer
(each keyed on its ini path). Staging arcade hotkeys never touches the retail buffer, and vice versa --
a single shared ctx=() buffer would be wrong here.
"""
from __future__ import annotations

from pathlib import Path

from .. import proc_guard
from . import cfgutil
from .pcsx2_hotkeys_cmds import (_ACTIONS, _SECTION, _binding_from_params, _render_value,
                                 _unknown_keys, make_hotkey_buffer)
from .rpc import method

_PROC = "pcsx2x6"
_INIS = {
    "x6a": Path("~/Applications/pcsx2x6/PCSX2x6/inis/PCSX2.ini").expanduser(),
    "x6r": Path("~/Applications/pcsx2x6-retail/PCSX2x6/inis/PCSX2.ini").expanduser(),
}


def _running() -> bool:
    return proc_guard.emulator_running(_PROC)


# One buffer per fork ini, keyed on that ini's PATH, so arcade + retail hotkeys stage
# independently and never share state. `running` resolves THIS module's _running at call time
# (a test can swap it); the guard/messages name "pcsx2x6".
_BUFS = {p: make_hotkey_buffer(running=lambda: _running(), proc=_PROC) for p in _INIS}


def _get(ini: Path, buf) -> dict:
    text = buf.get(ini)                 # buffer-over-disk: reflects staged, unsaved edits
    run = _running()

    def row(key, label):
        vals = cfgutil.ini_read_all(text, _SECTION, key)
        value = " / ".join(_render_value(v) for v in vals) if vals else "—"
        return {"id": key, "label": label, "kind": "chord", "value": value, "capturable": not run}

    groups = [{"title": title, "binds": [row(k, l) for k, l in binds]}
              for title, binds in _ACTIONS]
    extra = _unknown_keys(text)
    if extra:
        groups.append({"title": "Other (set in pcsx2x6)", "binds": [row(k, k) for k in extra]})
    note = ("Close pcsx2x6 first, it rewrites this file on exit." if run else
            "Bind each action to a keyboard key/combo or a controller button/chord (hold them "
            "together). Highlight a row and press Start to clear it.")
    return {"running": run, "note": note, "groups": groups, "clearable": True,
            "buffered": True, "dirty": buf.dirty}


def _input_set(ini: Path, buf, params: dict) -> dict:
    key = params.get("id", "")
    binding, shown = _binding_from_params(params)
    buf.set(ini, {"op": "set", "id": key, "binding": binding})     # stage in memory; no disk write
    return {"id": key, "value": shown, "dirty": buf.dirty, "message": f"{key} → {shown}"}


def _input_clear(ini: Path, buf, params: dict) -> dict:
    key = params.get("id") or params.get("key") or ""
    buf.set(ini, {"op": "clear", "id": key})                       # stage in memory; no disk write
    return {"id": key, "value": "—", "dirty": buf.dirty, "message": f"{key} cleared"}


def _register(prefix: str) -> None:
    @method(f"{prefix}_hk.input_get", slow=True)   # buffered: NO cache=("config",) — the buffer is the cache
    def _g(params, prefix=prefix):
        return _get(_INIS[prefix], _BUFS[prefix])

    @method(f"{prefix}_hk.input_set", slow=True)
    def _s(params, prefix=prefix):
        return _input_set(_INIS[prefix], _BUFS[prefix], params)

    @method(f"{prefix}_hk.input_clear", slow=True)
    def _c(params, prefix=prefix):
        return _input_clear(_INIS[prefix], _BUFS[prefix], params)

    @method(f"{prefix}_hk.input_save", slow=True)
    def _sv(params, prefix=prefix):
        buf = _BUFS[prefix]
        return {"saved": buf.save(_INIS[prefix]), "dirty": buf.dirty}

    @method(f"{prefix}_hk.input_cancel", slow=True)
    def _cn(params, prefix=prefix):
        buf = _BUFS[prefix]
        buf.cancel(_INIS[prefix])
        return {"cancelled": True, "dirty": buf.dirty}


for _p in _INIS:
    _register(_p)
