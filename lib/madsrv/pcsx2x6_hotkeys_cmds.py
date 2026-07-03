"""pcsx2x6 Hotkeys (per member) -- the flat [Hotkeys] section of each fork ini. Reuses the standard
pcsx2hk logic (action list, chord token rendering, unknown-key preservation, key validation) from
pcsx2_hotkeys_cmds, pointed at the fork inis + the pcsx2x6 process guard. Namespaces x6a_hk / x6r_hk.

Rendered by the generic input_map page; every row is kind "chord" so the capture modal accumulates
simultaneously-held inputs. Byte-preserving, multi-line aware (collapse alt lines on rebind). Refuses
while pcsx2x6 runs (it rewrites its ini on exit).
"""
from __future__ import annotations

from pathlib import Path

from .. import proc_guard, staterev
from . import cfgutil
from .pcsx2_hotkeys_cmds import (_ACTIONS, _SECTION, _render_token, _render_value,
                                 _unknown_keys, _valid_key)
from .rpc import RpcError, method

_PROC = "pcsx2x6"
_INIS = {
    "x6a": Path("~/Applications/pcsx2x6/PCSX2x6/inis/PCSX2.ini").expanduser(),
    "x6r": Path("~/Applications/pcsx2x6-retail/PCSX2x6/inis/PCSX2.ini").expanduser(),
}


def _running() -> bool:
    return proc_guard.emulator_running(_PROC)


def _get(ini: Path) -> dict:
    if not ini.is_file():
        raise RpcError("ENOENT", f"pcsx2x6 config not found at {ini}")
    text = ini.read_text(encoding="utf-8", errors="replace")
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
    return {"running": run, "note": note, "groups": groups, "clearable": True}


def _write(ini: Path, key: str, binding: str) -> None:
    if not ini.is_file():
        raise RpcError("ENOENT", f"pcsx2x6 config not found at {ini}")
    if _running():
        raise RpcError("EBUSY", "close pcsx2x6 first; it rewrites its config on exit")
    orig = ini.read_text(encoding="utf-8", errors="replace")
    if not _valid_key(key, orig):
        raise RpcError("EINVAL", f"{key!r} is not a PCSX2 hotkey action")
    text = orig
    # collapse pre-existing alternative binding lines so a rebind leaves exactly one.
    if len(cfgutil.ini_read_all(text, _SECTION, key)) > 1:
        text = cfgutil.ini_remove_all(text, _SECTION, key)
    new = cfgutil.ini_set_or_insert(text, _SECTION, key, binding)
    if new is None:                       # [Hotkeys] absent — create it, then insert
        base = text + ("" if not text or text.endswith("\n") else "\n") + f"[{_SECTION}]\n"
        new = cfgutil.ini_set_or_insert(base, _SECTION, key, binding)
    if new is None:
        raise RpcError("EIO", "could not write the [Hotkeys] section")
    if new != orig:
        cfgutil.ensure_bak(ini)
        cfgutil.atomic_write(ini, new)
    staterev.bump("config")


def _input_set(ini: Path, params: dict) -> dict:
    key = params.get("id", "")
    codes = params.get("codes")
    if codes is None and str(params.get("value", "")).strip():
        try:
            codes = [int(params.get("value"))]
        except (TypeError, ValueError):
            codes = None
    if not codes:
        raise RpcError("EINVAL", "press a key or button, or hold a chord")
    tokens = []
    for c in codes:
        try:
            tok = _render_token(int(c))
        except (TypeError, ValueError):
            tok = None
        if tok is None:
            raise RpcError("EINVAL", "that input can't be bound as a hotkey")
        tokens.append(tok)
    binding = " & ".join(tokens)
    _write(ini, key, binding)
    return {"id": key, "value": _render_value(binding), "message": f"{key} → {_render_value(binding)}"}


def _input_clear(ini: Path, params: dict) -> dict:
    key = params.get("id") or params.get("key") or ""
    if not ini.is_file():
        raise RpcError("ENOENT", f"pcsx2x6 config not found at {ini}")
    if _running():
        raise RpcError("EBUSY", "close pcsx2x6 first; it rewrites its config on exit")
    text = ini.read_text(encoding="utf-8", errors="replace")
    if not _valid_key(key, text):
        raise RpcError("EINVAL", f"{key!r} is not a PCSX2 hotkey action")
    new = cfgutil.ini_remove_all(text, _SECTION, key)
    if new != text:
        cfgutil.ensure_bak(ini)
        cfgutil.atomic_write(ini, new)
    staterev.bump("config")
    return {"id": key, "value": "—", "message": f"{key} cleared"}


def _register(prefix: str) -> None:
    @method(f"{prefix}_hk.input_get", slow=True, cache=("config",))
    def _g(params, prefix=prefix):
        return _get(_INIS[prefix])

    @method(f"{prefix}_hk.input_set", slow=True)
    def _s(params, prefix=prefix):
        return _input_set(_INIS[prefix], params)

    @method(f"{prefix}_hk.input_clear", slow=True)
    def _c(params, prefix=prefix):
        return _input_clear(_INIS[prefix], params)


for _p in _INIS:
    _register(_p)
