"""onthego.* — the MAD "On-the-go" control page (handheld auto-profiles).

Policy-backed (controller-policy.local.toml via localpolicy), NOT cfgutil/INI. Provides:
  onthego.list           -> the sidebar chooser tree (Global + Per-system)
  onthego_global.get/set -> master enabled + detection mode + default watt cap  ([handheld])
  onthego_<sys>.get/set  -> per-system enable + watt cap + resolution  ([systems.<sys>.handheld])
The generic C++ pages render these: GuiMadPageStandaloneSections (Fetch chooser on "onthego.list")
-> GuiMadPageEmuSettings ("<ns>.get"/"<ns>.set") for each leaf. localpolicy.dump bumps
staterev("config"), so the page reloads after any write (no extra handling here). Mirrors the
policy-backed pattern in citron_dock_cmds.py; see memory onthego-handheld-profiles.
"""
from __future__ import annotations

from .rpc import RpcError, method

# system key -> (display name, res-capable?). res-capable = has a numeric internal-res knob the
# on-the-go rails drive. Switch res = the per-emu Dock-detection toggle (not here); Wii U (Cemu)
# resolution is curated per title (graphic packs), so neither exposes a `res` row.
_SYSTEMS = [
    ("switch",     "Nintendo Switch", False),
    ("ps3",        "PlayStation 3",   True),
    ("ps2",        "PlayStation 2",   True),
    ("gc",         "GameCube",        True),
    ("wii",        "Wii",             True),
    ("wiiu",       "Wii U",           False),
    ("psx",        "PlayStation 1",   True),
    ("n64",        "Nintendo 64",     True),
    ("saturn",     "Sega Saturn",     True),
    ("dreamcast",  "Dreamcast",       True),
    ("naomi",      "Sega NAOMI",      True),
    ("atomiswave", "Atomiswave",      True),
    ("daphne",     "Daphne",          False),
    ("lindbergh",  "Sega Lindbergh",  False),
]
_WATT_MIN, _WATT_MAX, _WATT_DEFAULT = 4, 15, 12
_MODE_OPTS = ["Auto (physical display)", "Force handheld", "Force docked"]
# One uniform handheld-resolution ladder for EVERY res-capable system: an abstract multiplier that
# the backend-aware rail (lib/handheld_res) snaps DOWN to whatever the launching emulator actually
# supports (a core with no 3x rung uses 2x). Stored as the token; back-compatible with the old
# native/2x/inherit values.
_RES_OPTS = [("native", "Native (1x)"), ("2x", "2x"), ("3x", "3x"), ("4x", "4x"),
             ("6x", "6x"), ("8x", "8x"), ("inherit", "Inherit (leave as-is)")]
_RES_TOKENS = [t for t, _ in _RES_OPTS]
_RES_LABELS = [d for _, d in _RES_OPTS]


def _truthy(v) -> bool:
    return str(v).strip().lower() in ("1", "true", "on", "yes")


def _int_or(v, default):
    try:
        return int(float(str(v).strip()))
    except (TypeError, ValueError):
        return default


# ── policy read/write ────────────────────────────────────────────────────────
def _merged() -> dict:
    from ..policy import load_merged
    m = load_merged()
    return m if isinstance(m, dict) else {}


def _hh() -> dict:
    hh = _merged().get("handheld")
    return hh if isinstance(hh, dict) else {}


def _sys_hh(sys: str) -> dict:
    systems = _merged().get("systems")
    sysd = systems.get(sys) if isinstance(systems, dict) else None
    hh = sysd.get("handheld") if isinstance(sysd, dict) else None
    return hh if isinstance(hh, dict) else {}


def _write(path_keys, key, value, *, remove=False) -> None:
    """Set/clear one key under a nested [path_keys] table in controller-policy.local.toml.
    localpolicy.dump does the atomic write + staterev.bump('config')."""
    from .. import localpolicy
    from ..policy import LOCAL
    data = localpolicy.load(LOCAL)
    blk = data
    for k in path_keys:
        blk = blk.setdefault(k, {})
    if remove:
        blk.pop(key, None)
    else:
        blk[key] = value
    localpolicy.dump(LOCAL, data)


def _sys_section(sys: str, name: str) -> dict:
    """One Per-system row. Most systems are a single settings page; a few FOLD into a small group.
    Wii U adds a dynamic per-game resolution browser. Daphne + Lindbergh have no res knob but DO have
    an existing MAD input page, so their row becomes a group [Settings (watt cap), Input] -- the Input
    leaf reuses the standalone dispatch (daphne_map / lindbergh_pads), which works nested here because
    the On-the-go chooser IS a GuiMadPageStandaloneSections (same madOpenStandaloneTarget path)."""
    page = {"label": name, "sublabel": "", "kind": "settings",
            "arg": f"onthego_{sys}", "title": f"{name} - On-the-go"}
    settings_leaf = {**page, "label": "Settings", "sublabel": "include in on-the-go + watt cap"}
    if sys == "wiiu":
        return {"label": name, "sublabel": "", "kind": "group",
                "arg": "", "title": f"{name} - On-the-go", "sections": [
                    settings_leaf,
                    {"label": "Resolution", "sublabel": "per-game handheld resolution (graphic packs)",
                     "kind": "settings_pergame", "arg": "cemures", "title": "Wii U handheld resolution"}]}
    if sys == "daphne":
        return {"label": name, "sublabel": "", "kind": "group",
                "arg": "", "title": f"{name} - On-the-go", "sections": [
                    settings_leaf,
                    {"label": "Input", "sublabel": "Deck buttons for handheld (docked untouched)",
                     "kind": "settings", "arg": "daphne_handheld", "title": f"{name} - Handheld input"}]}
    if sys == "lindbergh":
        return {"label": name, "sublabel": "", "kind": "group",
                "arg": "", "title": f"{name} - On-the-go", "sections": [
                    settings_leaf,
                    {"label": "Input", "sublabel": "pads to players (per game)",
                     "kind": "lindbergh_pads", "arg": "lindbergh", "title": f"{name} - Controllers"}]}
    return page


# ── sidebar chooser tree ─────────────────────────────────────────────────────
@method("onthego.list", slow=True)
def _list(params):
    from .systems_cmds import resolve_art
    icon = resolve_art(["icons/on-the-go.png"])
    # Per-system rows are listed alphabetically by display name (Atomiswave..Wii U), not in the
    # _SYSTEMS declaration order, so the chooser reads predictably.
    per_sys = [_sys_section(sys, name)
               for sys, name, _res in sorted(_SYSTEMS, key=lambda t: t[1].lower())]
    sections = [
        {"label": "Global", "sublabel": "master switch, detection, default watt cap",
         "kind": "settings", "arg": "onthego_global", "title": "On-the-go — Global"},
        {"label": "Per-system", "sublabel": "enable, watt cap & resolution per system",
         "kind": "group", "arg": "", "title": "On-the-go — Per-system", "sections": per_sys},
        {"label": "RetroArch (handheld)",
         "sublabel": "Deck-pad gameplay binds + hotkey combos",
         "kind": "group", "arg": "", "title": "On-the-go - RetroArch (handheld)", "sections": [
            {"label": "Pad mapping",
             "sublabel": "which Deck button drives each RetroArch button",
             "kind": "settings", "arg": "ra_handheld_pad",
             "title": "RetroArch handheld - Pad mapping"},
            {"label": "Hotkey combos",
             "sublabel": "modifier + rewind / fast-forward / menu / slow-mo",
             "kind": "settings", "arg": "ra_handheld_hk",
             "title": "RetroArch handheld - Hotkey combos"},
         ]},
    ]
    tile = {"key": "on-the-go", "label": "On-the-go", "sublabel": "",
            "art": [icon] if icon else [], "sections": sections}
    return {"tiles": [tile]}


# ── global page ──────────────────────────────────────────────────────────────
@method("onthego_global.get", slow=True)
def _global_get(params):
    hh = _hh()
    detect = str(hh.get("detect", "display")).strip().lower()
    force = str(hh.get("force", "")).strip().lower()
    mode = 1 if (detect == "manual" and force == "handheld") else \
           2 if (detect == "manual" and force == "docked") else 0
    return {
        "exists": True, "running": False,
        "note": "When you play HANDHELD, the systems you enable below get a lower internal "
                "resolution + a TDP watt cap, restored automatically when docked. Detection is "
                "the physical screen; Force is for testing. Tip: keep Steam's per-game TDP slider "
                "off for the ES-DE shortcut so this owns the cap.",
        "groups": [{"title": "On-the-go", "note": "", "settings": [
            {"key": "enabled", "label": "Enable on-the-go profiles", "type": "bool",
             "value": bool(hh.get("enabled", False))},
            {"key": "mode", "label": "Detection", "type": "enum", "value": mode,
             "options": _MODE_OPTS},
            {"key": "default_watt_cap", "label": "Default watt cap (W)", "type": "int",
             "value": _int_or(hh.get("default_watt_cap", _WATT_DEFAULT), _WATT_DEFAULT),
             "min": _WATT_MIN, "max": _WATT_MAX, "step": 1},
        ]}],
    }


@method("onthego_global.set", slow=True)
def _global_set(params):
    key, val = params["key"], params["value"]
    if key == "enabled":
        _write(["handheld"], "enabled", _truthy(val))
    elif key == "mode":
        idx = _int_or(val, 0)
        detect, force = ("manual", "handheld") if idx == 1 else \
                        ("manual", "docked") if idx == 2 else ("display", "")
        _write(["handheld"], "detect", detect)
        _write(["handheld"], "force", force)
    elif key == "default_watt_cap":
        _write(["handheld"], "default_watt_cap",
               max(_WATT_MIN, min(_WATT_MAX, _int_or(val, _WATT_DEFAULT))))
    else:
        raise RpcError("EINVAL", f"unknown key {key!r}")
    return {"key": key, "value": val}


# --- RetroArch (handheld) editors: pad map + hotkey combos ---
# The Deck's built-in pad has NO usable raw-evdev gamepad codes (it needs sdl2), so live
# button-capture is impossible -- both editors are DROPDOWNS over this fixed SDL-GameController
# control set (label, sdl2-index token). Guide (index 5) is intentionally skipped.
_DECK_BTN_OPTS = [("A", "0"), ("B", "1"), ("X", "2"), ("Y", "3"), ("Back/Select", "4"),
                  ("Start", "6"), ("L3 (left stick)", "7"), ("R3 (right stick)", "8"),
                  ("L1 (LB)", "9"), ("R1 (RB)", "10"), ("D-pad Up", "11"), ("D-pad Down", "12"),
                  ("D-pad Left", "13"), ("D-pad Right", "14")]
_DECK_AXIS_OPTS = [("L2 (left trigger)", "+4"), ("R2 (right trigger)", "+5")]
_DECK_BTN_TOKENS = [t for _, t in _DECK_BTN_OPTS]
_DECK_BTN_LABELS = [l for l, _ in _DECK_BTN_OPTS]
_DECK_AXIS_TOKENS = [t for _, t in _DECK_AXIS_OPTS]
_DECK_AXIS_LABELS = [l for l, _ in _DECK_AXIS_OPTS]
# The 14 digital RetroPad functions the pad-map page exposes (row label = the RetroArch button;
# the chosen value = which Deck button drives it). Sticks/triggers stay at shipped defaults (v1).
_PAD_ROWS = [("input_player1_a_btn", "A"), ("input_player1_b_btn", "B"),
             ("input_player1_x_btn", "X"), ("input_player1_y_btn", "Y"),
             ("input_player1_select_btn", "Select"), ("input_player1_start_btn", "Start"),
             ("input_player1_l3_btn", "L3"), ("input_player1_r3_btn", "R3"),
             ("input_player1_l_btn", "L1"), ("input_player1_r_btn", "R1"),
             ("input_player1_up_btn", "D-pad Up"), ("input_player1_down_btn", "D-pad Down"),
             ("input_player1_left_btn", "D-pad Left"), ("input_player1_right_btn", "D-pad Right")]
_PAD_ROW_KEYS = {k for k, _ in _PAD_ROWS}
# [handheld.retroarch] hotkey slots read by ra_handheld_input._SCHEME (field, default, kind, label).
_HK_SLOTS = [
    ("modifier_btn",     "8",  "btn",  "Modifier button (hold)"),
    ("rewind_btn",       "9",  "btn",  "Rewind (+ modifier)"),
    ("fast_forward_btn", "10", "btn",  "Fast-forward (+ modifier)"),
    ("menu_btn",         "4",  "btn",  "Quick menu (+ modifier)"),
    ("slowmotion_axis",  "+5", "axis", "Slow-motion (+ modifier)"),
]


@method("ra_handheld_pad.get", slow=True)
def _pad_get(params):
    from .. import ra_handheld_input as rhi
    ovr = rhi.load_pad_overrides()
    settings = []
    for key, label in _PAD_ROWS:
        cur = str(ovr.get(key, rhi.GAMEPAD_DEFAULTS.get(key, "0")))
        idx = _DECK_BTN_TOKENS.index(cur) if cur in _DECK_BTN_TOKENS else 0
        settings.append({"key": key, "label": label, "type": "enum",
                         "value": idx, "options": _DECK_BTN_LABELS})
    settings.append({"type": "action", "key": "reset",
                     "label": "Reset pad map to defaults (reopen to refresh)",
                     "rpc": "ra_handheld_pad.reset", "args": {}})
    return {"exists": True, "running": False,
            "note": "Choose which Deck button drives each RetroArch button when you play HANDHELD. "
                    "Defaults match a standard pad; change a row only for a custom layout. Applied "
                    "only handheld; your docked binds are untouched and return when you dock.",
            "groups": [{"title": "Gameplay buttons", "note": "", "settings": settings}]}


@method("ra_handheld_pad.set", slow=True)
def _pad_set(params):
    from .. import ra_handheld_input as rhi, staterev
    key = params.get("key", "")
    if key not in _PAD_ROW_KEYS:
        raise RpcError("EINVAL", f"unknown key {key!r}")
    idx = _int_or(params.get("value"), 0)
    tok = _DECK_BTN_TOKENS[idx] if 0 <= idx < len(_DECK_BTN_TOKENS) else _DECK_BTN_TOKENS[0]
    ovr = rhi.load_pad_overrides()
    if tok == str(rhi.GAMEPAD_DEFAULTS.get(key)):
        ovr.pop(key, None)                       # picked the default -> drop the override
    else:
        ovr[key] = tok
    rhi.save_pad_overrides(ovr)
    staterev.bump("config")                      # sidecar isn't policy -> bump so the page reloads
    return {"key": key, "value": params.get("value")}


@method("ra_handheld_pad.reset", slow=True)
def _pad_reset(params):
    from .. import ra_handheld_input as rhi, staterev
    rhi.save_pad_overrides({})                    # empty -> drop sidecar -> shipped defaults
    staterev.bump("config")
    return {"message": "Pad map reset to defaults"}


@method("ra_handheld_hk.get", slow=True)
def _hk_get(params):
    ra = _hh().get("retroarch")
    ra = ra if isinstance(ra, dict) else {}
    settings = []
    for field, dflt, kind, label in _HK_SLOTS:
        tok = str(ra.get(field, dflt) or dflt)
        toks, labels = ((_DECK_BTN_TOKENS, _DECK_BTN_LABELS) if kind == "btn"
                        else (_DECK_AXIS_TOKENS, _DECK_AXIS_LABELS))
        idx = toks.index(tok) if tok in toks else (toks.index(dflt) if dflt in toks else 0)
        settings.append({"key": field, "label": label, "type": "enum",
                         "value": idx, "options": labels})
    settings.append({"type": "action", "key": "reset",
                     "label": "Reset combos to defaults (reopen to refresh)",
                     "rpc": "ra_handheld_hk.reset", "args": {}})
    return {"exists": True, "running": False,
            "note": "Hold the Modifier button, then press another button for rewind / fast-forward "
                    "/ quick menu, or the trigger for slow-motion. Applied only when handheld; each "
                    "hotkey button keeps its normal gameplay use while the modifier is not held.",
            "groups": [{"title": "Deck-pad hotkey combos", "note": "", "settings": settings}]}


@method("ra_handheld_hk.set", slow=True)
def _hk_set(params):
    key = params.get("key", "")
    slot = next((s for s in _HK_SLOTS if s[0] == key), None)
    if slot is None:
        raise RpcError("EINVAL", f"unknown key {key!r}")
    _f, dflt, kind, _l = slot
    toks = _DECK_BTN_TOKENS if kind == "btn" else _DECK_AXIS_TOKENS
    idx = _int_or(params.get("value"), 0)
    tok = toks[idx] if 0 <= idx < len(toks) else dflt
    _write(["handheld", "retroarch"], key, tok)
    return {"key": key, "value": params.get("value")}


@method("ra_handheld_hk.reset", slow=True)
def _hk_reset(params):
    for field, _d, _k, _l in _HK_SLOTS:
        _write(["handheld", "retroarch"], field, None, remove=True)   # -> shipped default
    return {"message": "Hotkey combos reset to defaults"}


# --- Daphne handheld editor (WS-D): remap the Deck's buttons for Hypseus, handheld-only ---
# The Deck's SDL joystick button order (confirmed on-device): value = index+1. Guide (idx5) skipped;
# L2/R2 are analog axes and the directions ride the left stick (not remapped here).
_DAPHNE_BTN_OPTS = [("A", "1"), ("B", "2"), ("X", "3"), ("Y", "4"), ("View/Select", "5"),
                    ("Start", "7"), ("L3", "8"), ("R3", "9"), ("L1", "10"), ("R1", "11")]
_DAPHNE_BTN_TOKENS = [t for _, t in _DAPHNE_BTN_OPTS]
_DAPHNE_BTN_LABELS = [l for l, _ in _DAPHNE_BTN_OPTS]
_DAPHNE_ROWS = [("COIN1", "Insert coin"), ("START1", "Start"),
                ("BUTTON1", "Action 1"), ("BUTTON2", "Action 2"), ("BUTTON3", "Action 3")]
_DAPHNE_ROW_KEYS = {a for a, _ in _DAPHNE_ROWS}


@method("daphne_handheld.get", slow=True)
def _daphne_get(params):
    from .. import daphne_input
    hi = daphne_input.load_deck()
    settings = []
    for action, label in _DAPHNE_ROWS:
        tok = str(hi.button_value(action))
        idx = _DAPHNE_BTN_TOKENS.index(tok) if tok in _DAPHNE_BTN_TOKENS else 0
        settings.append({"key": action, "label": label, "type": "enum",
                         "value": idx, "options": _DAPHNE_BTN_LABELS})
    settings.append({"type": "action", "key": "reset",
                     "label": "Reset Daphne pad to defaults (reopen to refresh)",
                     "rpc": "daphne_handheld.reset", "args": {}})
    return {"exists": True, "running": False,
            "note": "Choose which Deck button does each Daphne action when you play HANDHELD (the "
                    "built-in pad). Your docked X-Arcade map is untouched. Directions use the left "
                    "stick.",
            "groups": [{"title": "Deck buttons", "note": "", "settings": settings}]}


@method("daphne_handheld.set", slow=True)
def _daphne_set(params):
    from .. import daphne_input, staterev
    key = params.get("key", "")
    if key not in _DAPHNE_ROW_KEYS:
        raise RpcError("EINVAL", f"unknown key {key!r}")
    idx = _int_or(params.get("value"), 0)
    tok = _DAPHNE_BTN_TOKENS[idx] if 0 <= idx < len(_DAPHNE_BTN_TOKENS) else _DAPHNE_BTN_TOKENS[0]
    hi = daphne_input.load_deck()
    hi.set_button(key, int(tok))
    daphne_input.save_deck(hi)
    staterev.bump("config")
    return {"key": key, "value": params.get("value")}


@method("daphne_handheld.reset", slow=True)
def _daphne_reset(params):
    from .. import daphne_input, staterev
    daphne_input._write(daphne_input.DECK_INI, daphne_input.deck_default_text())
    staterev.bump("config")
    return {"message": "Daphne pad reset to defaults"}


# ── per-system pages (one ns each, registered in a loop) ─────────────────────
def _sys_get_payload(sys: str, name: str, res_capable: bool):
    hh = _sys_hh(sys)
    has_cap = "watt_cap" in hh
    eff_cap = _int_or(hh.get("watt_cap"), None) if has_cap else \
        _int_or(_hh().get("default_watt_cap", _WATT_DEFAULT), _WATT_DEFAULT)
    settings = [
        {"key": "enable", "label": "Include in on-the-go", "type": "bool",
         "value": bool(hh.get("enabled", False))},
        {"key": "watt_cap", "label": "Watt cap (W)", "type": "int",
         "value": eff_cap if eff_cap is not None else _WATT_DEFAULT,
         "min": _WATT_MIN, "max": _WATT_MAX, "step": 1,
         "inherit": True, "inherited": (not has_cap)},
    ]
    note = "Applied only when handheld; your docked settings return automatically on exit."
    if res_capable:
        cur = str(hh.get("res", "native")).strip().lower()
        ridx = _RES_TOKENS.index(cur) if cur in _RES_TOKENS else 0
        settings.append({"key": "res", "label": "Handheld resolution", "type": "enum",
                         "value": ridx, "options": _RES_LABELS})
        note = ("Applied only when handheld; your docked settings return automatically on exit. The "
                "handheld resolution applies to whichever emulator each game launches (a RetroArch "
                "core or a standalone); a software core with no upscale option simply ignores it.")
    elif sys == "switch":
        note = "Switch internal resolution follows each Switch emulator's Dock-detection " \
               "toggle (720p handheld / 1080p docked), not a setting here."
    elif sys == "wiiu":
        note = "Wii U (Cemu): when enabled, handheld swaps in your saved Cemu handheld controller " \
               "profile so the built-in pad drives the game (docked profile returns on exit). " \
               "Per-game handheld resolution is on the sibling 'Resolution' page."
    return {"exists": True, "running": False, "note": note,
            "groups": [{"title": name, "note": "", "settings": settings}]}


def _register_sys(sys: str, name: str, res_capable: bool) -> None:
    @method(f"onthego_{sys}.get", slow=True)
    def _g(params, _s=sys, _n=name, _r=res_capable):
        return _sys_get_payload(_s, _n, _r)

    @method(f"onthego_{sys}.set", slow=True)
    def _st(params, _s=sys, _r=res_capable):
        key, val = params["key"], params["value"]
        if key == "enable":
            _write(["systems", _s, "handheld"], "enabled", _truthy(val))
        elif key == "watt_cap":
            if str(val).strip().lower() == "inherit":
                _write(["systems", _s, "handheld"], "watt_cap", None, remove=True)
            else:
                _write(["systems", _s, "handheld"], "watt_cap",
                       max(_WATT_MIN, min(_WATT_MAX, _int_or(val, _WATT_DEFAULT))))
        elif key == "res" and _r:
            idx = _int_or(val, 0)
            tok = _RES_TOKENS[idx] if 0 <= idx < len(_RES_TOKENS) else "native"
            _write(["systems", _s, "handheld"], "res", tok)
        else:
            raise RpcError("EINVAL", f"unknown key {key!r}")
        return {"key": key, "value": val}


for _sys, _name, _res in _SYSTEMS:
    _register_sys(_sys, _name, _res)
