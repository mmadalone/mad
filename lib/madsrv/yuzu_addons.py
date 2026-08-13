r"""Shared Yuzu-fork (Citron/Eden) per-game Add-Ons engine (citron_addons.* / eden_addons.*).

The fork stores DISABLED add-ons (mods/updates/DLC that are unchecked) in the
[DisabledAddOns] section of qt-config.ini as a SimpleIni-faked-QSettings counted array:

    [DisabledAddOns]
    size=<N titles>
    i\title_id\default=false
    i\title_id=<DECIMAL u64 of the titleid>
    i\disabled\size=<M>
    i\disabled\j\d\default=false
    i\disabled\j\d="<addon name>"       (quoted only when it contains a special char)

Indices are 1-BASED. The fork reads the array by key NAME (so line order doesn't matter)
but iterates exactly `size` entries, so an edit must maintain the size/index invariants --
we therefore PARSE the whole section into a model, toggle, and RE-SERIALIZE it (the
emulator re-normalizes on its next exit anyway). An add-on whose name is in a title's
disabled list = DISABLED; absent = enabled.

Available add-ons for a title are the mod dirs under <load>/<TitleID-HEX>/ (a dir with
exefs/romfs/cheats content is one add-on; a dir of sub-option dirs yields "Mod/SubOption"
names, matching the fork's PatchManager). We show the UNION of those and the persistent
disabled list, so a mod that's been disabled but whose files were removed can still be
re-enabled / cleaned up. Rendered per game by GuiMadPageEmuSettings (dynamic bool
toggles); the fork rewrites config on exit, so writes refuse while it runs.

One AddonsEngine instance per fork; constructing it registers <ns>.get/set. The
file/load getters are read PER CALL so a test repointing the shim's _FILE/_LOAD globals
is honored (the shims are citron_addons_cmds.py / eden_addons_cmds.py).
"""
from __future__ import annotations

import re
from pathlib import Path

from .. import inifile, proc_guard, staterev
from . import cfgutil
from .rpc import RpcError, method

_SECTION = "DisabledAddOns"
_TID_RE = re.compile(r"^[0-9A-Fa-f]{16}$")
_SPECIAL = re.compile(r"[^A-Za-z0-9_.-]")           # needs quoting if it has any of these


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


# -- parse / serialize the whole [DisabledAddOns] array -------------------------
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
    """The [DisabledAddOns] body for `model` (interleaved; the fork reads by key name)."""
    lines = [f"size={len(model)}"]
    for i, (tid, names) in enumerate(model.items(), 1):
        lines.append(f"{i}\\title_id\\default=false")
        lines.append(f"{i}\\title_id={tid}")
        lines.append(f"{i}\\disabled\\size={len(names)}")
        for j, name in enumerate(names, 1):
            lines.append(f"{i}\\disabled\\{j}\\d\\default=false")
            lines.append(f"{i}\\disabled\\{j}\\d={_quote(name)}")
    return "\n".join(lines) + "\n"


# -- available add-ons for a title (<load>/<HEX>/ mods) -------------------------
def _dir_has_content(d: Path) -> bool:
    if any((d / s).is_dir() for s in ("exefs", "romfs", "romfslite", "cheats")):
        return True
    try:
        return any(f.suffix.lower() in (".pchtxt", ".ips") for f in d.iterdir() if f.is_file())
    except OSError:
        return False


def _available(load: Path, hex_tid: str) -> list[str]:
    base = load / hex_tid.upper()
    out: list[str] = []
    try:
        mods = sorted((p for p in base.iterdir() if p.is_dir()), key=lambda p: p.name.lower())
    except OSError:
        return out
    for mod in mods:
        if _dir_has_content(mod):
            out.append(mod.name)
        else:                                        # a dir of sub-option dirs -> Mod/SubOption
            try:
                subs = sorted((p for p in mod.iterdir() if p.is_dir()), key=lambda p: p.name.lower())
            except OSError:
                subs = []
            hit = False
            for sub in subs:
                if _dir_has_content(sub):
                    out.append(f"{mod.name}/{sub.name}")
                    hit = True
            if not hit:                              # bare dir, still offer it
                out.append(mod.name)
    return out


class AddonsEngine:
    """One instance per fork; constructing it registers <ns>.get/set."""

    def __init__(self, *, ns: str, display: str, file_getter, load_getter,
                 load_hint: str, proc: str):
        self.ns, self.display = ns, display
        self._file, self._load = file_getter, load_getter
        self._load_hint, self._proc = load_hint, proc
        self._register()

    def _running(self) -> bool:
        return proc_guard.emulator_running(self._proc)

    def has_content(self, hex_tid: str) -> bool:
        """True if this title has any add-on to manage. Mirrors get's UNION: an installed
        mod dir OR a persistent [DisabledAddOns] entry -- a disabled update/mod whose files
        were removed still renders an OFF (re-enable) toggle, so its tile must NOT be
        hidden. Used to hide the empty Add-Ons tile."""
        if _available(self._load(), hex_tid):
            return True
        text = cfgutil.read_text(self._file())
        model = _parse(text) if text is not None else {}
        return bool(model.get(_dec(hex_tid)))

    def _do_get(self, params):
        hex_tid = _tid(params)
        text = cfgutil.read_text(self._file())
        model = _parse(text) if text is not None else {}
        disabled = model.get(_dec(hex_tid), [])
        disabled_set = set(disabled)
        avail = _available(self._load(), hex_tid)
        # union: currently-available mods + any persistent disabled entries whose files are gone
        names = list(dict.fromkeys(avail + [n for n in disabled if n not in set(avail)]))
        rows = [{"key": f"addon:{n}", "label": n, "type": "bool", "value": n not in disabled_set}
                for n in names]
        note = (f"Enable/disable mods, updates and DLC for this game (from "
                f"{self._load_hint}/<TitleID>/). Off = disabled."
                if rows else
                f"No add-ons found for this game. Put mods under "
                f"{self._load_hint}/<TitleID-in-hex>/ to see them here.")
        return {"exists": True, "running": self._running(), "note": note,
                "groups": [{"title": "Add-Ons", "note": "", "settings": rows}]}

    def _do_set(self, params):
        if self._running():
            raise RpcError("EBUSY", f"close {self.display} first - it rewrites its config on exit.")
        hex_tid = _tid(params)
        key = params.get("key", "")
        if not key.startswith("addon:"):
            raise RpcError("EINVAL", f"{key!r} is not an add-on toggle")
        name = key[len("addon:"):]
        enabled = str(params.get("value", "")).strip().lower() in cfgutil._TRUE
        dec = _dec(hex_tid)
        f = self._file()
        text = cfgutil.read_text(f)
        if text is None:
            raise RpcError("ENOENT", f"{self.display} config not found - launch a game once.")
        model = _parse(text)
        disabled = model.setdefault(dec, [])
        if enabled:
            model[dec] = [n for n in disabled if n != name]
        elif name not in disabled:
            disabled.append(name)
        body = _serialize(model)
        if cfgutil._ini_span(text, _SECTION) is not None:
            new = inifile.set_section(text, _SECTION, body)
        else:                                        # create the section
            new = text + ("" if text.endswith("\n") else "\n") + f"[{_SECTION}]\n" + body
        if new != text:
            cfgutil.ensure_bak(f)
            cfgutil.atomic_write(f, new)
            staterev.bump("config")
        return {"key": key, "value": enabled}

    def _register(self) -> None:
        @method(f"{self.ns}.get", slow=True)
        def _get(params, _s=self):
            return _s._do_get(params)

        @method(f"{self.ns}.set", slow=True)
        def _set(params, _s=self):
            return _s._do_set(params)
