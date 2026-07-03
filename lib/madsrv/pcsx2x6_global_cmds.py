"""pcsx2x6 Global settings page (per member) -- PCSX2's Controllers "Global Settings" tab:
SDL input source, DualSense LED, mouse mapping, controller multitap. All bool, verified vs the
fork source ControllerGlobalSettingsWidget.cpp. Immediate writes (like PCSX2's own UI), refused
while pcsx2x6 runs.

Why a bespoke page instead of cfgutil.do_get/do_set: one control ([UI] EnableMouseMapping) is
ABSENT from the fork inis by default, and cfgutil omits absent keys on get + refuses to create on
set. Here an absent key renders its DEFAULT and CREATES on write (cfgutil.ini_set_or_insert).

Multitap ([Pad] MultitapPort1/2) is edited here AND honored by the launch router
(pcsx2_cfg.assign_devices preserve_multitap, wired in switch_bind for pcsx2x6/ps2guncon), so the
toggle sticks across launches instead of being derived from the pad count.

Namespaces: x6a_global (arcade ini), x6r_global (retail -datapath ini).
"""
from __future__ import annotations

from pathlib import Path

from .. import proc_guard, staterev
from . import cfgutil
from .rpc import RpcError, method

_PROC = "pcsx2x6"
_INIS = {
    "x6a": Path("~/Applications/pcsx2x6/PCSX2x6/inis/PCSX2.ini").expanduser(),
    "x6r": Path("~/Applications/pcsx2x6-retail/PCSX2x6/inis/PCSX2.ini").expanduser(),
}

# (title, [(section, key, label, default_on)]) -- all bool.
_GROUPS = [
    ("SDL Input Source", [
        ("InputSources", "SDL", "Enable SDL Input Source", True),
        ("InputSources", "SDLControllerEnhancedMode", "DualShock 4 / DualSense Enhanced Mode", True),
        ("InputSources", "SDLPS5PlayerLED", "DualSense Player LED", True),
    ]),
    ("Mouse / Pointer", [
        ("UI", "EnableMouseMapping", "Enable Mouse Mapping (mouse as analog)", False),
    ]),
    ("Controller Multitap", [
        ("Pad", "MultitapPort1", "Multitap on Console Port 1", False),
        ("Pad", "MultitapPort2", "Multitap on Console Port 2", False),
    ]),
]
_ITEMS = {f"{sec}/{key}": (sec, key) for _t, items in _GROUPS for sec, key, _l, _d in items}


def _running() -> bool:
    return proc_guard.emulator_running(_PROC)


def _get(ini: Path) -> dict:
    text = cfgutil.read_text(ini) or ""
    run = _running()
    groups = []
    for title, items in _GROUPS:
        rows = []
        for sec, key, label, default in items:
            raw = cfgutil.ini_read(text, sec, key)
            val = default if raw is None else (raw.strip().lower() in cfgutil._TRUE)
            rows.append({"key": f"{sec}/{key}", "label": label, "type": "bool", "value": val})
        groups.append({"title": title, "note": "", "settings": rows})
    note = ("Close pcsx2x6 first, it rewrites this file on exit." if run else
            "PCSX2 global controller settings. Multitap is honored by MAD at launch. "
            "Changes save instantly (a one-time backup is made before the first change).")
    return {"exists": True, "running": run, "note": note, "groups": groups}


def _set(ini: Path, params: dict) -> dict:
    if _running():
        raise RpcError("EBUSY", "close pcsx2x6 first; it rewrites its config on exit")
    key = params.get("key", "")
    if key not in _ITEMS:
        raise RpcError("EINVAL", f"{key!r} is not a global setting")
    sec, k = _ITEMS[key]
    on = str(params.get("value", "")).strip().lower() in cfgutil._TRUE
    tok = "true" if on else "false"
    text = cfgutil.read_text(ini)
    if text is None:
        raise RpcError("ENOENT", f"{ini.name} not found — launch a game once to create it.")
    new = cfgutil.ini_set_or_insert(text, sec, k, tok)
    if new is None:                 # section absent -> create it, then set the key
        base = text + ("" if not text or text.endswith("\n") else "\n") + f"[{sec}]\n"
        new = cfgutil.ini_set_or_insert(base, sec, k, tok)
    if new is None:
        raise RpcError("EIO", f"could not write [{sec}] {k}")
    if new != text:
        cfgutil.ensure_bak(ini)
        cfgutil.atomic_write(ini, new)
    staterev.bump("config")
    return {"key": key, "value": on}


def _register(prefix: str) -> None:
    @method(f"{prefix}_global.get", slow=True)
    def _g(params, prefix=prefix):
        return _get(_INIS[prefix])

    @method(f"{prefix}_global.set", slow=True)
    def _s(params, prefix=prefix):
        return _set(_INIS[prefix], params)


for _p in _INIS:
    _register(_p)
