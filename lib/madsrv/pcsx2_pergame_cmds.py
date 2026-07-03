"""pcsx2pg.* — PlayStation 2 (standard PCSX2) PER-GAME settings editor.

Writes ONLY ~/.config/PCSX2/gamesettings/<SERIAL>_<CRC>.ini (the file PCSX2 reads to
override the global config for one disc). The global ~/.config/PCSX2/inis/PCSX2.ini is
NEVER touched, so one game's override can't clobber another's.

Reuses the FULL global category tree (`pcsx2_settings.CATEGORIES`) — Emulation, Graphics,
On-Screen Display, Audio, Advanced — rendered as one buffered page per game (category name
prefixes each group title). Every setting is per-game-inheritable:
  * enum/clamp -> a stepper whose FIRST option is "Inherit global" (index 0); picking it
    deletes the key(s) so the game inherits the global value again.
  * bool -> a 3-way Inherit / Off / On.
  * int/float/float_scaled -> the C++ numeric stepper's "Inherit global" slot (below min);
    the backend sends `inherit:true` + `inherited:<bool>`, and a set of the string
    "inherit" clears the key. (Needs the fork's numeric-inherit affordance.)
  * "Widescreen 16:9 patch" -> a repeatable `[Patches] Enable = Widescreen 16:9` toggle,
    offered only when PCSX2's patch DB has one for this game.
Model = presence-of-key: a key PRESENT in the game's ini is an override; ABSENT = inherit.

Buffered Save/Cancel (like the global pages + Lindbergh): `pcsx2pg.get` returns
`buffered:true`; `.set` STAGES an edit; `.save` REPLAYS the staged edits onto a fresh read
of the per-game file (so nothing external is clobbered); `.cancel` reloads. Writes are
refused while PCSX2 runs (it rewrites config on exit). The picker (`pcsx2pg.games`) is
DYNAMIC — re-reads PCSX2's game list fresh each open and hides deleted-ROM ghosts.
"""
from __future__ import annotations

import re
from pathlib import Path

from .. import proc_guard
from . import cfgutil, pcsx2_games
from . import pcsx2_settings as pgs
from .rpc import RpcError, method

_PROC = "pcsx2"
_LABEL = "PCSX2 (PS2)"
_GS_DIR = Path.home() / ".config/PCSX2/gamesettings"
_KEY_RE = re.compile(r"^[A-Z]{3,4}-\d{3,5}_[0-9A-F]{8}$")   # <SERIAL>_<CRC>, anti-traversal
_WS_LABEL = "Widescreen 16:9"

# Keys HIDDEN per-game (shown only globally): EnableCheats -> the per-game Cheats page owns it.
_HIDE_PERGAME = {"EnableCheats"}

# Notes for the dynamic Patches / Cheats groups (available labels are built per-game).
_PATCH_NOTE = {
    "Patches": "Patches for this game from PCSX2's database. Turning on 'Widescreen 16:9' also needs "
               "the Aspect ratio set to 16:9.",
    "Cheats":  "Cheats for this game (from ~/.config/PCSX2/cheats/). The global 'Enable Cheats' "
               "toggle must also be on for cheats to apply.",
}

# Settings PCSX2 exposes ONLY per-game (hidden on its global pages), so they're not in the
# global pcsx2_settings tree — added here per category. All keys verified present in the live
# ini. UserHacks (Manual HW Fixes) reveals the Hardware/Upscaling Fixes tabs; HWDownloadMode
# and the RTC group are per-game-only; fastCDVD is per-game-only on the Emulation page.
_PG_ONLY = {
    "pcsx2emu": [
        {"title": "Real-Time Clock", "note": "", "items": [
            pgs._bool("ManuallySetRealTimeClock", "Manually set the real-time clock", section="EmuCore"),
            # PCSX2 stores RtcYear as an OFFSET from 2000 (0-99; tm_year = RtcYear + 100), so the
            # stored value is 0-99, not the absolute year. Default 0 == year 2000.
            pgs._int("RtcYear", "RTC year (2000 + n)", section="EmuCore", min=0, max=99, step=1),
            pgs._int("RtcMonth", "RTC month", section="EmuCore", min=1, max=12, step=1),
            pgs._int("RtcDay", "RTC day", section="EmuCore", min=1, max=31, step=1),
            pgs._int("RtcHour", "RTC hour", section="EmuCore", min=0, max=23, step=1),
            pgs._int("RtcMinute", "RTC minute", section="EmuCore", min=0, max=59, step=1),
            pgs._int("RtcSecond", "RTC second", section="EmuCore", min=0, max=59, step=1),
            # UseSystemLocaleFormat is master-only (absent from the installed build's ini) -> omitted.
            pgs._bool("fastCDVD", "Enable Fast CDVD", section="EmuCore/Speedhacks"),
        ]},
    ],
    "pcsx2gfx": [
        {"title": "Manual HW Renderer Fixes", "note": "", "items": [
            pgs._bool("UserHacks", "Enable Manual Hardware Renderer Fixes"),
            pgs._enum_idx("HWDownloadMode", "GS Download Mode",
                          ["Accurate", "Disable Readbacks", "Unsynchronized", "Disabled"]),
        ]},
        {"title": "Hardware Fixes", "note": "Only apply when Manual Hardware Renderer Fixes is on.", "items": [
            pgs._int("UserHacks_CPUSpriteRenderBW", "CPU Sprite Render Size", min=0, max=10, step=1),
            pgs._enum_idx("UserHacks_CPUSpriteRenderLevel", "CPU Sprite Render Level",
                          ["Sprites Only", "Sprites / Triangles", "Blended Sprites / Triangles"]),
            pgs._enum_idx("UserHacks_CPUCLUTRender", "Software CLUT Render",
                          ["Disabled", "Normal", "Aggressive"]),
            pgs._enum_idx("UserHacks_GPUTargetCLUTMode", "GPU Target CLUT",
                          ["Disabled", "Enabled (Exact Match)", "Enabled (Check Inside Target)"]),
            pgs._enum_idx("UserHacks_TextureInsideRt", "Texture Inside RT",
                          ["Disabled", "Inside Target", "Merge Targets"]),
            pgs._enum_idx("UserHacks_AutoFlushLevel", "Auto Flush",
                          ["Disabled", "Enabled (Sprites Only)", "Enabled (All Primitives)"]),
            pgs._int("UserHacks_SkipDraw_Start", "Skip Draw Start", min=0, max=10000, step=1),
            pgs._int("UserHacks_SkipDraw_End", "Skip Draw End", min=0, max=10000, step=1),
            pgs._bool("UserHacks_CPU_FB_Conversion", "Framebuffer Conversion"),
            pgs._bool("UserHacks_DisableDepthSupport", "Disable Depth Conversion"),
            pgs._bool("UserHacks_Disable_Safe_Features", "Disable Safe Features"),
            pgs._bool("UserHacks_DisableRenderFixes", "Disable Render Fixes"),
            pgs._bool("preload_frame_with_gs_data", "Preload Frame Data"),
            pgs._bool("UserHacks_DisablePartialInvalidation", "Disable Partial Source Invalidation"),
            pgs._bool("UserHacks_ReadTCOnClose", "Read Targets When Closing"),
            pgs._bool("UserHacks_EstimateTextureRegion", "Estimate Texture Region"),
            pgs._bool("paltex", "GPU Palette Conversion"),
        ]},
        {"title": "Upscaling Fixes", "note": "Only apply when Manual Hardware Renderer Fixes is on.", "items": [
            pgs._enum_idx("UserHacks_HalfPixelOffset", "Half Pixel Offset",
                          ["Off", "Normal (Vertex)", "Special (Texture)", "Special (Texture Aggressive)",
                           "Align to Native", "Align to Native + Texture Offset"]),
            pgs._enum_idx("UserHacks_native_scaling", "Native Scaling",
                          ["Off", "Normal", "Aggressive", "Normal (Maintain Upscale)",
                           "Aggressive (Maintain Upscale)"]),
            pgs._enum_idx("UserHacks_round_sprite_offset", "Round Sprite", ["Off", "Half", "Full"]),
            pgs._enum_idx("UserHacks_BilinearHack", "Bilinear Dirty Upscale",
                          ["Automatic", "Force Bilinear", "Force Nearest"]),
            pgs._int("UserHacks_TCOffsetX", "Texture Offset X", min=0, max=1000, step=1),
            pgs._int("UserHacks_TCOffsetY", "Texture Offset Y", min=0, max=1000, step=1),
            pgs._bool("UserHacks_align_sprite_X", "Align Sprite"),
            pgs._bool("UserHacks_merge_pp_sprite", "Merge Sprite"),
            pgs._bool("UserHacks_ForceEvenSpritePosition", "Force Even Sprite Position"),
            pgs._bool("UserHacks_NativePaletteDraw", "Unscaled Palette Texture Draws"),
        ]},
    ],
}


def _pg_groups() -> list:
    """The per-game page groups: every global category group (title prefixed with the
    category) minus the per-game-hidden keys, PLUS the per-game-only groups for that
    category. (Patches/Cheats are added dynamically per-game in _pergame_get.)"""
    out = []
    for ns, (title, groups) in pgs.CATEGORIES.items():
        for g in groups:
            items = [it for it in g["items"] if it["key"] not in _HIDE_PERGAME]
            if items:
                out.append({"title": f"{title} — {g['title']}", "note": g.get("note", ""),
                            "items": items})
        for eg in _PG_ONLY.get(ns, []):
            out.append({"title": f"{title} — {eg['title']}", "note": eg.get("note", ""),
                        "items": eg["items"]})
    return out


_PG_GROUPS = _pg_groups()


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
_ENABLE_READ = re.compile(r'(?m)^[ \t]*Enable[ \t]*=[ \t]*([^\n]*?)[ \t\r]*$')
_ENABLE_LINE = re.compile(r'(?m)^[ \t]*Enable[ \t]*=[^\n]*(?:\n|$)')
_ENABLE_VAL_LINE = re.compile(r'(?m)^[ \t]*Enable[ \t]*=[ \t]*([^\n]*?)[ \t\r]*(?:\n|$)')
_OVERRIDE_LINE = re.compile(r'(?m)^[ \t]*[^\[\s#][^\n]*=')


def _patches_labels(text: str, section: str = "Patches") -> list[str]:
    span = cfgutil._ini_span(text, section)
    if not span:
        return []
    return [m.group(1) for m in _ENABLE_READ.finditer(text[span[0]:span[1]])]


def _patches_has(text: str, label: str, section: str = "Patches") -> bool:
    return label in _patches_labels(text, section)


def _patches_add(text: str, label: str, section: str = "Patches") -> str:
    if _patches_has(text, label, section):
        return text
    if text and not text.endswith("\n"):
        text += "\n"
    span = cfgutil._ini_span(text, section)
    if not span:
        pre = text
        if pre and not pre.endswith("\n\n"):
            pre += "\n"
        return pre + f"[{section}]\nEnable = {label}\n"
    body = text[span[0]:span[1]]
    ms = list(_ENABLE_LINE.finditer(body))
    at = span[0] + (ms[-1].end() if ms else len(body))
    ins = f"Enable = {label}\n"
    head = text[:at]
    if head and not head.endswith("\n"):
        ins = "\n" + ins
    return head + ins + text[at:]


def _patches_remove(text: str, label: str, section: str = "Patches") -> str:
    span = cfgutil._ini_span(text, section)
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
    if text and not text.endswith("\n"):
        text += "\n"
    if cfgutil._ini_span(text, section) is not None:
        return text
    if text and not text.endswith("\n\n"):
        text += "\n"
    return text + f"[{section}]\n"


def _has_overrides(text: str) -> bool:
    return bool(_OVERRIDE_LINE.search(text or ""))


# ── enum inherit view (Inherit global at index 0; out-of-curated token appended) ──
def _curated_index(item: dict, raw: str) -> int | None:
    if item.get("write_mode") == "option":
        stored = list(item.get("options_stored") or item.get("options_display") or [])
        return stored.index(raw) if raw in stored else None
    try:
        i = int(float(raw))
    except (TypeError, ValueError):
        return None
    disp = item.get("options_display") or item.get("options_stored") or []
    return i if 0 <= i < len(disp) else None


def _enum_view(item: dict, raw: str | None) -> tuple[list[str], int]:
    disp0 = list(item.get("options_display") or item.get("options_stored") or [])
    options = ["Inherit global"] + disp0
    if raw is None:
        return options, 0
    ci = _curated_index(item, raw)
    if ci is not None:
        return options, 1 + ci
    return options + [f"(current: {raw})"], len(options)


def _enum_stored(item: dict, real: int, pg_raw: str | None) -> str | None:
    disp = item.get("options_display") or item.get("options_stored") or []
    if 0 <= real < len(disp):
        if item.get("write_mode") == "option":
            stored = list(item.get("options_stored") or item.get("options_display") or [])
            return stored[real]
        return str(real)
    return pg_raw


# ── per-game row builders (inherit-aware for every type) ─────────────────────
def _read_item(pg_text: str | None, it: dict) -> dict | None:
    sec, key, typ = it["section"], it["key"], it["type"]
    if typ == "clamp":
        raws = [cfgutil.ini_read(pg_text, sec, k) if pg_text else None for k in it["clamp_keys"]]
        options = ["Inherit global"] + list(it["options_display"])
        if all(r is None for r in raws):
            return {"key": key, "label": it["label"], "type": "enum", "options": options, "value": 0}
        bits = [cfgutil.bool_get(it, r or "") for r in raws]
        return {"key": key, "label": it["label"], "type": "enum",
                "options": options, "value": 1 + pgs._clamp_index(bits)}
    raw = cfgutil.ini_read(pg_text, sec, it.get("name", key)) if pg_text else None
    if typ == "bool":
        val = 0 if raw is None else (2 if cfgutil.bool_get(it, raw) else 1)
        return {"key": key, "label": it["label"], "type": "enum",
                "options": ["Inherit global", "Off", "On"], "value": val}
    if typ == "enum":
        options, value = _enum_view(it, raw)
        return {"key": key, "label": it["label"], "type": "enum", "options": options, "value": value}
    if typ in ("int", "float", "float_scaled"):
        out_type = "int" if typ in ("int", "float_scaled") else "float"
        if raw is None:
            value = it.get("min", 0)
        elif typ == "float_scaled":
            try:
                value = int(round(float(raw) * it["scale"]))
            except (TypeError, ValueError):
                value = it.get("min", 0)
        else:
            try:
                value = float(raw) if typ == "float" else int(float(raw))
            except (TypeError, ValueError):
                value = it.get("min", 0)
        row = {"key": key, "label": it["label"], "type": out_type, "value": value,
               "inherit": True, "inherited": raw is None}
        for k in ("min", "max", "step"):
            if k in it:
                row[k] = it[k]
        return row
    return None


def _is_inherit(value) -> bool:
    return isinstance(value, str) and value.strip().lower() == "inherit"


def _write_item(text: str, it: dict, value) -> str:
    """Stage one per-game edit into `text` (clear-key for inherit, else set)."""
    sec, typ = it["section"], it["type"]
    if typ == "clamp":
        if _is_inherit(value):
            for k in it["clamp_keys"]:
                text = cfgutil.ini_remove(text, sec, k)
            return cfgutil.ini_drop_empty_section(text, sec)
        try:
            idx = int(float(value))
        except (TypeError, ValueError):
            raise RpcError("EINVAL", f"bad index {value!r} for {it['key']}")
        if idx <= 0:                                   # index 0 = Inherit global
            for k in it["clamp_keys"]:
                text = cfgutil.ini_remove(text, sec, k)
            return cfgutil.ini_drop_empty_section(text, sec)
        real = min(idx, len(it["options_display"])) - 1   # shift past the Inherit slot
        text = _ensure_section(text, sec)
        for i, k in enumerate(it["clamp_keys"]):
            text = cfgutil.ini_set_or_insert(text, sec, k, "true" if real >= (i + 1) else "false")
        return text
    key = it.get("name", it["key"])
    if typ in ("int", "float", "float_scaled"):
        if _is_inherit(value):
            return cfgutil.ini_drop_empty_section(cfgutil.ini_remove(text, sec, key), sec)
        if typ == "float_scaled":
            try:
                n = int(round(float(value)))
            except (TypeError, ValueError):
                raise RpcError("EINVAL", f"bad value {value!r} for {key}")
            if "min" in it and "max" in it:
                n = max(it["min"], min(it["max"], n))
            tok = cfgutil.fmt_float(n / it["scale"])
        else:
            tok = cfgutil.compute_write(it, value, cfgutil.ini_read(text, sec, key) or "")
        nt = cfgutil.ini_set_or_insert(_ensure_section(text, sec), sec, key, tok)
        if nt is None:
            raise RpcError("EINTERNAL", f"couldn't place {key} in [{sec}]")
        return nt
    # bool / enum: value is a display index; 0 (or "inherit") clears the override
    if _is_inherit(value):
        return cfgutil.ini_drop_empty_section(cfgutil.ini_remove(text, sec, key), sec)
    try:
        n = int(float(value))
    except (TypeError, ValueError):
        raise RpcError("EINVAL", f"bad index {value!r} for {key}")
    if n <= 0:
        return cfgutil.ini_drop_empty_section(cfgutil.ini_remove(text, sec, key), sec)
    if typ == "bool":
        tok = it.get("bool_true", "true") if n >= 2 else it.get("bool_false", "false")
    else:
        tok = _enum_stored(it, n - 1, cfgutil.ini_read(text, sec, key))
        if tok is None:
            raise RpcError("EINVAL", f"index {n} out of range for {key}")
    nt = cfgutil.ini_set_or_insert(_ensure_section(text, sec), sec, key, tok)
    if nt is None:
        raise RpcError("EINTERNAL", f"couldn't place {key} in [{sec}]")
    return nt


def _note(ws: bool | None) -> str:
    note = ("Per-game overrides for standard PCSX2. Pick 'Inherit global' to clear an override "
            "so the game uses the global PS2 setting. Changes are staged; press Save to apply. "
            "Nothing here changes the global config, so other games are never affected.")
    if ws is True:
        note += (" A widescreen 16:9 patch is available for this game: turn it on and set "
                 "Aspect ratio to 16:9 for proper widescreen.")
    return note


# ── buffered engine (per titleid) ─────────────────────────────────────────────
_buf: dict = {"titleid": None, "text": None, "disk": None, "dirty": False, "edits": []}


def _reload(titleid: str) -> None:
    text = cfgutil.read_text(_pergame_path(titleid))
    _buf.update({"titleid": titleid, "text": text, "disk": text, "dirty": False, "edits": []})


def _running() -> bool:
    return proc_guard.emulator_running(_PROC)


def _pergame_get(titleid: str) -> dict:
    if not _KEY_RE.match(titleid):
        raise RpcError("EINVAL", f"bad game id {titleid!r}")
    if not (_buf["titleid"] == titleid and _buf["dirty"]):
        _reload(titleid)
    serial, crc = _split(titleid)
    pg_text = _buf["text"]
    ws = pcsx2_games.has_widescreen(serial, crc) if serial and crc else None
    groups = []
    for g in _PG_GROUPS:
        settings = [row for it in g["items"] if (row := _read_item(pg_text, it))]
        if settings:
            groups.append({"title": g["title"], "note": g.get("note", ""), "settings": settings})
    # Dynamic Patches / Cheats: the available [Label] groups for THIS game, as on/off toggles
    # that add/remove a repeatable `Enable = <label>` line in [Patches] / [Cheats].
    for section, kind in (("Patches", "patches"), ("Cheats", "cheats")):
        labels = pcsx2_games.patch_labels(serial, crc, kind) if serial and crc else []
        if labels:
            rows = [{"key": f"pt:{section}:{lbl}", "label": lbl, "type": "bool",
                     "value": _patches_has(pg_text or "", lbl, section)} for lbl in labels]
            groups.append({"title": section, "note": _PATCH_NOTE[section], "settings": rows})
    # exists MUST be true: a missing gamesettings file is the normal first-use state (created on
    # demand). The C++ hides all controls when exists=false, so a fresh game would show nothing.
    return {"exists": True, "running": _running(), "buffered": True, "dirty": _buf["dirty"],
            "note": _note(ws), "groups": groups}


def _echo(it: dict, text: str) -> int | bool:
    """The C++-shaped value after a set, re-read from the staged text."""
    row = _read_item(text, it)
    return row["value"] if row else 0


def _pergame_set(params: dict) -> dict:
    if _running():
        raise RpcError("EBUSY", f"{_LABEL} is running — close it first "
                                "(it rewrites its config on exit).")
    titleid = params.get("titleid") or ""
    if not _KEY_RE.match(titleid):
        raise RpcError("EINVAL", f"bad game id {titleid!r}")
    if _buf["titleid"] != titleid or _buf["text"] is None:
        _reload(titleid)
    key, value = params["key"], params["value"]
    if key.startswith("pt:"):                       # a patch/cheat toggle: pt:<section>:<label>
        _, section, label = key.split(":", 2)
        on = str(value).strip().lower() in cfgutil._TRUE
        text = _buf["text"] or ""
        _buf["text"] = (_patches_add(text, label, section) if on
                        else _patches_remove(text, label, section))
        _buf["edits"].append((key, value))
        _buf["dirty"] = (_buf["text"] != _buf["disk"])
        return {"key": key, "value": on, "dirty": _buf["dirty"]}
    it = _item_by_key(key)
    if it is None:
        raise RpcError("EINVAL", f"{key!r} is not an editable setting")
    _buf["text"] = _write_item(_buf["text"] or "", it, value)
    _buf["edits"].append((key, value))
    _buf["dirty"] = (_buf["text"] != _buf["disk"])
    return {"key": key, "value": _echo(it, _buf["text"]), "dirty": _buf["dirty"]}


def _replay(text: str, key, value) -> str:
    if key.startswith("pt:"):
        _, section, label = key.split(":", 2)
        on = str(value).strip().lower() in cfgutil._TRUE
        return _patches_add(text, label, section) if on else _patches_remove(text, label, section)
    it = _item_by_key(key)
    return _write_item(text, it, value) if it else text


def _pergame_save(titleid: str) -> dict:
    if _running():
        raise RpcError("EBUSY", f"{_LABEL} is running — close it first "
                                "(it rewrites its config on exit).")
    from .. import staterev
    if _buf["titleid"] != titleid or not _buf["edits"]:
        _buf["dirty"] = False
        return {"saved": False}
    fresh = cfgutil.read_text(_pergame_path(titleid)) or ""
    text = fresh
    for key, value in _buf["edits"]:
        text = _replay(text, key, value)
    saved = text != fresh
    if saved:
        pg = _pergame_path(titleid)
        pg.parent.mkdir(parents=True, exist_ok=True)
        cfgutil.ensure_bak(pg)                          # no-op when the file is new
        cfgutil.atomic_write(pg, text)
        staterev.bump("config")                         # refresh the picker's "custom" badge
    _buf.update({"text": text, "disk": text, "edits": [], "dirty": False})
    return {"saved": saved}


def _pergame_cancel(titleid: str) -> dict:
    _reload(titleid)
    return {"cancelled": True}


def _pergame_games() -> dict:
    games = pcsx2_games.games()
    if not games:
        return {"games": [], "system": "ps2",
                "note": "No PS2 games found in PCSX2's game list. Open PCSX2 "
                        "once so it scans your PS2 folder, then reopen this page."}
    out = []
    for g in games:
        override = _has_overrides(cfgutil.read_text(_pergame_path(g["key"])))
        # stem = the ROM basename (ES-DE FileData getStem parity) so the media browser
        # resolves this game's art/video; "" if the cache entry has no path.
        stem = Path(g["path"]).stem if g.get("path") else ""
        out.append({"titleid": g["key"], "name": g["name"], "stem": stem,
                    "override": override, "summary": "Custom settings" if override else ""})
    # system = the ES-DE system whose media the browser resolves.
    return {"games": out, "system": "ps2"}


@method("pcsx2pg.get", slow=True)
def _get(params):
    tid = params.get("titleid")
    if not tid:
        raise RpcError("EINVAL", "titleid required")
    return _pergame_get(tid)


@method("pcsx2pg.set", slow=True)
def _set(params):
    return _pergame_set(params)


@method("pcsx2pg.save", slow=True)
def _save(params):
    tid = params.get("titleid")
    if not tid:
        raise RpcError("EINVAL", "titleid required")
    return _pergame_save(tid)


@method("pcsx2pg.cancel", slow=True)
def _cancel(params):
    tid = params.get("titleid")
    if not tid:
        raise RpcError("EINVAL", "titleid required")
    return _pergame_cancel(tid)


@method("pcsx2pg.games", slow=True)
def _games(params):
    return _pergame_games()
