"""guncon2_retail.* — the retail PS2 GunCon2 configuration page (a SINGLE unified page).

The retail lightgun co-op setup runs the pcsx2x6 fork with
`-datapath ~/Applications/pcsx2x6-retail`, so its config is a SEPARATE ini from the Namco
arcade portable one. This one page carries everything for the retail GunCon2:

  • the button BINDINGS (D-Pad, Trigger, Shoot Offscreen, Calibration Shot, A/B/C, Start,
    Select) as gun-capture rows written to [USB1]/[USB2],
  • the per-gun CROSSHAIR image + size as dropdown SELECTORS (player-scoped -> the
    selected gun's [USBn]),
  • a DYNAMIC Start/Stop Sinden guns button (label + action follow the live driver state).

Relative-aim keys are never offered (binding any freezes the cursor). pcsx2x6 rewrites
its ini on EXIT, so writes refuse while it is running.
"""
from __future__ import annotations

from pathlib import Path

from .. import proc_guard, staterev
from . import cfgutil, sinden_cmds
from .input_translate import usb_keyboard_source, usb_mouse_button_source, usb_source_label
from .rpc import RpcError, method

_INI = Path("~/Applications/pcsx2x6-retail/PCSX2x6/inis/PCSX2.ini").expanduser()
_CROSSHAIR_DIR = _INI.parent.parent / "crosshairs"   # ~/Applications/pcsx2x6-retail/PCSX2x6/crosshairs

# ── button bindings (the arcade "Light Gun" device trims most of these) ──────────
# Relative* deliberately omitted: binding any guncon2-retail_Relative* freezes the cursor.
_DPAD = [("guncon2-retail_Up", "D-pad Up"), ("guncon2-retail_Down", "D-pad Down"),
         ("guncon2-retail_Left", "D-pad Left"), ("guncon2-retail_Right", "D-pad Right")]
_TRIGGER = [("guncon2-retail_Trigger", "Trigger"),
            ("guncon2-retail_ShootOffscreen", "Shoot Offscreen"),
            ("guncon2-retail_Recalibrate", "Calibration Shot")]
_BUTTONS = [("guncon2-retail_A", "A"), ("guncon2-retail_B", "B"), ("guncon2-retail_C", "C"),
            ("guncon2-retail_Start", "Start"), ("guncon2-retail_Select", "Select")]
_ALL_BINDS = _DPAD + _TRIGGER + _BUTTONS
_BIND_KEYS = {k for k, _ in _ALL_BINDS}
_LABELS = dict(_ALL_BINDS)

# ── crosshair SELECTORS (dropdowns) ─────────────────────────────────────────────
# NOTE: no Sinden-border control here. The software border only renders in ACJV LIGHTGUN
# mode (ImGuiOverlays DrawSindenBorder), which is set ONLY by the arcade .ACGAME handler
# (VMManager) - a retail PS2 disc never enters it, so a border toggle would be a dead
# control. (This rig uses a physical LED border anyway.)
_SIZE_OPTS = [("0.05", "Small (0.05)"), ("0.08", "Medium (0.08)"),
              ("0.12", "Large (0.12)"), ("0.2", "X-Large (0.2)")]

_PLAYER_PICK = [
    {"id": "usb1", "label": "USB Port 1 (Gun 1)"},
    {"id": "usb2", "label": "USB Port 2 (Gun 2)"},
]
_PICK_IDS = {p["id"] for p in _PLAYER_PICK}


def _running() -> bool:
    return proc_guard.process_running("pcsx2x6", exact=False)


def _sel(params) -> str:
    s = (params.get("player") or "usb1").strip()
    return s if s in _PICK_IDS else "usb1"


def _usb_section(sel: str) -> str:
    return "USB" + sel[-1]            # usb1 -> USB1


def _crosshair_options() -> tuple[list[str], list[str]]:
    """(display stems, absolute paths) for each .png in the retail crosshairs dir."""
    try:
        pngs = sorted(_CROSSHAIR_DIR.glob("*.png"))
    except OSError:
        pngs = []
    return [p.stem for p in pngs], [str(p) for p in pngs]


def _opts(pairs):
    return [{"value": v, "label": l} for v, l in pairs]


def _selectors(text: str, section: str) -> list:
    """Per-gun crosshair image + size (player-scoped -> the selected gun's [USBn])."""
    disp, stored = _crosshair_options()
    sels = []
    if stored:                                   # only offer the image picker if PNGs exist
        cur = (cfgutil.ini_read(text, section, "guncon2-retail_cursor_path") or "").strip()
        sels.append({"key": "cursor_path", "label": "Crosshair image", "scope": "player",
                     "value": cur, "dependent": False,
                     "options": [{"value": p, "label": d} for d, p in zip(disp, stored)]})
    sels.append({"key": "cursor_scale", "label": "Crosshair size", "scope": "player",
                 "value": (cfgutil.ini_read(text, section, "guncon2-retail_cursor_scale")
                           or "0.08").strip(),
                 "dependent": False, "options": _opts(_SIZE_OPTS)})
    return sels


# selector key -> ini key. All crosshair selectors are player-scoped (written to the
# selected gun's USB section by selector_set).
_PLAYER_SEL = {
    "cursor_path": "guncon2-retail_cursor_path",
    "cursor_scale": "guncon2-retail_cursor_scale",
}


def _get(sel: str, run: bool) -> dict:
    text = _INI.read_text(encoding="utf-8", errors="replace") if _INI.is_file() else ""
    section = _usb_section(sel)

    def gun_row(key, label):
        src = (cfgutil.ini_read(text, section, key) or "").strip()
        return {"id": key, "label": label, "kind": "gun",
                "value": usb_source_label(src) if src else "—", "capturable": not run}

    groups = [
        {"title": "D-Pad", "binds": [gun_row(k, l) for k, l in _DPAD]},
        {"title": "Trigger", "binds": [gun_row(k, l) for k, l in _TRIGGER]},
        {"title": "Buttons", "binds": [gun_row(k, l) for k, l in _BUTTONS]},
    ]
    note = ("Close pcsx2x6 first, it rewrites this file on exit." if run else
            "Retail GunCon2 (Gun " + sel[-1] + "): set the crosshair, then bind the trigger, "
            "D-pad and buttons. The Sinden gun uses USB Port " + sel[-1] + "; a binding targets "
            "that port's pointer slot, so pull any gun's trigger / press any key.")
    # DYNAMIC Start/Stop button: label + action follow the LIVE driver state at page-open
    # (input_get is uncached, so re-opening reflects the flip). The C++ button fires
    # sinden.driver directly and flashes its message.
    guns_up = sinden_cmds._driver_running()
    actions = [{"type": "action", "key": "sinden_toggle",
                "label": "⏹ Stop Sinden guns" if guns_up else "▶ Start Sinden guns",
                "rpc": "sinden.driver",
                "args": {"action": "stop" if guns_up else "start"}}]
    return {"running": run, "note": note, "groups": groups,
            "selectors": _selectors(text, section), "actions": actions,
            "players": _PLAYER_PICK, "player": sel}


# Uncached (unlike the arcade page): the Start/Stop Sinden button reflects the live driver
# state, so the page must recompute on each open rather than serve a config-keyed cache.
@method("guncon2_retail.input_get", slow=True)
def _input_get(params):
    return _get(_sel(params), _running())


def _write(section: str, key: str, source: str) -> None:
    if not _INI.is_file():
        raise RpcError("ENOENT", "retail pcsx2x6 config not found - launch a retail game once")
    if _running():
        raise RpcError("EBUSY", "close pcsx2x6 first; it rewrites its config on exit")
    text = _INI.read_text(encoding="utf-8", errors="replace")
    new = cfgutil.ini_set_or_insert(text, section, key, source)
    if new is None:
        raise RpcError("ENOKEY", f"[{section}] section not found in the config")
    if new != text:
        cfgutil.ensure_bak(_INI)
        cfgutil.atomic_write(_INI, new)
    staterev.bump("config")


@method("guncon2_retail.input_set", slow=True)
def _input_set(params):
    sel = _sel(params)
    key = params.get("id", "")
    if key not in _BIND_KEYS:
        raise RpcError("EINVAL", f"{key!r} is not a remappable retail GunCon2 input")
    gun_kind = params.get("gun_kind", "")
    value = params.get("value", "")
    if gun_kind == "mouse":
        try:
            source = usb_mouse_button_source(int(value), int(sel[-1]) - 1)
        except (TypeError, ValueError):
            source = None
    elif gun_kind == "key":
        source = usb_keyboard_source(str(value))
    else:
        raise RpcError("EINVAL", "press a mouse button or a key")
    if source is None:
        raise RpcError("EINVAL", "that input can't be mapped to this control")
    _write(_usb_section(sel), key, source)
    return {"id": key, "value": usb_source_label(source),
            "message": f"{_LABELS.get(key, key)} → {usb_source_label(source)}"}


@method("guncon2_retail.selector_set", slow=True)
def _selector_set(params):
    key = params.get("key", "")
    value = str(params.get("value", "")).strip()
    if key not in _PLAYER_SEL:                    # crosshair image/size -> the selected gun's USB section
        raise RpcError("EINVAL", f"unknown selector {key!r}")
    _write(_usb_section(_sel(params)), _PLAYER_SEL[key], value)
    return {"key": key, "value": value, "message": f"{_PLAYER_SEL[key]} → {value}"}
