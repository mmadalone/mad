r"""citron_cheats.* — Citron (Switch) per-game cheat enable/disable.

Citron stores DISABLED cheats in [DisabledCheats] of qt-config.ini, the same SimpleIni-faked
counted array as [DisabledAddOns] but keyed by the game's main-NSO BUILD ID (a 64-hex string):

    [DisabledCheats]
    size=<N build ids>
    i\build_id\default=false
    i\build_id=<64-hex>
    i\disabled\size=<M>
    i\disabled\j\d\default=false
    i\disabled\j\d="<Cheat Name>"

Cheats for a title live at ~/.local/share/citron/load/<TitleID-HEX>/<mod>/cheats/<BUILDID>.txt as
`[Cheat Name]` bracketed headers; the cheat file BASENAME is the 16-hex (u64) truncation of the
build id, zero-padded to 64 for the config key (so enumeration needs no separate build-id lookup).
A cheat whose name is in its build id's disabled list = DISABLED; absent = enabled. NOTE: cheats
only exist once the user adds cheat files, so this page is empty until then. Rendered per game by
GuiMadPageEmuSettings (dynamic bool toggles); writes refuse while Citron runs.
"""
from __future__ import annotations

import re
from pathlib import Path

from .. import inifile, proc_guard, staterev
from . import cfgutil
from .rpc import RpcError, method

_FILE = Path.home() / ".config/citron/qt-config.ini"
_LOAD = Path.home() / ".local/share/citron/load"
_SECTION = "DisabledCheats"
_PROC = "citron"
_TID_RE = re.compile(r"^[0-9A-Fa-f]{16}$")
_CHEAT_HDR = re.compile(r"^\s*\[([^\]]+)\]\s*$")     # [Cheat Name] header
_SPECIAL = re.compile(r"[^A-Za-z0-9_.-]")


def _running() -> bool:
    return proc_guard.emulator_running(_PROC)


def _tid(params) -> str:
    t = params.get("titleid") or ""
    if not _TID_RE.match(t):
        raise RpcError("EINVAL", f"bad game id {t!r}")
    return t


def _read(text: str, key: str) -> str | None:
    return cfgutil.ini_read(text, _SECTION, key)


def _unquote(v: str) -> str:
    v = v.strip()
    if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
        return v[1:-1]                                # strip only the surrounding quotes
    return v


def _quote(name: str) -> str:
    return f'"{name}"' if (name == "" or _SPECIAL.search(name)) else name


# ── parse / serialize [DisabledCheats] (keyed by build_id STRING) ────────────
def _parse(text: str) -> dict:
    model: dict[str, list[str]] = {}
    if cfgutil._ini_span(text, _SECTION) is None:
        return model
    try:
        n = int(_read(text, "size") or "0")
    except ValueError:
        n = 0
    for i in range(1, n + 1):
        bid = _read(text, f"{i}\\build_id")
        if bid is None:
            continue
        try:
            m = int(_read(text, f"{i}\\disabled\\size") or "0")
        except ValueError:
            m = 0
        names = [d for j in range(1, m + 1)
                 if (d := _read(text, f"{i}\\disabled\\{j}\\d")) is not None]
        model[_unquote(bid)] = [_unquote(x) for x in names]
    return model


def _serialize(model: dict) -> str:
    lines = [f"size={len(model)}"]
    for i, (bid, names) in enumerate(model.items(), 1):
        lines.append(f"{i}\\build_id\\default=false")
        lines.append(f"{i}\\build_id={_quote(bid)}")
        lines.append(f"{i}\\disabled\\size={len(names)}")
        for j, name in enumerate(names, 1):
            lines.append(f"{i}\\disabled\\{j}\\d\\default=false")
            lines.append(f"{i}\\disabled\\{j}\\d={_quote(name)}")
    return "\n".join(lines) + "\n"


# ── enumerate cheats for a title: {build_id64 -> [cheat names]} ──────────────
def _resolve_bid(stem: str, config_bids) -> str:
    """The [DisabledCheats] config key for a cheat file whose basename is `stem`. Citron's config
    stores the FULL build id (20-byte SHA1 = 40 hex) lowercased + zero-padded to 64; a cheat file
    is usually named with only the 16-hex (u64) truncation. So: prefer an EXISTING config build_id
    that shares the file's 16-hex prefix (edit the right entry), else zero-pad the stem to 64. The
    trailing bytes of a full build id can't be reconstructed from a 16-hex filename, so a 16-hex
    cheat for a game Citron has never fingerprinted can't be matched -- an inherent limitation."""
    s = stem.lower()
    prefix = s[:16]
    for b in config_bids:
        if b.lower().startswith(prefix):
            return b.lower()
    return (s + "0" * 64)[:64]


def _cheats(hex_tid: str, config_bids=()) -> dict:
    out: dict[str, list[str]] = {}
    base = _LOAD / hex_tid.upper()
    try:
        txts = sorted(base.glob("*/cheats/*.txt"))
    except OSError:
        return out
    for txt in txts:
        stem = txt.stem                              # 16-hex (u64 trunc) or the full build id
        if not re.fullmatch(r"[0-9A-Fa-f]{16,64}", stem):
            continue
        bid = _resolve_bid(stem, config_bids)
        try:
            names = [m.group(1).strip() for line in txt.read_text(
                encoding="utf-8", errors="replace").splitlines()
                if (m := _CHEAT_HDR.match(line))]
        except OSError:
            names = []
        for name in names:
            out.setdefault(bid, [])
            if name not in out[bid]:
                out[bid].append(name)
    return out


def _key(bid: str, name: str) -> str:
    return f"cheat:{bid}:{name}"


def has_content(hex_tid: str) -> bool:
    """True if this title has any cheat (a *.txt with cheat headers under load/<tid>/*/cheats/). Used
    to hide the empty per-game Cheats tile."""
    return bool(_cheats(hex_tid))


@method("citron_cheats.get", slow=True)
def _get(params):
    hex_tid = _tid(params)
    text = cfgutil.read_text(_FILE)
    disabled = _parse(text) if text is not None else {}
    avail = _cheats(hex_tid, disabled.keys())        # resolve build ids against existing config entries
    rows = []
    for bid, names in avail.items():
        dset = set(disabled.get(bid, []))
        for name in names:
            rows.append({"key": _key(bid, name), "label": name, "type": "bool",
                         "value": name not in dset})
    note = ("Enable/disable cheats for this game. Off = disabled." if rows else
            "No cheats found. Put a <BuildID>.txt cheat file under "
            "~/.local/share/citron/load/<TitleID-in-hex>/<mod>/cheats/ to see them here.")
    return {"exists": True, "running": _running(), "note": note,
            "groups": [{"title": "Cheats", "note": "", "settings": rows}]}


@method("citron_cheats.set", slow=True)
def _set(params):
    if _running():
        raise RpcError("EBUSY", "close Citron first — it rewrites its config on exit.")
    _tid(params)
    key = params.get("key", "")
    if not key.startswith("cheat:") or key.count(":") < 2:
        raise RpcError("EINVAL", f"{key!r} is not a cheat toggle")
    _, bid, name = key.split(":", 2)
    enabled = str(params.get("value", "")).strip().lower() in cfgutil._TRUE
    text = cfgutil.read_text(_FILE)
    if text is None:
        raise RpcError("ENOENT", "Citron config not found — launch a game once.")
    model = _parse(text)
    disabled = model.setdefault(bid, [])
    if enabled:
        model[bid] = [n for n in disabled if n != name]
    elif name not in disabled:
        disabled.append(name)
    body = _serialize(model)
    if cfgutil._ini_span(text, _SECTION) is not None:
        new = inifile.set_section(text, _SECTION, body)
    else:
        new = text + ("" if text.endswith("\n") else "\n") + f"[{_SECTION}]\n" + body
    if new != text:
        cfgutil.ensure_bak(_FILE)
        cfgutil.atomic_write(_FILE, new)
        staterev.bump("config")
    return {"key": key, "value": enabled}
