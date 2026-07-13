r"""cemu_pg_* - Cemu (Wii U) PER-GAME settings over gameProfiles/<titleid>.ini (the SAME file Cemu
reads via right-click -> Edit game profile). Two inherit-aware pages:
  cemu_pg_general  ([General] shared libraries + pad view, [CPU] mode + thread quantum, [Audio] mute)
  cemu_pg_gfx      ([Graphics] graphics API, accurate shader mul, precompiled shaders)

Cemu's per-game model is SIMPLER than the Yuzu forks: a key PRESENT == overridden, ABSENT == use
Cemu's default. There is NO \use_global/\default twin. For graphics_api the "default" is the global
settings.xml value; for the CPU/shader keys there is no global equivalent (they were removed from the
global config), so the default is Cemu's compiled default. Either way index 0 is labelled
"Use default", not "Inherit global". We render inherit-aware rows (reusing the shared
yuzu_pergame.render_item SHAPE, relabelling index 0) and, on write, set the plain `key = value` or
REMOVE it to fall back to default. exists:true (create-on-demand - a partial ini is fine, absent keys
inherit). Instant save; refuses while Cemu runs (it rewrites profiles on exit).

Enum codes are source-verified (CemuConfig.h, cemu-project/Cemu, 2026-07-08):
  CPUMode  SinglecoreInterpreter=0 SinglecoreRecompiler=1 DualcoreRecompiler=2(legacy)
           MulticoreRecompiler=3 Auto=4       (we curate out legacy 2 via write_mode "option")
  AccurateShaderMulOption  False=0 True=1
  PrecompiledShaderOption  Auto=0 Enable=1 Disable=2
  graphics_api 0=OpenGL 1=Vulkan     threadQuantum uint (Cemu GUI presets 20000..100000)
"""
from __future__ import annotations

from .. import proc_guard, staterev
from . import cemu_games, cfgutil
from . import yuzu_pergame as yp
from .rpc import RpcError, method

_PROC = "cemu"
_DEFAULT = "Use default"
_NOTE = ("Per-game overrides written to this game's Cemu game profile; saves instantly. "
         "Pick 'Use default' to clear one.")


# ── descriptor helpers (no "file": these pages edit a single per-game ini) ─────
def _bool(key, label, section):
    return {"key": key, "label": label, "section": section, "type": "bool",
            "bool_true": "true", "bool_false": "false"}


def _enum(key, label, section, options, *, mode="index", stored=None):
    it = {"key": key, "label": label, "section": section, "type": "enum",
          "write_mode": mode, "options_display": options}
    if stored is not None:
        it["options_stored"] = stored
    return it


GENERAL_GROUPS = [
    {"title": "General", "note": "", "items": [
        _bool("loadSharedLibraries", "Load shared libraries", "General"),
        _bool("startWithPadView", "Start with GamePad view", "General"),
    ]},
    {"title": "CPU", "note": "", "items": [
        _enum("cpuMode", "CPU mode", "CPU",
              ["Single-core interpreter", "Single-core recompiler", "Multi-core recompiler", "Auto"],
              mode="option", stored=["0", "1", "3", "4"]),
        _enum("threadQuantum", "Thread quantum", "CPU",
              ["20000", "45000 (default)", "60000", "80000", "100000"],
              mode="option", stored=["20000", "45000", "60000", "80000", "100000"]),
    ]},
    {"title": "Audio", "note": "", "items": [
        _bool("disableAudio", "Disable audio for this game", "Audio"),
    ]},
]

GFX_GROUPS = [
    {"title": "Graphics", "note": "", "items": [
        _enum("graphics_api", "Graphics API", "Graphics", ["OpenGL", "Vulkan"]),
        _enum("accurateShaderMul", "Accurate shader multiplication", "Graphics",
              ["Fast (false)", "Accurate (true)"]),
        _enum("precompiledShaders", "Precompiled shaders", "Graphics",
              ["Auto", "Enabled", "Disabled"]),
    ]},
]

PG_PAGES = {
    "cemu_pg_general": ("General", GENERAL_GROUPS),
    "cemu_pg_gfx":     ("Graphics", GFX_GROUPS),
}


def _running() -> bool:
    return proc_guard.emulator_running(_PROC)


# ── inherit-aware row (reuse the shared shape, relabel index 0 to "Use default") ──
def _render(it: dict, raw: str | None) -> dict | None:
    row = yp.render_item(it, raw)
    if row and row.get("type") == "enum":
        opts = list(row.get("options") or [])
        if opts and opts[0] == "Inherit global":
            opts[0] = _DEFAULT
            row = {**row, "options": opts}
    return row


def pergame_get(groups: list, pg_text: str | None, running: bool) -> dict:
    out = []
    for g in groups:
        settings = []
        for it in g["items"]:
            raw = cfgutil.ini_read(pg_text, it["section"], it["key"]) if pg_text else None
            row = _render(it, raw)
            if row:
                settings.append(row)
        if settings:
            out.append({"title": g["title"], "note": g.get("note", ""), "settings": settings})
    # exists MUST be true (create-on-demand): the C++ hides all controls when exists=false.
    return {"exists": True, "running": running, "note": _NOTE, "groups": out}


# ── write one override (plain key = value; NO \default twin) / clear to default ──
def _clear(text: str, sec: str, key: str) -> str:
    text = cfgutil.ini_remove(text, sec, key)
    return cfgutil.ini_drop_empty_section(text, sec)


def _set(text: str, sec: str, key: str, stored: str) -> str:
    text = yp._ensure_section(text, sec)                 # shared: appends [sec] if missing
    t = cfgutil.ini_set_or_insert(text, sec, key, stored)
    return t if t is not None else text


def _write_item(text: str, it: dict, value) -> str:
    sec, key, typ = it["section"], it["key"], it["type"]
    if yp.is_inherit(value):
        return _clear(text, sec, key)
    try:
        n = int(float(value))
    except (TypeError, ValueError):
        raise RpcError("EINVAL", f"bad index {value!r} for {key}")
    if n <= 0:                                            # index 0 = "Use default" -> remove the key
        return _clear(text, sec, key)
    if typ == "bool":
        stored = it.get("bool_true", "true") if n >= 2 else it.get("bool_false", "false")
    elif typ == "enum":
        stored = yp._enum_stored(it, n - 1, cfgutil.ini_read(text, sec, key))
        if stored is None:
            raise RpcError("EINVAL", f"index {n} out of range for {key}")
    else:
        raise RpcError("EINVAL", f"unsupported type {typ!r} for {key}")
    return _set(text, sec, key, stored)


def _register(ns: str, groups: list) -> None:
    @method(f"{ns}.get", slow=True)
    def _g(params, groups=groups):
        pg = cemu_games.pergame_path(yp.tid(params))     # yp.tid validates 16-hex (anti-traversal)
        lf, _crlf = cemu_games.read_ini(pg)              # LF-normalised (Cemu gameProfiles are CRLF)
        return pergame_get(groups, lf, _running())

    @method(f"{ns}.set", slow=True)
    def _s(params, groups=groups):
        if _running():
            raise RpcError("EBUSY", "close Cemu first - it rewrites game profiles on exit.")
        titleid = yp.tid(params)
        key = params.get("key")
        it = yp.item_by_key(groups, key)
        if it is None:
            raise RpcError("EINVAL", f"{key!r} is not an editable setting")
        pg = cemu_games.pergame_path(titleid)
        lf, crlf = cemu_games.read_ini(pg)
        text = lf or ""
        new = _write_item(text, it, params.get("value"))
        if new != text:
            cemu_games.write_ini(pg, new, crlf)          # restores the file's original ending
            staterev.bump("config")
        row = _render(it, cfgutil.ini_read(new, it["section"], it["key"]))
        return {"key": key, "value": row["value"] if row else 0}


for _ns, (_title, _groups) in PG_PAGES.items():
    _register(_ns, _groups)
