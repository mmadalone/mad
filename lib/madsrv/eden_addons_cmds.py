r"""eden_addons.* — Eden (Switch) per-game Add-Ons enable/disable.

Mirrors citron_addons_cmds.py for Eden. Citron and Eden are both Yuzu forks that share the
qt-config.ini format AND the on-disk load/<TitleID-HEX>/ mod layout, so this is a faithful clone
re-pointed at Eden's config file (~/.config/eden/qt-config.ini) and load dir
(~/.local/share/eden/load/). Only the namespace, paths and process name differ; the
[DisabledAddOns] parse/serialize logic is identical.

Eden stores DISABLED add-ons (mods/updates/DLC that are unchecked) in the [DisabledAddOns]
section of qt-config.ini as a SimpleIni-faked-QSettings counted array:

    [DisabledAddOns]
    size=<N titles>
    i\title_id\default=false
    i\title_id=<DECIMAL u64 of the titleid>
    i\disabled\size=<M>
    i\disabled\j\d\default=false
    i\disabled\j\d="<addon name>"       (quoted only when it contains a special char)

Indices are 1-BASED; the `\d` entries are emitted in a second pass after all the outer-array
scalars. Eden reads the array by key NAME (so line order doesn't matter) but iterates exactly
`size` entries, so an edit must maintain the size/index invariants -- we therefore PARSE the whole
section into a model, toggle, and RE-SERIALIZE it (Eden re-normalizes on its next exit anyway).
An add-on whose name is in a title's disabled list = DISABLED; absent = enabled.

Available add-ons for a title are the mod dirs under ~/.local/share/eden/load/<TitleID-HEX>/
(a dir with exefs/romfs/cheats content is one add-on; a dir of sub-option dirs yields
"Mod/SubOption" names, matching Eden's PatchManager). We show the UNION of those and the
persistent disabled list, so a mod that's been disabled but whose files were removed can still be
re-enabled / cleaned up. Rendered per game by GuiMadPageEmuSettings (dynamic bool toggles); Eden
rewrites config on exit, so writes refuse while it runs.
"""
from __future__ import annotations

import re
from pathlib import Path

from .. import inifile, proc_guard, staterev
from . import cfgutil
from .rpc import RpcError, method

_FILE = Path.home() / ".config/eden/qt-config.ini"
_LOAD = Path.home() / ".local/share/eden/load"
_SECTION = "DisabledAddOns"
_PROC = "eden"
_TID_RE = re.compile(r"^[0-9A-Fa-f]{16}$")
_SPECIAL = re.compile(r"[^A-Za-z0-9_.-]")           # needs quoting if it has any of these


def _running() -> bool:
    return proc_guard.emulator_running(_PROC)


def _tid(params) -> str:
    t = params.get("titleid") or ""
    if not _TID_RE.match(t):
        raise RpcError("EINVAL", f"bad game id {t!r}")
    return t


def _dec(hex_tid: str) -> str:
    return str(int(hex_tid, 16))


def _read(text: str, key: str) -> str | None:
    return cfgutil.ini_read(text, _SECTION, key)


def _unquote(v: str) -> str:
    v = v.strip()
    if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
        return v[1:-1]                                # strip only the surrounding quotes
    return v


def _quote(name: str) -> str:
    return f'"{name}"' if (name == "" or _SPECIAL.search(name)) else name


# ── parse / serialize the whole [DisabledAddOns] array ───────────────────────
def _parse(text: str) -> dict:
    """{decimal_titleid -> [disabled add-on names]} in file order (dict preserves it)."""
    model: dict[str, list[str]] = {}
    if cfgutil._ini_span(text, _SECTION) is None:
        return model
    try:
        n = int(_read(text, "size") or "0")
    except ValueError:
        n = 0
    for i in range(1, n + 1):
        tid = _read(text, f"{i}\\title_id")
        if tid is None:
            continue
        try:
            m = int(_read(text, f"{i}\\disabled\\size") or "0")
        except ValueError:
            m = 0
        names = []
        for j in range(1, m + 1):
            d = _read(text, f"{i}\\disabled\\{j}\\d")
            if d is not None:
                names.append(_unquote(d))
        model[tid.strip()] = names
    return model


def _serialize(model: dict) -> str:
    """The [DisabledAddOns] body for `model` (interleaved; Eden reads by key name)."""
    lines = [f"size={len(model)}"]
    for i, (tid, names) in enumerate(model.items(), 1):
        lines.append(f"{i}\\title_id\\default=false")
        lines.append(f"{i}\\title_id={tid}")
        lines.append(f"{i}\\disabled\\size={len(names)}")
        for j, name in enumerate(names, 1):
            lines.append(f"{i}\\disabled\\{j}\\d\\default=false")
            lines.append(f"{i}\\disabled\\{j}\\d={_quote(name)}")
    return "\n".join(lines) + "\n"


# ── available add-ons for a title (load/<HEX>/ mods) ─────────────────────────
def _has_content(d: Path) -> bool:
    if any((d / s).is_dir() for s in ("exefs", "romfs", "romfslite", "cheats")):
        return True
    try:
        return any(f.suffix.lower() in (".pchtxt", ".ips") for f in d.iterdir() if f.is_file())
    except OSError:
        return False


def _available(hex_tid: str) -> list[str]:
    base = _LOAD / hex_tid.upper()
    out: list[str] = []
    try:
        mods = sorted((p for p in base.iterdir() if p.is_dir()), key=lambda p: p.name.lower())
    except OSError:
        return out
    for mod in mods:
        if _has_content(mod):
            out.append(mod.name)
        else:                                        # a dir of sub-option dirs -> Mod/SubOption
            try:
                subs = sorted((p for p in mod.iterdir() if p.is_dir()), key=lambda p: p.name.lower())
            except OSError:
                subs = []
            hit = False
            for sub in subs:
                if _has_content(sub):
                    out.append(f"{mod.name}/{sub.name}")
                    hit = True
            if not hit:                              # bare dir, still offer it
                out.append(mod.name)
    return out


# ── get / set ────────────────────────────────────────────────────────────────
def has_content(hex_tid: str) -> bool:
    """True if this title has any add-on to manage. Mirrors _get's UNION: an installed mod dir OR a
    persistent [DisabledAddOns] entry -- a disabled update/mod whose files were removed still renders
    an OFF (re-enable) toggle in _get, so its tile must NOT be hidden. Used to hide the empty Add-Ons
    tile."""
    if _available(hex_tid):
        return True
    text = cfgutil.read_text(_FILE)
    model = _parse(text) if text is not None else {}
    return bool(model.get(_dec(hex_tid)))


@method("eden_addons.get", slow=True)
def _get(params):
    hex_tid = _tid(params)
    text = cfgutil.read_text(_FILE)
    model = _parse(text) if text is not None else {}
    disabled = model.get(_dec(hex_tid), [])
    disabled_set = set(disabled)
    # union: currently-available mods + any persistent disabled entries whose files are gone
    names = list(dict.fromkeys(_available(hex_tid) + [n for n in disabled if n not in set(_available(hex_tid))]))
    rows = [{"key": f"addon:{n}", "label": n, "type": "bool", "value": n not in disabled_set}
            for n in names]
    note = ("Enable/disable mods, updates and DLC for this game (from "
            "~/.local/share/eden/load/<TitleID>/). Off = disabled."
            if rows else
            "No add-ons found for this game. Put mods under "
            "~/.local/share/eden/load/<TitleID-in-hex>/ to see them here.")
    return {"exists": True, "running": _running(), "note": note,
            "groups": [{"title": "Add-Ons", "note": "", "settings": rows}]}


@method("eden_addons.set", slow=True)
def _set(params):
    if _running():
        raise RpcError("EBUSY", "close Eden first - it rewrites its config on exit.")
    hex_tid = _tid(params)
    key = params.get("key", "")
    if not key.startswith("addon:"):
        raise RpcError("EINVAL", f"{key!r} is not an add-on toggle")
    name = key[len("addon:"):]
    enabled = str(params.get("value", "")).strip().lower() in cfgutil._TRUE
    dec = _dec(hex_tid)
    text = cfgutil.read_text(_FILE)
    if text is None:
        raise RpcError("ENOENT", "Eden config not found - launch a game once.")
    model = _parse(text)
    disabled = model.setdefault(dec, [])
    if enabled:
        model[dec] = [n for n in disabled if n != name]
    elif name not in disabled:
        disabled.append(name)
    body = _serialize(model)
    if cfgutil._ini_span(text, _SECTION) is not None:
        new = inifile.set_section(text, _SECTION, body)
    else:                                            # create the section
        new = text + ("" if text.endswith("\n") else "\n") + f"[{_SECTION}]\n" + body
    if new != text:
        cfgutil.ensure_bak(_FILE)
        cfgutil.atomic_write(_FILE, new)
        staterev.bump("config")
    return {"key": key, "value": enabled}
