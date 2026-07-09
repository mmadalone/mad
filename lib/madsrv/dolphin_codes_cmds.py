r"""dolphin_ar.* + dolphin_gecko.* -- per-game AR / Gecko code enable/disable (GameCube/Wii, Dolphin).

Dolphin stores codes in `GameSettings/<ID>.ini` as `$<Name>` headers followed by code lines, under
`[ActionReplay]` / `[Gecko]`, with the enabled/disabled ones listed (as `$<Name>` lines) under
`[<Section>_Enabled]` / `[<Section>_Disabled]`. Codes come from BOTH the user file and the bundled
read-only DB (union by name). This page lists every code the game has as an on/off toggle. Toggling only writes the `$Name` to the
user file's `_Enabled` / `_Disabled` list, and ONLY when the desired state differs from the Sys-DB
default (mirroring Dolphin's SaveCodes exactly); code BODIES are never copied (Sys bodies stay in Sys).

CODE IDENTITY (verified vs Dolphin GeckoCodeConfig.cpp / ActionReplay.cpp): for GECKO, Dolphin's
canonical name is the text BEFORE the first '[' (the ' [creator]' suffix is split off), and the
enabled/disabled lists store that canonical name -- so we must match on it, NOT the full header. For
ACTION REPLAY, the full remainder IS the name (no creator split). ENABLED STATE layers: a code is on
if the bundled DB or the user file enables it, unless the user file DISABLES it -- so to turn OFF a
code the bundled DB enables by default we must write it to the user `[<Section>_Disabled]`, not merely
omit it from `[<Section>_Enabled]`. The `[<Section>]` block body keeps the ORIGINAL `$Name [creator]`
header (Dolphin reconstructs it there). Writes refuse while Dolphin runs; byte-preserving + atomic + .bak.
"""
from __future__ import annotations

import re
from pathlib import Path

from .. import dolphin_gameids as gids
from .. import proc_guard, staterev
from . import cfgutil
from .rpc import RpcError, method

_PROC = "dolphin"
_ID_RE = re.compile(r"^[A-Z0-9]{6}$")
_NAME_RE = re.compile(r"^[$+](.+?)\s*$")          # "$Name" header ("+Name" = legacy inline-enabled)


def _tid(params) -> str:
    t = (params.get("titleid") or "").strip()
    if not _ID_RE.match(t):
        raise RpcError("EINVAL", f"bad game id {t!r}")
    return t


def _running() -> bool:
    return proc_guard.emulator_running(_PROC)


def _canon(section: str, raw: str) -> str:
    """Dolphin's canonical code identity. Gecko: text before the first '[' (drops ' [creator]');
    ActionReplay: the full remainder. Matches Dolphin's on-disk enabled/disabled lists."""
    n = raw.strip()
    if section == "Gecko":
        n = n.split("[", 1)[0].strip()
    return n


# ── section-body parsing ─────────────────────────────────────────────────────
def _body(text: str, ini_section: str) -> str:
    span = cfgutil._ini_span(text or "", ini_section)
    return text[span[0]:span[1]] if span else ""


def _raw_headers(text: str, ini_section: str) -> list[str]:
    """The raw $Name strings (post-$, stripped) in [ini_section], in order (deduped)."""
    out: list[str] = []
    for line in _body(text, ini_section).splitlines():
        m = _NAME_RE.match(line.strip())
        if m and m.group(1).strip() not in out:
            out.append(m.group(1).strip())
    return out


def _names(text: str, section: str) -> list[str]:
    """Canonical code names defined in [section] (deduped, in order)."""
    seen: set = set()
    out: list[str] = []
    for raw in _raw_headers(text, section):
        c = _canon(section, raw)
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def _enabled(text: str, section: str) -> list[str]:
    return _names(text, f"{section}_Enabled")


def _disabled(text: str, section: str) -> list[str]:
    return _names(text, f"{section}_Disabled")


def _blocks(text: str, section: str) -> dict:
    """{canonical name: full block-text} for each code block in [section] -- the block INCLUDES its
    ORIGINAL `$Name [creator]` header line and its code lines, newline-terminated."""
    out: dict[str, list[str]] = {}
    cur: str | None = None
    for line in _body(text, section).splitlines():
        m = _NAME_RE.match(line.strip())
        if m:
            cur = _canon(section, m.group(1))
            out.setdefault(cur, [line.rstrip("\r")])
        elif cur is not None and line.strip():
            out[cur].append(line.rstrip("\r"))
        elif not line.strip():
            cur = None                            # a blank line ends the current block
    return {k: "\n".join(v) + "\n" for k, v in out.items()}


def _sources(gid: str) -> list[str]:
    """Each source file's TEXT, low->high priority: the bundled-DB fallback chain, then the user file.
    Parsed SEPARATELY (never concatenated) -- each has its own [section] and cfgutil._ini_span only
    sees the first, so concatenating would drop every later file's codes."""
    out: list[str] = []
    for p in gids.bundled_chain(gid):
        try:
            out.append(p.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            pass
    out.append(cfgutil.read_text(gids.user_ini(gid)) or "")
    return out


def _all_codes(gid: str, section: str) -> tuple[list[str], dict]:
    """(ordered canonical names, {name: block}) for the union of bundled + user codes in [section].
    Later (user) sources win the body on a name clash."""
    order: list[str] = []
    blocks: dict[str, str] = {}
    for src in _sources(gid):
        b = _blocks(src, section)
        for name in _names(src, section):
            if name not in blocks:
                order.append(name)
            if name in b:
                blocks[name] = b[name]
    for name in order:
        blocks.setdefault(name, f"${name}\n")
    return order, blocks


def _bundled_enabled(gid: str, section: str) -> set:
    """Canonical names the BUNDLED DB turns on by default (union of its _Enabled minus its _Disabled;
    the strict per-file layering is over-approximated, which is fine -- only a few titles ship any)."""
    on: set = set()
    off: set = set()
    for p in gids.bundled_chain(gid):
        try:
            t = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        on |= set(_enabled(t, section))
        off |= set(_disabled(t, section))
    return on - off


def has_codes(gid: str, section: str) -> bool:
    if not _ID_RE.match(gid or ""):
        return False
    names, _ = _all_codes(gid, section)
    return bool(names)


# ── enabled-list writers ([<Section>_Enabled] / _Disabled: lists of $Name lines) ─
def _rewrite_section(text: str, section: str, body: str) -> str:
    """Replace [section]'s body with `body` (creating the section at EOF if absent). Byte-preserves
    the rest of the file. Tolerates a final section header with no trailing newline."""
    text = text or ""
    if text and not text.endswith("\n"):
        text += "\n"                              # a bare final header -> normal header, so _ini_span sees it
    span = cfgutil._ini_span(text, section)
    if span is None:
        if text and not text.endswith("\n\n"):
            text += "\n"
        return text + f"[{section}]\n" + body
    return text[:span[0]] + body + text[span[1]:]


def _set_list(text: str, ini_section: str, names: list[str]) -> str:
    """Write `names` as $Name lines under [ini_section]; drop the section entirely when empty.
    Empty + section-absent is a TRUE no-op (returns text unchanged) -- creating-then-dropping would
    leave a stray trailing newline + a spurious write for a logical no-op."""
    if not names:
        if cfgutil._ini_span(text or "", ini_section) is None:
            return text or ""
        return cfgutil.ini_drop_empty_section(_rewrite_section(text, ini_section, ""), ini_section)
    return _rewrite_section(text, ini_section, "".join(f"${n}\n" for n in names))


# ── get / set ─────────────────────────────────────────────────────────────────
def _get(gid: str, section: str, label: str) -> dict:
    names, _ = _all_codes(gid, section)
    user = cfgutil.read_text(gids.user_ini(gid)) or ""
    bundled_on = _bundled_enabled(gid, section)
    effective = (bundled_on | set(_enabled(user, section))) - set(_disabled(user, section))
    rows = [{"key": f"code:{n}", "label": n, "type": "bool", "value": n in effective} for n in names]
    if not names:
        note = f"This game has no {label} in Dolphin's database or your files."
    else:
        note = f"{label} for this game (Dolphin's built-in database + your own)."
        if bundled_on:
            note += " Some are enabled by Dolphin by default."
        note += " Turning a code on also needs 'Enable cheats' on in the General settings."
    return {"exists": True, "running": _running(), "note": note,
            "groups": [{"title": label, "note": "", "settings": rows}] if rows else []}


def _set(gid: str, section: str, params: dict) -> dict:
    if _running():
        raise RpcError("EBUSY", "Dolphin is running -- close it first (it rewrites config on exit).")
    key = params.get("key") or ""
    if not key.startswith("code:"):
        raise RpcError("EINVAL", f"{key!r} is not a code toggle")
    name = key[len("code:"):]                     # the canonical name (from _get's rows)
    on = str(params.get("value")).strip().lower() in cfgutil._TRUE
    names, _ = _all_codes(gid, section)
    if name not in names:
        raise RpcError("EINVAL", f"unknown code {name!r}")
    # Mirror Dolphin's SaveCodes exactly: write a $Name to _Enabled/_Disabled ONLY when the desired
    # state differs from the Sys-DB default; NEVER copy a code body into the user file (Dolphin reads
    # bundled bodies from Sys and user bodies from the user file's [Gecko]/[ActionReplay] as they are).
    default_on = name in _bundled_enabled(gid, section)
    path = gids.user_ini(gid)
    text = cfgutil.read_text(path) or ""
    enabled = [n for n in _enabled(text, section) if n != name]
    disabled = [n for n in _disabled(text, section) if n != name]
    if on and not default_on:
        enabled.append(name)
    elif (not on) and default_on:
        disabled.append(name)
    text = _set_list(text, f"{section}_Enabled", enabled)
    text = _set_list(text, f"{section}_Disabled", disabled)
    if text != (cfgutil.read_text(path) or ""):
        path.parent.mkdir(parents=True, exist_ok=True)
        cfgutil.ensure_bak(path)                                 # no-op when the file is new
        cfgutil.atomic_write(path, text)
        staterev.bump("config")
    return {"key": key, "value": on}


@method("dolphin_ar.get", slow=True)
def _ar_get(params):
    return _get(_tid(params), "ActionReplay", "AR codes")


@method("dolphin_ar.set", slow=True)
def _ar_set(params):
    return _set(_tid(params), "ActionReplay", params)


@method("dolphin_gecko.get", slow=True)
def _gecko_get(params):
    return _get(_tid(params), "Gecko", "Gecko codes")


@method("dolphin_gecko.set", slow=True)
def _gecko_set(params):
    return _set(_tid(params), "Gecko", params)
