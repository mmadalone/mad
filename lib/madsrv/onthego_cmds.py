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

from .. import es_gamelist, es_systems
from . import mad_tree
from .rpc import RpcError, method

# system key -> (display name, res-capable?). res-capable = has a numeric internal-res knob the
# on-the-go rails drive. Switch res = the per-emu Dock-detection toggle (not here); Wii U (Cemu)
# resolution is curated per title (graphic packs), so neither exposes a `res` row. This is a CURATED
# catalog of the demanding systems on-the-go tunes; the Per-system grid shows only the entries that
# have a gamelist (es_systems._has_gamelist), so e.g. psx stays here but is hidden until PS1 games
# exist. Xbox (xemu) has no res rail yet -> res-off, but still gets the universal watt cap.
_SYSTEMS = [
    ("switch",     "Nintendo Switch", False),
    ("ps3",        "PlayStation 3",   True),
    ("ps2",        "PlayStation 2",   True),
    ("gc",         "GameCube",        True),
    ("wii",        "Wii",             True),
    ("wiiu",       "Wii U",           False),
    ("xbox",       "Xbox",            False),
    ("psx",        "PlayStation 1",   True),
    ("n64",        "Nintendo 64",     True),
    ("saturn",     "Sega Saturn",     True),
    ("dreamcast",  "Dreamcast",       True),
    ("naomi",      "Sega NAOMI",      True),
    ("atomiswave", "Atomiswave",      True),
    ("daphne",     "Daphne",          False),
    ("lindbergh",  "Sega Lindbergh",  False),
    ("mugen",      "M.U.G.E.N",       False),
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


def _sys_leaves(sys: str, name: str) -> list:
    """The leaf page(s) behind one Per-system tile. Most systems are a single Settings page; a few FOLD
    into [Settings, Input, ...] (Wii U a resolution browser; Daphne/Lindbergh/PS2 a handheld Input leaf,
    context=handheld so the docked map is untouched). A tile with ONE leaf opens it directly; several open
    a small chooser. Concise labels, no sublabels (standing rule mad-concise-section-names)."""
    settings_leaf = {"label": "Settings", "kind": "settings", "arg": f"onthego_{sys}",
                     "title": f"{name} - On-the-go"}
    if sys == "wiiu":
        return [settings_leaf,
                {"label": mad_tree.L.INPUT, "kind": "settings", "arg": "cemu_input_handheld",
                 "title": f"{name} - Handheld input"},
                {"label": "Resolution", "kind": "settings_pergame", "arg": "cemures",
                 "title": f"{name} - Handheld resolution"}]
    if sys == "daphne":
        return [settings_leaf,
                {"label": mad_tree.L.INPUT, "kind": "settings", "arg": "daphne_handheld",
                 "title": f"{name} - Handheld input"}]
    if sys == "lindbergh":
        # Game-first per-game menu (pick a game once -> [Settings, Input mapping]), INDEPENDENT of the
        # docked cabinet config. Settings = handheld resolution (lindbergh_hhres); Input mapping = the
        # handheld Deck-pad dropdown editor (lindbergh_hhinput). Both are pergame_settings pages
        # (GuiMadPageEmuSettings), so no rebuild. Gun games hide the Input leaf (see lindbergh_hhmenu).
        return [settings_leaf,
                {"label": mad_tree.L.PERGAME, "kind": "settings_pergame_menu", "arg": "lindbergh_hhmenu",
                 "title": f"{name} - Per-game", "sections": [
                     {"label": "Settings", "kind": "pergame_settings", "arg": "lindbergh_hhres",
                      "title": f"{name} - Handheld resolution"},
                     {"label": mad_tree.L.INPUT_MAP, "key": "input", "kind": "pergame_settings",
                      "arg": "lindbergh_hhinput", "title": f"{name} - Input mapping"}]}]
    if sys == "wii":
        # Game-first per-game page (pick a game once -> ONE Settings page: handheld resolution +
        # Force Classic Controller). settings_pergame (singular) opens the page directly, no submenu.
        # The list drops lightgun titles and hides motion/pointer-only games (a Classic Controller
        # can't drive them handheld); see dolphin_wii_hh. pergame_settings = GuiMadPageEmuSettings, no rebuild.
        return [settings_leaf,
                {"label": mad_tree.L.PERGAME, "kind": "settings_pergame", "arg": "dolphin_wii_hh",
                 "title": f"{name} - Per-game"}]
    if sys == "ps2":
        # Handheld PS2 input folds in HERE (was the top-level "PlayStation 2 (handheld)" group). Same
        # context-threaded editors as the docked tile, opened with context=handheld -> the handheld slice
        # of the store; the docked map is untouched. All games = the global map; Per-game = one title.
        return [settings_leaf,
                {"label": mad_tree.L.INPUT, "kind": "group", "arg": "", "title": f"{name} - Input", "sections": [
                    {"label": "All games", "kind": "input_map", "arg": "pcsx2", "context": "handheld",
                     "title": f"{name} handheld - All games"},
                    {"label": mad_tree.L.PERGAME, "kind": "input_pergame", "arg": "pcsx2pgin",
                     "context": "handheld", "title": f"{name} handheld - Per-game"}]}]
    if sys == "ps3":
        # Handheld PS3 input, mirroring the ps2 fold: the same context-threaded editors as the docked
        # RPCS3 tile, opened with context=handheld -> the handheld slice of the store; the docked map
        # is untouched. All games = the global map; Per-game = one title.
        return [settings_leaf,
                {"label": mad_tree.L.INPUT, "kind": "group", "arg": "", "title": f"{name} - Input", "sections": [
                    {"label": "All games", "kind": "input_map", "arg": "rpcs3", "context": "handheld",
                     "title": f"{name} handheld - All games"},
                    {"label": mad_tree.L.PERGAME, "kind": "input_pergame", "arg": "rpcs3pgin",
                     "context": "handheld", "title": f"{name} handheld - Per-game"}]}]
    if sys == "mugen":
        # Settings = the shared watt-cap page. Resolution is MUGEN-specific (aspect-preserving
        # GameWidth/Height downshift, applied by lib/mugen_res at launch; NOT the multiplier
        # rail), split all-games + per-game. Per-game reuses the mugen.games browser.
        return [settings_leaf,
                {"label": "Resolution", "kind": "settings", "arg": "mugen_hhres",
                 "title": f"{name} - Handheld resolution (all games)"},
                {"label": "Per-game resolution", "kind": "settings_pergame_menu", "arg": "mugen",
                 "title": f"{name} - Per-game resolution", "sections": [
                     {"label": "Resolution", "kind": "pergame_settings", "arg": "mugen_hhres_pg",
                      "title": f"{name} - Handheld resolution"}]}]
    return [settings_leaf]


def _sys_tile(sys: str, name: str) -> dict:
    """One Per-system grid tile: the system's console art + its leaf page(s). Rendered by the
    GuiMadPageStandalones sub-grid (a `grid` section carries these as its tiles)."""
    from .systems_cmds import console_art
    art = console_art(sys)
    return {"key": sys, "label": name, "sublabel": "",
            "art": [art] if art else [], "sections": _sys_leaves(sys, name)}


# ── sidebar chooser tree ─────────────────────────────────────────────────────
def _hub_tile() -> dict:
    """The On-the-go hub tile with its section rows (pre-grid). `_list` gridifies this into the
    top-level icon-tile grid; the structural tests check this semantic tree directly via _hub_tile."""
    from .systems_cmds import resolve_art
    icon = resolve_art(["icons/on-the-go.png"])
    # Per-system is an icon-tile grid, alphabetical by display name, gated to systems that actually
    # have at least one VISIBLE game -- NOT merely a gamelist.xml on disk. ES-DE leaves an empty
    # gamelist.xml behind after you delete a system's last game, so a bare file-existence check
    # (_has_gamelist alone) would keep showing an emptied system. es_gamelist.visible_records is the
    # same "does it have games" signal the RetroArch hub uses. So psx (never scanned) and an emptied
    # xbox are hidden; only real, playable systems appear.
    present = {s for s in es_systems.load_systems()
               if es_systems._has_gamelist(s) and es_gamelist.visible_records(s)}
    per_sys = [_sys_tile(sys, name)
               for sys, name, _res in sorted(_SYSTEMS, key=lambda t: t[1].lower())
               if sys in present]
    # Only offer the Per-system grid when at least one curated system has games -- an empty grid
    # would fall through to the reused sub-grid's standalones empty-state text, which is wrong here.
    per_sys_row = {"label": "Per-system", "kind": "grid", "arg": "",
                   "title": "On-the-go - Per-system", "sections": per_sys,
                   "note": "Per-system handheld watt cap + resolution."} if per_sys else None
    # The handheld PS2 input now folds into Per-system -> PlayStation 2 -> Input (see _sys_leaves), not a
    # separate top-level row. Concise labels, no sublabels (standing rule mad-concise-section-names).
    sections = [row for row in [
        {"label": "Global", "kind": "settings", "arg": "onthego_global", "title": "On-the-go - Global"},
        per_sys_row,
        # Pad mapping + Hotkey combos retired with the old handheld rail (RA input profiles own the
        # Deck-pad binds/hotkeys now); only Per-game input remains, so this single-child group is
        # collapsed by _collapse_singletons in _list (standing rule mad-collapse-single-child-groups).
        {"label": "RetroArch", "kind": "group", "arg": "", "title": "On-the-go - RetroArch", "sections": [
            {"label": "Per-game input", "kind": "ra_systems_handheld", "arg": "",
             "title": "On-the-go - Per-game input"},
         ]},
        {"label": "Quit combo", "kind": "settings", "arg": "quit_handheld",
         "title": "On-the-go - Quit combo"},
    ] if row]
    return {"key": "on-the-go", "label": "On-the-go", "sublabel": "",
            "art": [icon] if icon else [], "sections": sections}


@method("onthego.list", slow=True)
def _list(params):
    # Render the hub as a tiled icon GRID: gridify the hub tile so its section rows become the
    # top-level tiles (Global / Per-system / RetroArch / Quit combo), each with its own icon. Reuses
    # the standalone _gridify_tile so the hub + emulator grids stay identical.
    from .standalones_cmds import _gridify_tile, _collapse_singletons
    hub = _hub_tile()
    # Collapse the now-single-child RetroArch group so it opens Per-game input directly (standing
    # rule mad-collapse-single-child-groups); auto-reverts to a submenu if a second RA row returns.
    hub["sections"] = _collapse_singletons(hub["sections"])
    # Also tile each Per-system console sub-tile's OWN leaf chooser: a multi-page system (Wii U /
    # Daphne / Lindbergh / Wii / PS2) opens a tiled icon grid of its pages instead of a plain list.
    # The Per-system row is the only kind:"grid" (see _hub_tile), so this is scoped to on-the-go and
    # never touches the standalone / RA-hub grids. NO _decorate_pergame -> the per-game flow behind a
    # tile renders exactly as before; a single-page system (<2 nav sections) is returned unchanged by
    # _gridify_tile and still opens its form directly.
    for row in hub["sections"]:
        if row.get("kind") == "grid" and isinstance(row.get("sections"), list):
            row["sections"] = [_gridify_tile(s) for s in row["sections"]]
    return {"tiles": _gridify_tile(hub).get("members", [hub])}


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
        "note": "Watt cap for every handheld launch, restored when docked; override per system below.",
        "groups": [{"title": "On-the-go", "note": "", "settings": [
            {"key": "enabled", "label": "Enable on-the-go profiles", "type": "bool",
             "value": bool(hh.get("enabled", False))},
            {"key": "mode", "label": "Detection", "type": "enum", "value": mode,
             "options": _MODE_OPTS},
            {"key": "default_watt_cap", "label": "Default watt cap - all systems (W)", "type": "int",
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


# --- Handheld quit combo (WS-G): a Deck-pad chord the evdev quit-combo-watcher uses HANDHELD for
# standalone emulators (docked [quit_combo] untouched). The watcher matches raw EVDEV codes, so this
# is the Deck 28de:11ff virtual-pad evdev map (NOT the SDL indices the RA editors use). Confirmed via
# the WS-D Deck-pad capture. Stored as [quit_combo.handheld] buttons=[c1,c2] + hold_sec.
_DECK_EVDEV_OPTS = [("A", 304), ("B", 305), ("X", 307), ("Y", 308), ("L1", 310), ("R1", 311),
                    ("Back/Select", 314), ("Start", 315), ("L3", 317), ("R3", 318)]
_DECK_EVDEV_CODES = [c for _, c in _DECK_EVDEV_OPTS]
_DECK_EVDEV_LABELS = [l for l, _ in _DECK_EVDEV_OPTS]
_QUIT_DEFAULT = [314, 315]     # Select + Start (matches the docked default combo)
_QUIT_HOLD_MIN, _QUIT_HOLD_MAX, _QUIT_HOLD_DEFAULT = 1, 5, 2


def _quit_hh() -> dict:
    qc = _merged().get("quit_combo")
    hh = qc.get("handheld") if isinstance(qc, dict) else None
    return hh if isinstance(hh, dict) else {}


def _quit_buttons(hh) -> list:
    b = hh.get("buttons")
    try:
        b = [int(x) for x in b] if isinstance(b, list) else list(_QUIT_DEFAULT)
    except (TypeError, ValueError):                      # a hand-edited/corrupt value -> the default
        b = list(_QUIT_DEFAULT)
    while len(b) < 2:
        b.append(_QUIT_DEFAULT[len(b)])
    return b[:2]


def _evdev_idx(code) -> int:
    return _DECK_EVDEV_CODES.index(code) if code in _DECK_EVDEV_CODES else 0


@method("quit_handheld.get", slow=True)
def _quit_get(params):
    hh = _quit_hh()
    b1, b2 = _quit_buttons(hh)
    hold = _int_or(hh.get("hold_sec", _QUIT_HOLD_DEFAULT), _QUIT_HOLD_DEFAULT)
    settings = [
        {"key": "btn1", "label": "Button 1", "type": "enum",
         "value": _evdev_idx(b1), "options": _DECK_EVDEV_LABELS},
        {"key": "btn2", "label": "Button 2", "type": "enum",
         "value": _evdev_idx(b2), "options": _DECK_EVDEV_LABELS},
        {"key": "hold_sec", "label": "Hold time (seconds)", "type": "int",
         "value": max(_QUIT_HOLD_MIN, min(_QUIT_HOLD_MAX, hold)),
         "min": _QUIT_HOLD_MIN, "max": _QUIT_HOLD_MAX, "step": 1},
        {"type": "action", "key": "reset",
         "label": "Reset quit combo to default (reopen to refresh)",
         "rpc": "quit_handheld.reset", "args": {}},
    ]
    return {"exists": True, "running": False,
            "note": "Deck-pad chord to quit a standalone game, handheld only. Docked quit untouched; "
                    "RetroArch games use the quick menu.",
            "groups": [{"title": "Handheld quit combo", "note": "", "settings": settings}]}


@method("quit_handheld.set", slow=True)
def _quit_set(params):
    key, val = params.get("key", ""), params.get("value")
    if key in ("btn1", "btn2"):
        idx = _int_or(val, 0)
        code = _DECK_EVDEV_CODES[idx] if 0 <= idx < len(_DECK_EVDEV_CODES) else _DECK_EVDEV_CODES[0]
        btns = _quit_buttons(_quit_hh())
        btns[0 if key == "btn1" else 1] = code
        _write(["quit_combo", "handheld"], "buttons", btns)
    elif key == "hold_sec":
        _write(["quit_combo", "handheld"], "hold_sec",
               max(_QUIT_HOLD_MIN, min(_QUIT_HOLD_MAX, _int_or(val, _QUIT_HOLD_DEFAULT))))
    else:
        raise RpcError("EINVAL", f"unknown key {key!r}")
    return {"key": key, "value": val}


@method("quit_handheld.reset", slow=True)
def _quit_reset(params):
    _write(["quit_combo", "handheld"], "buttons", None, remove=True)   # -> falls back to docked combo
    _write(["quit_combo", "handheld"], "hold_sec", None, remove=True)
    return {"message": "Handheld quit combo reset (Select + Start)"}


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
            "note": "Which Deck button does each Daphne action, handheld only. Docked X-Arcade "
                    "untouched; directions use the left stick.",
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
        {"key": "enable", "label": "Custom cap / resolution for this system", "type": "bool",
         "value": bool(hh.get("enabled", False))},
        {"key": "watt_cap", "label": "Watt cap (W)", "type": "int",
         "value": eff_cap if eff_cap is not None else _WATT_DEFAULT,
         "min": _WATT_MIN, "max": _WATT_MAX, "step": 1,
         "inherit": True, "inherited": (not has_cap)},
    ]
    note = ("Turn on to override this system's handheld watt cap. Handheld only; docked returns on exit.")
    if res_capable:
        from .. import handheld_res
        choices = handheld_res.resolution_choices(sys)   # per-system real resolutions (WS-H), deduped
        rtokens = [t for t, _ in choices]
        cur = handheld_res.snap_token(sys, str(hh.get("res", "native")))
        ridx = rtokens.index(cur) if cur in rtokens else 0
        settings.append({"key": "res", "label": "Handheld resolution", "type": "enum",
                         "value": ridx, "options": [l for _, l in choices], "picker": True})
        note = ("Handheld only; docked returns on exit. The resolution applies to whichever emulator "
                "each game launches (a core with no upscale option just ignores it).")
    elif sys == "switch":
        note = "Switch internal resolution follows each Switch emulator's Dock-detection " \
               "toggle (720p handheld / 1080p docked), not a setting here."
    elif sys == "wiiu":
        note = "When enabled, handheld swaps in your saved Cemu handheld controller profile " \
               "(docked returns on exit). Per-game resolution is on the Resolution page."
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
            from .. import handheld_res
            rtokens = [t for t, _ in handheld_res.resolution_choices(_s)]   # same per-system order as .get
            idx = _int_or(val, 0)
            tok = rtokens[idx] if 0 <= idx < len(rtokens) else "native"
            _write(["systems", _s, "handheld"], "res", tok)
        else:
            raise RpcError("EINVAL", f"unknown key {key!r}")
        return {"key": key, "value": val}


for _sys, _name, _res in _SYSTEMS:
    _register_sys(_sys, _name, _res)
