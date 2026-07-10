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
]
_WATT_MIN, _WATT_MAX, _WATT_DEFAULT = 4, 15, 12
_MODE_OPTS = ["Auto (physical display)", "Force handheld", "Force docked"]
# Only the PS2/PS3 rails honor a 2x handheld target; the RetroArch + Dolphin rails do
# native-or-inherit, so the per-system 'res' options + index maps are chosen per system.
_TWOX = {"ps2", "ps3"}


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


# ── sidebar chooser tree ─────────────────────────────────────────────────────
@method("onthego.list", slow=True)
def _list(params):
    from .systems_cmds import resolve_art
    icon = resolve_art(["icons/on-the-go.png"])
    per_sys = [{"label": name, "sublabel": "", "kind": "settings",
                "arg": f"onthego_{sys}", "title": f"{name} — On-the-go"}
               for sys, name, _res in _SYSTEMS]
    sections = [
        {"label": "Global", "sublabel": "master switch, detection, default watt cap",
         "kind": "settings", "arg": "onthego_global", "title": "On-the-go — Global"},
        {"label": "Per-system", "sublabel": "enable, watt cap & resolution per system",
         "kind": "group", "arg": "", "title": "On-the-go — Per-system", "sections": per_sys},
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
        if sys in _TWOX:                         # PS2/PS3 honor a 2x target
            opts = ["Native (1x)", "2x", "Inherit (leave as-is)"]
            ridx = {"native": 0, "2x": 1, "inherit": 2}.get(cur, 0)
        else:                                    # RA/Dolphin: native-or-inherit only
            opts = ["Native (1x)", "Inherit (leave as-is)"]
            ridx = 1 if cur == "inherit" else 0
        settings.append({"key": "res", "label": "Handheld resolution", "type": "enum",
                         "value": ridx, "options": opts})
    elif sys == "switch":
        note = "Switch internal resolution follows each Switch emulator's Dock-detection " \
               "toggle (720p handheld / 1080p docked), not a setting here."
    elif sys == "wiiu":
        note = "Wii U (Cemu) resolution is curated per title via graphic packs, not here."
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
            mapping = {0: "native", 1: "2x", 2: "inherit"} if _s in _TWOX \
                else {0: "native", 1: "inherit"}
            _write(["systems", _s, "handheld"], "res", mapping.get(_int_or(val, 0), "native"))
        else:
            raise RpcError("EINVAL", f"unknown key {key!r}")
        return {"key": key, "value": val}


for _sys, _name, _res in _SYSTEMS:
    _register_sys(_sys, _name, _res)
