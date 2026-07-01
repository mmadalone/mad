"""pcsx2pg.* — PlayStation 2 (standard PCSX2) PER-GAME settings editor.

Writes ONLY ~/.config/PCSX2/gamesettings/<SERIAL>_<CRC>.ini (the file PCSX2 reads to
override the global config for one disc). The global ~/.config/PCSX2/inis/PCSX2.ini is
NEVER touched, so setting one game's aspect can't clobber another's. This is what makes
the aspect control per-game-aware: a 4:3 game gets `Auto 4:3/3:2` while a widescreen
title keeps 16:9, both as their own overrides.

Model (per-game, keyed by presence, NOT Eden's use_global triplets):
  * a key PRESENT in the game's ini = an override; ABSENT = inherit the global value.
  * every knob is shown as a stepper whose FIRST option is "Inherit global" (index 0);
    picking it deletes the key (cfgutil.ini_remove) so the game inherits again.
  * bool knobs become a 3-way Inherit / Off / On.
  * AspectRatio + the curated graphics/speed knobs are cloned from pcsx2_cmds.GROUPS.
  * "Widescreen 16:9 patch" is a plain toggle that adds/removes a single repeatable
    `[Patches] Enable = Widescreen 16:9` line; it is offered ONLY when PCSX2's patch
    database actually has a widescreen patch for this game (its presence is the badge).

Instant-save (eden model): each set is a byte-preserving atomic write with a one-time
.bak; writes are refused while PCSX2 is running (it rewrites config on exit).
"""
from __future__ import annotations

import re
from pathlib import Path

from .. import proc_guard, staterev
from . import cfgutil, pcsx2_cmds, pcsx2_games
from .rpc import RpcError, method

_PROC = "pcsx2"
_LABEL = "PCSX2 (PS2)"
_GS_DIR = Path.home() / ".config/PCSX2/gamesettings"
_KEY_RE = re.compile(r"^[A-Z]{3,4}-\d{3,5}_[0-9A-F]{8}$")   # <SERIAL>_<CRC>, anti-traversal
_WS_LABEL = "Widescreen 16:9"

# Aspect + widescreen, prepended to the curated global knobs (reused verbatim so the
# per-game page mirrors the global PS2 Settings page). WidescreenPatch is special-cased.
_ASPECT_GROUP = {"title": "Aspect & widescreen", "note": "", "items": [
    {"key": "AspectRatio", "label": "Aspect ratio", "section": "EmuCore/GS",
     "type": "enum", "write_mode": "option",
     "options_display": ["Auto 4:3/3:2", "4:3", "16:9", "Stretch"],
     "options_stored":  ["Auto 4:3/3:2", "4:3", "16:9", "Stretch"]},
    {"key": "WidescreenPatch", "label": "Widescreen 16:9 patch", "section": "Patches",
     "type": "wspatch"},
]}
_PG_GROUPS = [_ASPECT_GROUP] + pcsx2_cmds.GROUPS


def _pergame_path(titleid: str) -> Path:
    return _GS_DIR / f"{titleid}.ini"


def _split(titleid: str) -> tuple[str, str]:
    serial, _, crc = titleid.rpartition("_")
    return serial, crc.upper()


def _item_by_key(key: str) -> dict | None:
    for g in _PG_GROUPS:
        for it in g["items"]:
            if it["key"] == key:
                return it
    return None


# ── repeatable [Patches] Enable = <label> helpers (outside cfgutil's last-wins model) ─
# The trailing [ \t\r]* trims a CRLF file's stray \r out of the captured label so
# has/remove still match (files are LF on Linux, but stay robust to a hand-imported one).
_ENABLE_READ = re.compile(r'(?m)^[ \t]*Enable[ \t]*=[ \t]*([^\n]*?)[ \t\r]*$')
_ENABLE_LINE = re.compile(r'(?m)^[ \t]*Enable[ \t]*=[^\n]*(?:\n|$)')
_ENABLE_VAL_LINE = re.compile(r'(?m)^[ \t]*Enable[ \t]*=[ \t]*([^\n]*?)[ \t\r]*(?:\n|$)')
# A real override = any "key = value" line (ours or PCSX2's own), in any section. An
# emptied stub ("[EmuCore/GS]\n" left after clearing) has none, so it reads as not-custom.
_OVERRIDE_LINE = re.compile(r'(?m)^[ \t]*[^\[\s#][^\n]*=')


def _patches_labels(text: str) -> list[str]:
    span = cfgutil._ini_span(text, "Patches")
    if not span:
        return []
    return [m.group(1) for m in _ENABLE_READ.finditer(text[span[0]:span[1]])]


def _patches_has(text: str, label: str) -> bool:
    return label in _patches_labels(text)


def _patches_add(text: str, label: str) -> str:
    if _patches_has(text, label):
        return text
    if text and not text.endswith("\n"):          # a bare trailing "[Patches]" needs its \n
        text += "\n"                               # so _ini_span finds it (no duplicate section)
    span = cfgutil._ini_span(text, "Patches")
    if not span:
        pre = text
        if pre and not pre.endswith("\n\n"):
            pre += "\n"
        return pre + f"[Patches]\nEnable = {label}\n"
    body = text[span[0]:span[1]]
    ms = list(_ENABLE_LINE.finditer(body))
    at = span[0] + (ms[-1].end() if ms else len(body))
    ins = f"Enable = {label}\n"
    head = text[:at]
    if head and not head.endswith("\n"):
        ins = "\n" + ins
    return head + ins + text[at:]


def _patches_remove(text: str, label: str) -> str:
    span = cfgutil._ini_span(text, "Patches")
    if not span:
        return text
    start, end = span
    body = text[start:end]
    out, last, removed = [], 0, False
    for m in _ENABLE_VAL_LINE.finditer(body):
        if m.group(1) == label:
            out.append(body[last:m.start()])
            last, removed = m.end(), True
    if not removed:
        return text
    out.append(body[last:])
    return text[:start] + "".join(out) + text[end:]


def _ensure_section(text: str, section: str) -> str:
    if text and not text.endswith("\n"):          # a bare trailing header needs its \n first,
        text += "\n"                               # else _ini_span misses it and we duplicate it
    if cfgutil._ini_span(text, section) is not None:
        return text
    if text and not text.endswith("\n\n"):
        text += "\n"
    return text + f"[{section}]\n"


def _has_overrides(text: str) -> bool:
    """True if the per-game ini holds at least one real override (any key = value line),
    so an emptied stub left behind after clearing the last override reads as not-custom."""
    return bool(_OVERRIDE_LINE.search(text or ""))


def _enum_stored(item: dict, real: int, pg_raw: str | None) -> str | None:
    """The token to store for enum index `real` (0-based into the item's own option
    list). When the game has no current value, map cleanly into the base options;
    when it has one, mirror cfgutil's prepend-unknown behaviour via _enum_write."""
    if pg_raw is not None:
        return cfgutil._enum_write(item, real, pg_raw)
    if item.get("write_mode") == "option":
        stored = list(item.get("options_stored") or item.get("options_display") or [])
        return stored[real] if 0 <= real < len(stored) else None
    return str(real) if real >= 0 else None


# ── C++ payload builders ──────────────────────────────────────────────────────
def _setting(item: dict, pg_text: str | None) -> dict | None:
    """One settings row for the per-game page (enum steppers with Inherit at 0)."""
    sec, key, typ = item["section"], item["key"], item["type"]
    raw = cfgutil.ini_read(pg_text, sec, key) if pg_text else None
    if typ == "bool":
        val = 0 if raw is None else (2 if cfgutil.bool_get(item, raw) else 1)
        return {"key": key, "label": item["label"], "type": "enum",
                "options": ["Inherit global", "Off", "On"], "value": val}
    if typ == "enum":
        if raw is None:
            disp0 = list(item.get("options_display") or item.get("options_stored") or [])
            return {"key": key, "label": item["label"], "type": "enum",
                    "options": ["Inherit global"] + disp0, "value": 0}
        disp, idx = cfgutil._enum_get(item, raw)
        return {"key": key, "label": item["label"], "type": "enum",
                "options": ["Inherit global"] + disp, "value": 1 + idx}
    return None


def _note(ws: bool | None) -> str:
    note = ("Per-game overrides for standard PCSX2. Pick 'Inherit global' to clear an "
            "override so the game uses the global PS2 setting. Nothing here changes the "
            "global config, so other games are never affected.")
    if ws is True:
        note += (" A widescreen 16:9 patch is available for this game: turn it on and set "
                 "Aspect ratio to 16:9 for proper widescreen.")
    return note


def _pergame_get(titleid: str) -> dict:
    if not _KEY_RE.match(titleid):
        raise RpcError("EINVAL", f"bad game id {titleid!r}")
    serial, crc = _split(titleid)
    pg_text = cfgutil.read_text(_pergame_path(titleid))
    ws = pcsx2_games.has_widescreen(serial, crc) if serial and crc else None
    groups = []
    for g in _PG_GROUPS:
        settings = []
        for item in g["items"]:
            if item["key"] == "WidescreenPatch":
                if ws:                              # offer only when a patch actually exists
                    settings.append({"key": "WidescreenPatch", "label": item["label"],
                                     "type": "bool", "value": _patches_has(pg_text or "", _WS_LABEL)})
                continue
            row = _setting(item, pg_text)
            if row:
                settings.append(row)
        if settings:
            groups.append({"title": g["title"], "note": g.get("note", ""), "settings": settings})
    # Always editable: a missing gamesettings file is the NORMAL first-use state (the
    # first override creates it on demand), not an error. The C++ page hides all controls
    # when exists=false, so this must be true or a fresh game shows nothing. (Eden returns
    # exists=false only because it cannot synthesize the file; PCSX2 can, like lindbergh.)
    return {"exists": True,
            "running": proc_guard.emulator_running(_PROC),
            "note": _note(ws), "groups": groups}


def _commit(pg: Path, old: str, new: str | None) -> None:
    if new is None or new == old:
        return
    pg.parent.mkdir(parents=True, exist_ok=True)
    cfgutil.ensure_bak(pg)                          # no-op when the file is new
    cfgutil.atomic_write(pg, new)
    staterev.bump("config")                         # refresh the picker's "custom" badge


def _echo(item: dict, stored: str) -> int:
    if item["type"] == "bool":
        return 2 if cfgutil.bool_get(item, stored) else 1
    _, v = cfgutil._enum_get(item, stored)
    return 1 + v


def _pergame_set(params: dict) -> dict:
    if proc_guard.emulator_running(_PROC):
        raise RpcError("EBUSY", f"{_LABEL} is running — close it first "
                                "(it rewrites its config on exit).")
    titleid = params.get("titleid") or ""
    if not _KEY_RE.match(titleid):
        raise RpcError("EINVAL", f"bad game id {titleid!r}")
    key, value = params["key"], params["value"]
    pg = _pergame_path(titleid)
    text = cfgutil.read_text(pg) or ""

    if key == "WidescreenPatch":
        on = str(value).strip().lower() in cfgutil._TRUE
        new = _patches_add(text, _WS_LABEL) if on else _patches_remove(text, _WS_LABEL)
        _commit(pg, text, new)
        return {"key": key, "value": on}

    item = _item_by_key(key)
    if item is None:
        raise RpcError("EINVAL", f"{key!r} is not an editable setting")
    sec = item["section"]
    try:
        n = int(float(value))
    except (TypeError, ValueError):
        raise RpcError("EINVAL", f"bad index {value!r} for {key}")
    if n <= 0:                                       # Inherit global -> clear the override
        _commit(pg, text, cfgutil.ini_remove(text, sec, key))
        return {"key": key, "value": 0}
    if item["type"] == "bool":
        stored = item.get("bool_true", "true") if n >= 2 else item.get("bool_false", "false")
    else:
        stored = _enum_stored(item, n - 1, cfgutil.ini_read(text, sec, key))
        if stored is None:
            raise RpcError("EINVAL", f"index {n} out of range for {key}")
    new = cfgutil.ini_set_or_insert(_ensure_section(text, sec), sec, key, stored)
    if new is None:
        raise RpcError("EINTERNAL", f"couldn't place {key} in [{sec}]")
    _commit(pg, text, new)
    return {"key": key, "value": _echo(item, stored)}


def _pergame_games() -> dict:
    games = pcsx2_games.games()
    if not games:
        return {"games": [], "note": "No PS2 games found in PCSX2's game list. Open PCSX2 "
                                     "once so it scans your PS2 folder, then reopen this page."}
    return {"games": [{"titleid": g["key"], "name": g["name"],
                       "override": _has_overrides(cfgutil.read_text(_pergame_path(g["key"])))}
                      for g in games]}


@method("pcsx2pg.get", slow=True)
def _get(params):
    tid = params.get("titleid")
    if not tid:
        raise RpcError("EINVAL", "titleid required")
    return _pergame_get(tid)


@method("pcsx2pg.set", slow=True)
def _set(params):
    return _pergame_set(params)


@method("pcsx2pg.games", slow=True)
def _games(params):
    return _pergame_games()
