"""rpcs3pg.* — PlayStation 3 (RPCS3) PER-GAME settings editor.

Writes ONLY ~/.config/rpcs3/custom_configs/config_<SERIAL>.yml (the file RPCS3 reads to
override the global config.yml for one title). The global config.yml is NEVER touched, so
one game's override can't clobber another's or the global.

Inherit model (PCSX2 parity, valid per RPCS3 source: a per-game custom config is loaded as
an OVERLAY on top of the global config, so a key ABSENT from the custom file inherits the
GLOBAL value):
  * a key PRESENT in config_<SERIAL>.yml = an override;
  * "Inherit global" DELETES the key (byte-preserving), so it follows global again.
  * enum -> a stepper whose FIRST option is "Inherit global" (index 0 clears the key);
  * bool -> a 3-way Inherit / Off / On;
  * int  -> the numeric stepper's "Inherit global" slot (below min); a set of the string
    "inherit" clears the key.
Edits are byte-preserving single-key (cfgutil.yaml_*), so an existing hand-made full-dump
custom config (RPCS3's own "Create Custom Configuration" writes a full snapshot) is never
clobbered — inheriting a key just deletes that one line.

Reuses the FULL global category tree (rpcs3_settings.CATEGORIES: CPU/GPU/Audio/Advanced/
Emulator), rendered as one buffered page per game (category name prefixes each group title).
The two NESTED Vulkan keys are GLOBAL-ONLY here (_HIDE_PERGAME): a minimal custom config
can't safely CREATE a nested key flat (RPCS3 would ignore it).

Buffered Save/Cancel (X=Save / Y=Cancel), like the global pages: `.get` returns
buffered:true; `.set` STAGES; `.save` REPLAYS staged edits onto a FRESH read (so an external
write to other keys is preserved) + one-time .bak + atomic + staterev bump; `.cancel`
reloads. Writes are refused while RPCS3 runs (it rewrites config on exit). The picker
(`.games`) is DYNAMIC — re-reads RPCS3's games.yml each open.
"""
from __future__ import annotations

import re
from pathlib import Path

from .. import proc_guard
from . import cfgutil, rpcs3_games
from . import rpcs3_settings as gs
from .rpc import RpcError, method

_PROC = "rpcs3"
_LABEL = "RPCS3 (PS3)"
_CC_DIR = Path.home() / ".config/rpcs3/custom_configs"
_SERIAL_RE = re.compile(r"^[A-Z]{4}[0-9]{5}\Z")    # anti-traversal (\Z: no trailing newline)

# Keys shown ONLY on the global pages (a per-game minimal config can't create these nested
# Vulkan sub-block keys flat — RPCS3 reads them at Video/Vulkan/<key>, not Video/<key>).
_HIDE_PERGAME = {"Asynchronous Texture Streaming", "Asynchronous Queue Scheduler"}


def _pergame_path(serial: str) -> Path:
    return _CC_DIR / f"config_{serial}.yml"


def _pg_groups() -> list:
    """Every global category group (title prefixed with the category), minus the
    global-only keys. All items are flat top-level keys (bool/enum/int)."""
    out = []
    for _ns, (title, groups) in gs.CATEGORIES.items():
        for g in groups:
            items = [it for it in g["items"] if it["key"] not in _HIDE_PERGAME]
            if items:
                out.append({"title": f"{title} - {g['title']}", "note": g.get("note", ""),
                            "items": items})
    return out


_PG_GROUPS = _pg_groups()


def _row_id(it: dict) -> str:
    """Unique DISPLAY / C++ round-trip id for a per-game row. The per-game page flattens all
    5 categories onto ONE page, where the raw config key is NOT unique (Video/Renderer and
    Audio/Renderer both = "Renderer"); the section prefix disambiguates. The stored config
    key stays it["key"] (reads/writes use section+key), so only the round-trip id changes."""
    return f'{it["section"]}::{it["key"]}'


_ITEM_BY_ID = {_row_id(it): it for g in _PG_GROUPS for it in g["items"]}


def _item_by_key(rid: str) -> dict | None:
    return _ITEM_BY_ID.get(rid)


# ── inherit-aware row read / write (YAML) ─────────────────────────────────────
def _is_inherit(value) -> bool:
    return isinstance(value, str) and value.strip().lower() == "inherit"


def _enum_view(it: dict, raw: str | None):
    options = ["Inherit global"] + list(it["options_display"])
    if raw is None:
        return options, 0
    stored = list(it["options_stored"])
    if raw in stored:
        return options, 1 + stored.index(raw)
    return options + [f"(current: {raw})"], len(options)     # preserve an off-list value


def _read_item(pg_text: str | None, it: dict) -> dict | None:
    sec, key, typ, rid = it["section"], it["key"], it["type"], _row_id(it)
    raw = cfgutil.yaml_read(pg_text, sec, it.get("name", key)) if pg_text else None
    if typ == "bool":
        val = 0 if raw is None else (2 if cfgutil.bool_get(it, raw) else 1)
        return {"key": rid, "label": it["label"], "type": "enum",
                "options": ["Inherit global", "Off", "On"], "value": val}
    if typ == "enum":
        options, value = _enum_view(it, raw)
        return {"key": rid, "label": it["label"], "type": "enum", "options": options, "value": value}
    if typ == "int":
        if raw is None:
            value = it.get("min", 0)
        else:
            try:
                value = int(float(raw))
            except (TypeError, ValueError):
                value = it.get("min", 0)
        row = {"key": rid, "label": it["label"], "type": "int", "value": value,
               "inherit": True, "inherited": raw is None}
        for k in ("min", "max", "step"):
            if k in it:
                row[k] = it[k]
        return row
    return None


def _clear(text: str, sec: str, key: str) -> str:
    return cfgutil.yaml_drop_empty_section(cfgutil.yaml_remove(text, sec, key), sec)


def _place(text: str, sec: str, key: str, tok: str) -> str:
    nt = cfgutil.yaml_set_or_insert(cfgutil.yaml_ensure_section(text, sec), sec, key, tok)
    if nt is None:
        raise RpcError("EINTERNAL", f"couldn't place {key!r} in {sec}:")
    return nt


def _write_item(text: str, it: dict, value) -> str:
    """Stage one per-game edit into `text` (clear the key to inherit, else set)."""
    sec, typ = it["section"], it["type"]
    key = it.get("name", it["key"])
    if typ == "int":
        if _is_inherit(value):
            return _clear(text, sec, key)
        tok = cfgutil.compute_write(it, value, cfgutil.yaml_read(text, sec, key) or "")
        return _place(text, sec, key, tok)
    # bool / enum: value is a display index; 0 (or "inherit") clears the override
    if _is_inherit(value):
        return _clear(text, sec, key)
    try:
        n = int(float(value))
    except (TypeError, ValueError):
        raise RpcError("EINVAL", f"bad index {value!r} for {key}")
    if n <= 0:
        return _clear(text, sec, key)
    if typ == "bool":
        tok = it.get("bool_true", "true") if n >= 2 else it.get("bool_false", "false")
    else:                                             # enum: shift past the Inherit slot
        stored = list(it["options_stored"])
        real = n - 1
        if real >= len(stored):
            return text          # the "(current: …)" off-list slot = keep the existing value (no-op)
        tok = stored[real]
    return _place(text, sec, key, tok)


def _has_overrides(text: str | None) -> bool:
    return bool(text and re.search(r"(?m)^[ \t]+\S", text))    # any indented key line


# ── buffered engine (per serial) ──────────────────────────────────────────────
_buf: dict = {"serial": None, "text": None, "disk": None, "dirty": False, "edits": []}


def _running() -> bool:
    return proc_guard.emulator_running(_PROC)


def _reload(serial: str) -> None:
    text = cfgutil.read_text(_pergame_path(serial))
    _buf.update({"serial": serial, "text": text, "disk": text, "dirty": False, "edits": []})


def _check(serial: str) -> None:
    if not _SERIAL_RE.match(serial or ""):
        raise RpcError("EINVAL", f"bad game id {serial!r}")


def _pergame_get(serial: str) -> dict:
    _check(serial)
    if not (_buf["serial"] == serial and _buf["dirty"]):
        _reload(serial)
    pg_text = _buf["text"]
    groups = []
    for g in _PG_GROUPS:
        settings = [row for it in g["items"] if (row := _read_item(pg_text, it))]
        if settings:
            groups.append({"title": g["title"], "note": g.get("note", ""), "settings": settings})
    note = ("Per-game overrides (kept in custom_configs/config_" + serial + ".yml). Pick "
            "'Inherit global' to clear one back to the global default. Changes are staged; "
            "press Save.")
    # exists MUST be true: a missing custom config is the normal first-use state (created on
    # demand). The C++ hides all controls when exists=false, so a fresh game would show nothing.
    return {"exists": True, "running": _running(), "buffered": True, "dirty": _buf["dirty"],
            "note": note, "groups": groups}


def _echo(it: dict, text: str) -> dict:
    """The C++-shaped reply fields after a set. Echoes value always, plus the int inherit
    flags so a min-valued int cleared to Inherit doesn't look like an explicit override."""
    row = _read_item(text, it) or {}
    out = {"value": row.get("value", 0)}
    if row.get("type") == "int":
        out["inherit"] = row.get("inherit")
        out["inherited"] = row.get("inherited")
    return out


def _pergame_set(params: dict) -> dict:
    if _running():
        raise RpcError("EBUSY", f"{_LABEL} is running — close it first "
                                "(it rewrites its config on exit).")
    serial = params.get("titleid") or ""
    _check(serial)
    if _buf["serial"] != serial or _buf["text"] is None:
        _reload(serial)
    key, value = params["key"], params["value"]
    it = _item_by_key(key)
    if it is None:
        raise RpcError("EINVAL", f"{key!r} is not an editable setting")
    _buf["text"] = _write_item(_buf["text"] or "", it, value)
    _buf["edits"].append((key, value))
    _buf["dirty"] = (_buf["text"] != _buf["disk"])
    return {"key": key, "dirty": _buf["dirty"], **_echo(it, _buf["text"])}


def _pergame_save(serial: str) -> dict:
    _check(serial)
    if _running():
        raise RpcError("EBUSY", f"{_LABEL} is running — close it first "
                                "(it rewrites its config on exit).")
    from .. import staterev
    if _buf["serial"] != serial or not _buf["edits"]:
        _buf["dirty"] = False
        return {"saved": False}
    fresh = cfgutil.read_text(_pergame_path(serial))
    text = fresh or ""
    for key, value in _buf["edits"]:
        it = _item_by_key(key)
        if it is not None:
            text = _write_item(text, it, value)
    # Don't CREATE an empty custom config for a game that had none (all edits netted to
    # inherit) — an absent file == pure global, which is exactly the intent.
    if fresh is None and not _has_overrides(text):
        _buf.update({"text": None, "disk": None, "edits": [], "dirty": False})
        return {"saved": False}
    saved = text != (fresh or "")
    if saved:
        pg = _pergame_path(serial)
        pg.parent.mkdir(parents=True, exist_ok=True)
        cfgutil.ensure_bak(pg)                          # no-op when the file is new
        cfgutil.atomic_write(pg, text)
        staterev.bump("config")                         # refresh the picker's "custom" badge
    _buf.update({"text": text, "disk": text, "edits": [], "dirty": False})
    return {"saved": saved}


def _pergame_cancel(serial: str) -> dict:
    _check(serial)
    _reload(serial)
    return {"cancelled": True}


def _pergame_games() -> dict:
    games = rpcs3_games.games()
    if not games:
        return {"games": [], "system": "ps3",
                "note": "No PS3 games found in RPCS3's game list. Open RPCS3 once so it "
                        "scans your PS3 folder, then reopen this page."}
    out = []
    for g in games:
        override = _has_overrides(cfgutil.read_text(_pergame_path(g["key"])))
        out.append({"titleid": g["key"], "name": g["name"], "stem": rpcs3_games.stem_of(g["path"]),
                    "override": override, "summary": "Custom settings" if override else ""})
    return {"games": out, "system": "ps3"}


@method("rpcs3pg.get", slow=True)
def _get(params):
    tid = params.get("titleid")
    if not tid:
        raise RpcError("EINVAL", "titleid required")
    return _pergame_get(tid)


@method("rpcs3pg.set", slow=True)
def _set(params):
    return _pergame_set(params)


@method("rpcs3pg.save", slow=True)
def _save(params):
    tid = params.get("titleid")
    if not tid:
        raise RpcError("EINVAL", "titleid required")
    return _pergame_save(tid)


@method("rpcs3pg.cancel", slow=True)
def _cancel(params):
    tid = params.get("titleid")
    if not tid:
        raise RpcError("EINVAL", "titleid required")
    return _pergame_cancel(tid)


@method("rpcs3pg.games", slow=True)
def _games(params):
    return _pergame_games()
