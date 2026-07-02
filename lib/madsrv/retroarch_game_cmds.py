"""ragame.* / ragameset.* / ragamein.* — RetroArch hub "Per-game" section:
per-system game list, and the per-game Settings / Input-remap editors.

Game identity everywhere here is "<system>:<stem>" — stem = the ROM basename,
CASE-PRESERVED (Path(rom).stem, lib/classify.py:39), split on the FIRST ":".
Do NOT confuse with PCSX2's own titleid ("<SERIAL>_<CRC>") — only the
STRUCTURE of pcsx2_pergame_cmds.py's buffered get/set/save/cancel engine is
mirrored here, not its identity scheme.

Three RPC namespaces:
  ragame     the picker: ragame.systems (present RA systems) and ragame.games
             (one system's game list, with a cheap overrides/summary badge).
  ragameset  per-game SETTINGS, rendered by the existing GuiMadPageEmuSettings
             (ns="ragameset"). Clones pcsx2_pergame_cmds.py's buffered
             get/set/save/cancel engine over retroarch_settings.CATEGORIES
             (an inherit slot per type) and retroarch_cfg's per-game PG_*
             writer (set_game_option/get_game_options).
  ragamein   per-game INPUT REMAP, rendered by the SAME GuiMadPageEmuSettings
             (ns="ragamein") as device-agnostic RetroPad ENUM SELECTORS — not
             a physical-capture EmuInputMap page, since a remap target is just
             "which RetroPad button/device/port", not a physical bind. Backed
             by lib.retroarch_rmp's native .rmp writer.

Both editors MUST return "exists": true unconditionally (the Eden gotcha,
pcsx2_pergame_cmds.py's own docstring/comment) — a per-game override that has
never been saved is the normal first-use state, not a missing/broken game;
"exists": false would empty the C++ page instead of showing all-"Inherit
global"/"Default" rows.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from .. import es_gamelist, es_systems, proc_guard
from .. import retroarch_cfg
from .. import retroarch_rmp as rmp
from ..policy import load_merged
from . import retroarch_settings as rs
from .backends_cmds import present_ra_systems, _p1
from .rpc import RpcError, method
from .systems_cmds import console_art

_TRUE = rs._TRUE


def _split_titleid(titleid: str) -> tuple[str, str]:
    if not titleid or ":" not in titleid:
        raise RpcError("EINVAL", f"bad game id {titleid!r}")
    system, _, stem = titleid.partition(":")
    if not system or not stem:
        raise RpcError("EINVAL", f"bad game id {titleid!r}")
    return system, stem


def _is_inherit(value) -> bool:
    return isinstance(value, str) and value.strip().lower() == "inherit"


# ── ragame.* — the picker (systems + one system's games) ─────────────────────

def _ragame_systems() -> dict:
    sysxml = es_systems.load_systems()
    out = [{"name": s, "count": len(es_gamelist.visible_records(s)), "art": console_art(s)}
           for s in present_ra_systems(sysxml)]
    return {"systems": out}


# The PG_* sentinel BEGIN line substring (see retroarch_cfg); a game .cfg has a
# MAD settings override iff this marker is present.
_PG_MARKER = "MAD per-game options"


def _settings_override_stems(system: str) -> set[str]:
    """Case-preserved rom stems with a MAD per-game SETTINGS override. A system's
    core dirs can hold THOUSANDS of bezel-only .cfg files with no MAD block (fba
    = 4036), and reading each to check is far too slow (~12s -> the ragame.games
    RPC times out). Do ONE fast `grep -rlF` pass for the PG sentinel marker
    instead; only matched files carry a MAD block. Falls back to the per-file
    scan if grep is somehow unavailable."""
    dirs = [d for d in retroarch_cfg.core_dirs_for_system(system) if d.exists()]
    if not dirs:
        return set()
    try:
        out = subprocess.run(["grep", "-rlF", "--include=*.cfg", _PG_MARKER,
                              *map(str, dirs)],
                             capture_output=True, text=True, timeout=30)
        if out.returncode in (0, 1):        # 0 = matches, 1 = no matches; both fine
            return {Path(line).stem for line in out.stdout.splitlines() if line}
    except (OSError, subprocess.SubprocessError):
        pass
    stems: set[str] = set()                 # fallback: correct but slow
    for d in dirs:
        try:
            stems.update(p.stem for p in d.glob("*.cfg")
                         if retroarch_cfg.has_game_overrides(system, p.stem))
        except OSError:
            pass
    return stems


def _input_override_stems(system: str) -> set[str]:
    """Case-preserved rom stems with a per-game INPUT remap, found the same
    cheap way: glob the *.rmp files that already exist, not every game."""
    candidates: set[str] = set()
    for core_dir in rmp.core_remap_dirs_for_system(system):
        try:
            candidates.update(p.stem for p in core_dir.glob("*.rmp"))
        except OSError:
            pass
    return {stem for stem in candidates if rmp.has_game_remap(system, stem)}


def _controller_override_ents(system: str, merged: dict) -> dict[str, dict]:
    """{stem: game policy entry} for every `[games."<system>:<stem>"]` with its
    own `ports` — already loaded in `merged`, no extra file I/O at all."""
    prefix = f"{system}:"
    games = merged.get("games", {})
    out: dict[str, dict] = {}
    if isinstance(games, dict):
        for key, ent in games.items():
            if (isinstance(key, str) and key.startswith(prefix)
                    and isinstance(ent, dict) and ent.get("ports")):
                out[key[len(prefix):]] = ent
    return out


def _settings_line(system: str, stem: str, prefer_core: str | None = None) -> str:
    opts = retroarch_cfg.get_game_options(system, stem, prefer_core=prefer_core)
    if not opts:
        return "Settings      default"
    keys = list(opts)
    shown = ", ".join(keys[:4]) + ("…" if len(keys) > 4 else "")
    return f"Settings      {len(opts)} set - {shown}"


_BTN_RE = re.compile(r"input_player(\d+)_btn_(\w+)")
_BTN_LABEL_BY_NAME = dict(zip(rmp.BUTTON_NAMES, rmp.BUTTON_LABELS))
_DEVICE_LABEL_BY_VALUE = {v: lbl for lbl, v in rmp.DEVICE_OPTIONS}


def _input_line(system: str, stem: str, prefer_core: str | None = None) -> str:
    mapping = rmp.get_game_remap(system, stem, prefer_core=prefer_core)
    if not mapping:
        return "Input remap   default"
    bits = []
    for key in sorted(mapping):
        m = _BTN_RE.fullmatch(key)
        if not m or m.group(1) != "1":     # the compact summary covers P1 only
            continue
        src_label = _BTN_LABEL_BY_NAME.get(m.group(2))
        if src_label is None:
            continue
        try:
            tgt = int(mapping[key])
        except (TypeError, ValueError):
            continue
        if 0 <= tgt < len(rmp.BUTTON_LABELS):
            bits.append(f"{src_label}={rmp.BUTTON_LABELS[tgt]}")
    dev_raw = mapping.get("input_libretro_device_p1")
    if dev_raw is not None:
        try:
            dev_label = _DEVICE_LABEL_BY_VALUE.get(int(dev_raw))
        except (TypeError, ValueError):
            dev_label = None
        if dev_label:
            bits.append(f"device: {dev_label}")
    body = ", ".join(bits[:4]) if bits else f"{len(mapping)} keys set"
    return f"Input remap   {body}"


def _controllers_line(ent: dict | None) -> str:
    if not ent or not ent.get("ports"):
        return "Controllers   default (global)"
    return f"Controllers   P1: {_p1(ent)}"


def _ragame_games(system: str) -> dict:
    recs = es_gamelist.visible_records(system)   # hide <hidden>true</hidden> games (as ES-DE does)
    merged = load_merged()
    settings_stems = _settings_override_stems(system)
    input_stems = _input_override_stems(system)
    controller_ents = _controller_override_ents(system, merged)
    # Loaded ONCE for the whole system: retroarch_cfg.launched_core() re-parses
    # es_systems.xml per call when not given a `systems` dict, and this runs
    # once per GAME below (fba alone lists 4036 — see _settings_override_stems'
    # own perf note above).
    ra_systems = es_systems.load_systems()
    # The system default core is identical for every game (only games carrying a
    # per-game <altemulator> differ), so resolve it ONCE and per-game-resolve
    # only the altemulator games -- otherwise launched_core re-reads the gamelist
    # per game (~3.5s on fba's 1828 games).
    sys_core = retroarch_cfg.default_core(system, ra_systems)

    games = []
    for rec in recs.values():
        stem = rec["stem"]
        core = (retroarch_cfg.launched_core(system, stem, ra_systems)
                if rec.get("altemulator") else sys_core)
        has_settings = stem in settings_stems
        has_input = stem in input_stems
        ctrl_ent = controller_ents.get(stem)
        overrides = has_settings or has_input or (ctrl_ent is not None)
        summary = ""
        if overrides:
            summary = "\n".join([
                _settings_line(system, stem, core) if has_settings else "Settings      default",
                _input_line(system, stem, core) if has_input else "Input remap   default",
                _controllers_line(ctrl_ent),
            ])
        games.append({"stem": stem, "name": rec["name"], "overrides": overrides,
                      "summary": summary, "core": core or ""})
    games.sort(key=lambda g: g["name"].lower())
    return {"games": games}


@method("ragame.systems", slow=True)
def _ragame_systems_rpc(params):
    return _ragame_systems()


@method("ragame.games", slow=True)
def _ragame_games_rpc(params):
    system = params.get("system") or ""
    if not system:
        raise RpcError("EINVAL", "system required")
    return _ragame_games(system)


# ── ragameset.* — per-game SETTINGS (buffered EmuSettings ns) ────────────────
# Every global category group, per-game-inheritable — clones pcsx2_pergame_cmds
# .py's group/inherit-slot pattern over retroarch_settings.CATEGORIES.

def _rs_pg_groups() -> list:
    out = []
    for ns, (title, groups) in rs.CATEGORIES.items():
        for g in groups:
            out.append({"title": f"{title} — {g['title']}", "note": g.get("note", ""),
                        "items": g["items"]})
    return out


_RS_GROUPS = _rs_pg_groups()


def _rs_item_by_key(key: str) -> dict | None:
    for g in _RS_GROUPS:
        for it in g["items"]:
            if it["key"] == key:
                return it
    return None


def _rs_read_item(pg: dict, it: dict, base: dict | None = None) -> dict:
    """One per-game settings row, inherit-wrapped: index 0 = 'Inherit global'
    for bool/resolution/enum; int/float use the C++ numeric-inherit affordance
    (inherit:true/inherited:<bool>, a set of the string 'inherit' clears).

    `base` (optional) is the game's STANDALONE/bezel value for this key,
    outside MAD's PG_* block (retroarch_cfg.base_game_options) — "layer on
    top": when the PG block has no override for this key but a base value
    exists (e.g. the bezel pipeline's `aspect_ratio_index` line), the row
    shows that TRUE effective value instead of a misleading 'Inherit global',
    with the inherit slot's own label hinting where it's coming from. Picking
    Inherit still only clears the PG key (see _rs_write_item) — the base line
    is display-only context and is untouched, so the display falls back to it
    again afterwards."""
    key, label, t = it["key"], it["label"], it["type"]
    raw = pg.get(key)
    base_raw = base.get(key) if (raw is None and base) else None
    effective = raw if raw is not None else base_raw
    inherit_label = ("No override (using game's current)" if base_raw is not None
                     else "Inherit global")
    if t == "bool":
        val = 0 if effective is None else (2 if effective.strip().lower() in _TRUE else 1)
        return {"key": key, "label": label, "type": "enum",
                "options": [inherit_label, "Off", "On"], "value": val}
    if t in ("resolution", "enum"):
        options = [inherit_label] + list(it["options"])
        if effective is None:
            return {"key": key, "label": label, "type": "enum", "options": options, "value": 0}
        if t == "resolution":
            if effective in it["options"]:
                return {"key": key, "label": label, "type": "enum",
                        "options": options, "value": 1 + it["options"].index(effective)}
            return {"key": key, "label": label, "type": "enum",
                    "options": options + [f"(current: {effective})"], "value": len(options)}
        # enum (_eidx: stored value is the integer option INDEX)
        try:
            idx = int(float(effective))
        except (TypeError, ValueError):
            idx = None
        if idx is not None and 0 <= idx < len(it["options"]):
            return {"key": key, "label": label, "type": "enum",
                    "options": options, "value": 1 + idx}
        extra = idx if idx is not None else effective
        return {"key": key, "label": label, "type": "enum",
                "options": options + [f"(current: {extra})"], "value": len(options)}
    # int / float
    if effective is None:
        value = it.get("min", 0)
        inherited = True
    else:
        try:
            value = float(effective) if t == "float" else int(float(effective))
        except (TypeError, ValueError):
            value = it.get("min", 0)
            inherited = True
        else:
            inherited = raw is None
    row = {"key": key, "label": label, "type": t, "value": value,
           "inherit": True, "inherited": inherited}
    for k in ("min", "max", "step"):
        if k in it:
            row[k] = it[k]
    return row


def _rs_write_item(it: dict, value, current: dict) -> str | None:
    """The token to store via retroarch_cfg.set_game_option, or None to clear
    (inherit). `value` is the C++-sent value: an enum/bool option INDEX for
    bool/resolution/enum, or a raw number (or the string "inherit") for
    int/float. `current` is the live per-game dict (for the trailing
    "(current: ...)" slot, which must round-trip the untouched raw value)."""
    t = it["type"]
    if t == "bool":
        try:
            n = int(float(value))
        except (TypeError, ValueError):
            raise RpcError("EINVAL", f"bad index {value!r} for {it['key']}")
        if n <= 0:
            return None
        return "true" if n >= 2 else "false"
    if t in ("resolution", "enum"):
        try:
            n = int(float(value))
        except (TypeError, ValueError):
            raise RpcError("EINVAL", f"bad index {value!r} for {it['key']}")
        if n <= 0:
            return None
        real = n - 1
        options = list(it["options"])
        if real < len(options):
            return options[real] if t == "resolution" else str(real)
        return current.get(it["key"])          # the appended "(current: ...)" slot
    # int / float
    if _is_inherit(value):
        return None
    if t == "int":
        try:
            n = int(float(value))
        except (TypeError, ValueError):
            raise RpcError("EINVAL", f"bad integer {value!r} for {it['key']}")
        if "min" in it and "max" in it:
            n = max(it["min"], min(it["max"], n))
        return str(n)
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise RpcError("EINVAL", f"bad number {value!r} for {it['key']}")
    if "min" in it and "max" in it:
        v = max(float(it["min"]), min(float(it["max"]), v))
    return f"{v:.6f}"


_RS_NOTE = ("Per-game overrides for RetroArch. Pick 'Inherit global' to clear an "
            "override so the game uses the global RetroArch setting. Changes are "
            "staged; press Save to apply. Nothing here changes the global config, "
            "so other games are never affected.")

_rs_buf: dict = {"titleid": None, "data": None, "disk": None, "dirty": False, "edits": [],
                 "base": {}}


def _rs_reload(titleid: str) -> None:
    system, stem = _split_titleid(titleid)
    # Read the LAUNCHED core's cfg, not the alphabetically-first one (see
    # retroarch_cfg.launched_core) — a multi-core system otherwise shows/edits
    # the wrong core's per-game overrides.
    prefer = retroarch_cfg.launched_core(system, stem)
    data = dict(retroarch_cfg.get_game_options(system, stem, prefer_core=prefer))
    # "base" is the standalone/bezel cfg content outside the PG_* block —
    # display-only context for _rs_read_item's "layer on top" precedence
    # (Item A). It is NEVER staged as an edit and never affects "dirty".
    base = dict(retroarch_cfg.base_game_options(system, stem, prefer_core=prefer))
    _rs_buf.update({"titleid": titleid, "data": data, "disk": dict(data),
                    "dirty": False, "edits": [], "base": base})


def _rs_get(titleid: str) -> dict:
    _split_titleid(titleid)   # validate shape even before any buffered state exists
    if not (_rs_buf["titleid"] == titleid and _rs_buf["dirty"]):
        _rs_reload(titleid)
    data = _rs_buf["data"] or {}
    base = _rs_buf.get("base") or {}
    groups = []
    for g in _RS_GROUPS:
        settings = [_rs_read_item(data, it, base) for it in g["items"]]
        if settings:
            groups.append({"title": g["title"], "note": g.get("note", ""), "settings": settings})
    # exists MUST be true: a missing per-game cfg is the normal first-use state
    # (created on demand) — exists:false would empty the C++ page.
    return {"exists": True, "running": proc_guard.retroarch_running(), "buffered": True,
            "dirty": _rs_buf["dirty"], "note": _RS_NOTE, "groups": groups}


def _rs_set(params: dict) -> dict:
    if proc_guard.retroarch_running():
        raise RpcError("EBUSY", "RetroArch is running, close it first (per-game "
                                "writes also enable a global override flag).")
    titleid = params.get("titleid") or ""
    _split_titleid(titleid)
    if _rs_buf["titleid"] != titleid or _rs_buf["data"] is None:
        _rs_reload(titleid)
    key, value = params["key"], params["value"]
    it = _rs_item_by_key(key)
    if it is None:
        raise RpcError("EINVAL", f"{key!r} is not an editable setting")
    tok = _rs_write_item(it, value, _rs_buf["data"])
    if tok is None:
        _rs_buf["data"].pop(key, None)
    else:
        _rs_buf["data"][key] = tok
    _rs_buf["edits"].append((key, tok))
    _rs_buf["dirty"] = (_rs_buf["data"] != _rs_buf["disk"])
    # Shape back through the same base-layered read as _rs_get, so picking
    # Inherit right after a PG override immediately re-shows the honest
    # base/bezel value (if any) instead of a stale "Inherit global".
    return {"key": key,
            "value": _rs_read_item(_rs_buf["data"], it, _rs_buf.get("base"))["value"]}


def _rs_save(titleid: str) -> dict:
    if proc_guard.retroarch_running():
        raise RpcError("EBUSY", "RetroArch is running, close it first (per-game "
                                "writes also enable a global override flag).")
    if _rs_buf["titleid"] != titleid or not _rs_buf["edits"]:
        _rs_buf["dirty"] = False
        return {"saved": False}
    system, stem = _split_titleid(titleid)
    retroarch_cfg.ensure_pergame_enabled(["overrides"])
    # retroarch_cfg.set_game_option itself does a fresh read-modify-write per
    # call, so replaying the staged (key, token) edits in order is already safe
    # against an external change to OTHER keys between load and save — no
    # separate "replay onto one bulk fresh read" pass is needed here.
    for key, tok in _rs_buf["edits"]:
        retroarch_cfg.set_game_option(system, stem, key, tok)
    from .. import staterev
    staterev.bump("config")
    prefer = retroarch_cfg.launched_core(system, stem)
    fresh = dict(retroarch_cfg.get_game_options(system, stem, prefer_core=prefer))
    _rs_buf.update({"data": fresh, "disk": dict(fresh), "edits": [], "dirty": False})
    return {"saved": True}


def _rs_cancel(titleid: str) -> dict:
    _rs_reload(titleid)
    return {"cancelled": True}


@method("ragameset.get", slow=True)
def _ragameset_get(params):
    tid = params.get("titleid")
    if not tid:
        raise RpcError("EINVAL", "titleid required")
    return _rs_get(tid)


@method("ragameset.set", slow=True)
def _ragameset_set(params):
    return _rs_set(params)


@method("ragameset.save", slow=True)
def _ragameset_save(params):
    tid = params.get("titleid")
    if not tid:
        raise RpcError("EINVAL", "titleid required")
    return _rs_save(tid)


@method("ragameset.cancel", slow=True)
def _ragameset_cancel(params):
    tid = params.get("titleid")
    if not tid:
        raise RpcError("EINVAL", "titleid required")
    return _rs_cancel(tid)


# ── ragamein.* — per-game INPUT REMAP (buffered EmuSettings ns, enum selectors) ─
# Device-agnostic RetroPad choices, so every row is an ENUM ("Default (inherit)"
# + N named integer-valued options) — no physical capture / EmuInputMap needed.
# Two static player groups (P1/P2) rather than a runtime player selector — the
# simpler of the two options the spec allows, since GuiMadPageEmuSettings' plain
# groups-list contract renders it with zero new C++ assumptions.

_DEVICE_LABELS = [lbl for lbl, _v in rmp.DEVICE_OPTIONS]
_DEVICE_VALUES = [v for _lbl, v in rmp.DEVICE_OPTIONS]

# RetroArch's ANALOG_DPAD_* enum: the list index == the integer written to
# input_playerN_analog_dpad_mode. Confirmed against input/input_defines.h
# (RetroArch master): NONE=0, LSTICK=1, RSTICK=2, LSTICK_FORCED=3,
# RSTICK_FORCED=4, LRSTICK=5, TWINSTICK=6, LRSTICK_FORCED=7, TWINSTICK_FORCED=8.
# NOTE: the menu's DISPLAY order is not the enum order, so labels follow the
# header (the numeric value), not the menu switch.
_ANALOG_DPAD_LABELS = ["Off", "Left Analog", "Right Analog", "Left Analog (Forced)",
                       "Right Analog (Forced)", "Left+Right Analog", "Twin Stick",
                       "Left+Right Analog (Forced)", "Twin Stick (Forced)"]

_PORT_COUNT = 8   # RetroArch MAX_USERS; matches the 0..7 input_remap_port_pN range
                  # already observed in every real .rmp on this Deck.


def _pgin_groups() -> list[dict]:
    groups = []
    for p in (1, 2):
        groups.append({
            "title": f"Buttons (Player {p})",
            "note": "Which RetroPad button this game's core actually reads for "
                    "each input. 'Default' clears the remap for that input.",
            "items": [{"key": f"input_player{p}_btn_{name}", "label": label, "kind": "button"}
                      for name, label in zip(rmp.BUTTON_NAMES, rmp.BUTTON_LABELS)],
        })
    groups.append({
        "title": "Device",
        "note": "What kind of input device this game's core expects on each "
                "port, and whether an analog stick also drives the D-pad.",
        "items": [it for p in (1, 2) for it in (
            {"key": f"input_libretro_device_p{p}", "label": f"Player {p} device type",
             "kind": "device"},
            {"key": f"input_player{p}_analog_dpad_mode", "label": f"Player {p} analog-to-D-pad",
             "kind": "analog_dpad"},
        )],
    })
    groups.append({
        "title": "Port",
        "note": "Which physical controller port this game's core reads each player from.",
        "items": [{"key": f"input_remap_port_p{p}", "label": f"Player {p} port", "kind": "port"}
                  for p in (1, 2)],
    })
    return groups


_PGIN_GROUPS = _pgin_groups()


def _pgin_item_by_key(key: str) -> dict | None:
    for g in _PGIN_GROUPS:
        for it in g["items"]:
            if it["key"] == key:
                return it
    return None


def _inherit_enum_row(key: str, label: str, raw: str | None,
                       values: list[int], labels: list[str]) -> dict:
    """Shared shape for every ragamein row: 'Default (inherit)' + N named
    integer-valued choices, with a trailing '(current: ...)' slot for a raw
    on-disk value outside our curated set (never silently discarded)."""
    options = ["Default (inherit)"] + list(labels)
    if raw is None:
        return {"key": key, "label": label, "type": "enum", "options": options, "value": 0}
    try:
        n = int(raw)
    except (TypeError, ValueError):
        n = None
    if n is not None and n in values:
        return {"key": key, "label": label, "type": "enum",
                "options": options, "value": 1 + values.index(n)}
    return {"key": key, "label": label, "type": "enum",
            "options": options + [f"(current: {raw})"], "value": len(options)}


def _pgin_read_item(mapping: dict, it: dict) -> dict:
    key, label, kind = it["key"], it["label"], it["kind"]
    raw = mapping.get(key)
    if kind == "button":
        return _inherit_enum_row(key, label, raw, list(range(16)), list(rmp.BUTTON_LABELS))
    if kind == "device":
        return _inherit_enum_row(key, label, raw, _DEVICE_VALUES, _DEVICE_LABELS)
    if kind == "analog_dpad":
        return _inherit_enum_row(key, label, raw, list(range(len(_ANALOG_DPAD_LABELS))),
                                 _ANALOG_DPAD_LABELS)
    return _inherit_enum_row(key, label, raw, list(range(_PORT_COUNT)),
                             [f"Port {i + 1}" for i in range(_PORT_COUNT)])


def _pgin_values_for(kind: str) -> list[int]:
    if kind == "button":
        return list(range(16))
    if kind == "device":
        return _DEVICE_VALUES
    if kind == "analog_dpad":
        return list(range(len(_ANALOG_DPAD_LABELS)))
    return list(range(_PORT_COUNT))


def _pgin_write_item(it: dict, value, current: dict) -> str | None:
    try:
        n = int(float(value))
    except (TypeError, ValueError):
        raise RpcError("EINVAL", f"bad index {value!r} for {it['key']}")
    if n <= 0:
        return None                                     # Default (inherit) -> clear the key
    values = _pgin_values_for(it["kind"])
    real = n - 1
    if real < len(values):
        return str(values[real])
    return current.get(it["key"])                        # the "(current: ...)" slot


_IN_NOTE = ("Per-game RetroArch input remap. 'Default (inherit)' clears that "
            "override so RetroArch's own core/global mapping is used. Changes "
            "are staged; press Save to write the game's .rmp remap file.")

_in_buf: dict = {"titleid": None, "data": None, "disk": None, "dirty": False, "edits": []}


def _in_reload(titleid: str) -> None:
    system, stem = _split_titleid(titleid)
    # Read the LAUNCHED core's .rmp, not the alphabetically-first one (see
    # retroarch_cfg.launched_core) — a multi-core system otherwise shows/edits
    # the wrong core's per-game remap.
    prefer = retroarch_cfg.launched_core(system, stem)
    data = dict(rmp.get_game_remap(system, stem, prefer_core=prefer))
    _in_buf.update({"titleid": titleid, "data": data, "disk": dict(data), "dirty": False,
                    "edits": []})


def _in_get(titleid: str) -> dict:
    _split_titleid(titleid)
    if not (_in_buf["titleid"] == titleid and _in_buf["dirty"]):
        _in_reload(titleid)
    data = _in_buf["data"] or {}
    groups = [{"title": g["title"], "note": g.get("note", ""),
               "settings": [_pgin_read_item(data, it) for it in g["items"]]}
              for g in _PGIN_GROUPS]
    return {"exists": True, "running": proc_guard.retroarch_running(), "buffered": True,
            "dirty": _in_buf["dirty"], "note": _IN_NOTE, "groups": groups}


def _in_set(params: dict) -> dict:
    if proc_guard.retroarch_running():
        raise RpcError("EBUSY", "RetroArch is running, close it first (per-game "
                                "input writes also enable a global remap flag).")
    titleid = params.get("titleid") or ""
    _split_titleid(titleid)
    if _in_buf["titleid"] != titleid or _in_buf["data"] is None:
        _in_reload(titleid)
    key, value = params["key"], params["value"]
    it = _pgin_item_by_key(key)
    if it is None:
        raise RpcError("EINVAL", f"{key!r} is not an editable input remap")
    tok = _pgin_write_item(it, value, _in_buf["data"])
    if tok is None:
        _in_buf["data"].pop(key, None)
    else:
        _in_buf["data"][key] = tok
    _in_buf["edits"].append((key, tok))
    _in_buf["dirty"] = (_in_buf["data"] != _in_buf["disk"])
    return {"key": key, "value": _pgin_read_item(_in_buf["data"], it)["value"]}


def _in_save(titleid: str) -> dict:
    if proc_guard.retroarch_running():
        raise RpcError("EBUSY", "RetroArch is running, close it first (per-game "
                                "input writes also enable a global remap flag).")
    if _in_buf["titleid"] != titleid or not _in_buf["edits"]:
        _in_buf["dirty"] = False
        return {"saved": False}
    system, stem = _split_titleid(titleid)
    retroarch_cfg.ensure_pergame_enabled(["remaps"])
    # .rmp is a WHOLE-FILE replace (MAD owns it entirely, no sentinel), so a
    # naive push of the buffered dict would clobber any change the user made
    # in RetroArch's own Quick Menu ("Save Game Remap File") while this page
    # sat open with a now-stale snapshot. Instead: re-read the CURRENT on-disk
    # mapping fresh and replay only OUR staged (key, token) deltas onto it —
    # mirrors ragameset's per-key set_game_option replay onto its shared,
    # sentinel-scoped .cfg — so any foreign key survives untouched.
    prefer = retroarch_cfg.launched_core(system, stem)
    fresh = dict(rmp.get_game_remap(system, stem, prefer_core=prefer))
    for key, tok in _in_buf["edits"]:
        if tok is None:
            fresh.pop(key, None)
        else:
            fresh[key] = tok
    rmp.set_game_remap(system, stem, fresh)
    from .. import staterev
    staterev.bump("config")
    _in_buf.update({"data": dict(fresh), "disk": dict(fresh), "edits": [], "dirty": False})
    return {"saved": True}


def _in_cancel(titleid: str) -> dict:
    _in_reload(titleid)
    return {"cancelled": True}


@method("ragamein.get", slow=True)
def _ragamein_get(params):
    tid = params.get("titleid")
    if not tid:
        raise RpcError("EINVAL", "titleid required")
    return _in_get(tid)


@method("ragamein.set", slow=True)
def _ragamein_set(params):
    return _in_set(params)


@method("ragamein.save", slow=True)
def _ragamein_save(params):
    tid = params.get("titleid")
    if not tid:
        raise RpcError("EINVAL", "titleid required")
    return _in_save(tid)


@method("ragamein.cancel", slow=True)
def _ragamein_cancel(params):
    tid = params.get("titleid")
    if not tid:
        raise RpcError("EINVAL", "titleid required")
    return _in_cancel(tid)
