"""lindbergh.* methods — the Sega Lindbergh per-game Settings + input binder (MAD).

Lindbergh-loader config is strictly per-game: every game owns its
~/ROMs/lindbergh/<name>.lindbergh/elf/lindbergh.ini. So this is a "pick a game,
edit that game's ini" flow (settings_pergame -> GuiMadPageGamePicker -> the game's
lindbergh.ini), NOT a global-settings emulator.

BUFFERED Save/Cancel (Miquel's choice, unlike the live-save shared page): edits
accumulate in an in-memory buffer; lindbergh.save makes the one-time .bak and
writes the ini, lindbergh.cancel reloads from disk and drops the buffer. The .get
payload carries "buffered": true so GuiMadPageEmuSettings shows the Save/Cancel
footer and routes accordingly.

A game is identified the way the loader does: zlib.crc32 of the 0x4000 bytes at
the ELF's program-header[2].p_offset + 10 (verified: ramboM.elf -> 0x048F49DD).
That CRC keys data/lindbergh-profiles.json -> {genre, native res, gun, rows}. The
binder methods (load/bind/clear) live alongside these and are added with the
capture work; this file is the Settings half first.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path

from . import cfgutil
from .. import lindbergh_pads, staterev
from .rpc import RpcError, event, method

LAUNCHERS = Path(__file__).resolve().parent.parent.parent
LINDBERGH_ROOT = Path.home() / "ROMs" / "lindbergh"
CROSSHAIR_DIR = LINDBERGH_ROOT / "_crosshairs"
GAMELIST = Path.home() / "ES-DE" / "gamelists" / "lindbergh" / "gamelist.xml"
PROFILES_PATH = LAUNCHERS / "data" / "lindbergh-profiles.json"

_profiles_cache: dict | None = None

# The in-memory editing buffer (one game at a time; the page is modal).
_buf = {"titleid": "", "text": "", "ini": None, "dirty": False}


# ── profiles + game identification ────────────────────────────────────────────
def _profiles() -> dict:
    global _profiles_cache
    if _profiles_cache is None:
        try:
            _profiles_cache = json.loads(PROFILES_PATH.read_text())
        except Exception:
            _profiles_cache = {}
    return _profiles_cache


_crc_cache: dict = {}   # (path, mtime_ns, size) -> crc str or None


def _region_crc(elf: Path) -> str | None:
    """The loader's per-rev game id: crc32 of 0x4000 bytes at program-header[2].p_offset + 10
    (ELF32). Matches getGameData()'s key exactly. SEEKs to the window instead of reading the whole
    ELF (these are up to ~86 MB on the SD card and this runs per game on every games listing), and
    memoizes per (path, mtime, size)."""
    import struct
    try:
        st = elf.stat()
    except OSError:
        return None
    key = (str(elf), st.st_mtime_ns, st.st_size)
    if key in _crc_cache:
        return _crc_cache[key]
    crc = None
    try:
        with open(elf, "rb") as f:
            hdr = f.read(0x34)                      # ELF32 header
            if len(hdr) >= 0x34 and hdr[:4] == b"\x7fELF" and hdr[4] == 1:
                e_phoff = struct.unpack_from("<I", hdr, 0x1C)[0]
                e_phentsize = struct.unpack_from("<H", hdr, 0x2A)[0]
                e_phnum = struct.unpack_from("<H", hdr, 0x2C)[0]
                if e_phnum >= 3:
                    f.seek(e_phoff + 2 * e_phentsize + 4)   # program-header[2].p_offset (ELF32)
                    po = f.read(4)
                    if len(po) == 4:
                        f.seek(struct.unpack("<I", po)[0] + 10)
                        region = f.read(0x4000)
                        if len(region) == 0x4000:           # truncated/short ELF -> not a real game
                            crc = f"{zlib.crc32(region) & 0xFFFFFFFF:08x}"
    except (OSError, struct.error, IndexError):
        crc = None
    _crc_cache[key] = crc
    return crc


def _elf_of(gamedir: Path) -> Path | None:
    """The game's main ELF, from <base>.lindbergh.commands (one relative path line,
    possibly with a leading -t flag for the test tiles -> not a real game)."""
    cmd = gamedir / f"{gamedir.name}.commands"
    try:
        line = cmd.read_text().strip()
    except OSError:
        return None
    if not line or line.startswith("-"):        # a -t test tile: skip
        return None
    elf = (gamedir / line).resolve()
    return elf if elf.is_file() else None


def _profile_of(gamedir: Path) -> dict | None:
    elf = _elf_of(gamedir)
    if elf is None:
        return None
    crc = _region_crc(elf)
    return _profiles().get(crc) if crc else None


def _ini_of(gamedir: Path) -> Path:
    """lindbergh.ini lives next to the ELF (the loader reads it from the ELF's dir): under
    elf/ for the HOTD4-style dumps, at the game-dir top for the flat VF5/ID5-style dumps."""
    elf = _elf_of(gamedir)
    return (elf.parent / "lindbergh.ini") if elf is not None else (gamedir / "elf" / "lindbergh.ini")


def _game_names() -> dict:
    out = {}
    try:
        for g in ET.parse(GAMELIST).getroot().findall("game"):
            stem = Path((g.findtext("path") or "").strip()).stem  # drops .lindbergh
            name = (g.findtext("name") or "").strip()
            if stem and name:
                out[stem] = name
    except Exception:
        pass
    return out


def _games() -> list:
    """Real games (dir has elf/lindbergh.ini, not a -test tile), with display names."""
    names = _game_names()
    out = []
    if LINDBERGH_ROOT.is_dir():
        for p in sorted(LINDBERGH_ROOT.iterdir()):
            if (p.is_dir() and p.suffix == ".lindbergh"
                    and not p.stem.endswith("-test") and _ini_of(p).is_file()):
                out.append({"titleid": p.stem, "name": names.get(p.stem, p.stem)})
    out.sort(key=lambda g: g["name"].lower())
    return out


def _gamedir(titleid: str) -> Path:
    p = (LINDBERGH_ROOT / f"{titleid}.lindbergh").resolve()
    try:
        p.relative_to(LINDBERGH_ROOT.resolve())   # path-traversal guard
    except ValueError:
        raise RpcError("EINVAL", f"bad titleid {titleid!r}")
    if not _ini_of(p).is_file():
        raise RpcError("EINVAL", f"no lindbergh.ini for {titleid!r}")
    return p


# ── per-game settings schema (built from the buffer + the game's profile) ──────
def _detect_cpu_ghz() -> float:
    try:
        khz = int(Path("/sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq").read_text())
        return round(khz / 1_000_000, 1)
    except Exception:
        return 3.5     # Steam Deck boost, the documented HOTD4 value


def _bool_row(text, section, key, label):
    raw = cfgutil.ini_read(text, section, key)
    if raw is None:
        return None
    return {"key": key, "label": label, "type": "bool",
            "value": raw.strip().lower() in cfgutil._TRUE, "section": section,
            "bool_true": "true", "bool_false": "false"}


def _enum_row(text, section, key, label, stored, display):
    raw = cfgutil.ini_read(text, section, key)
    if raw is None:
        return None
    item = {"options_stored": stored, "options_display": display, "write_mode": "option"}
    disp, val = cfgutil._enum_get(item, raw.strip())
    return {"key": key, "label": label, "type": "enum", "options": disp, "value": val,
            "section": section, "options_stored": stored, "write_mode": "option"}


def _resolution_row(text, profile):
    w = cfgutil.ini_read(text, "Display", "WIDTH")
    h = cfgutil.ini_read(text, "Display", "HEIGHT")
    if not w or not h:
        return None
    try:
        nw, nh = int(profile["native_w"]), int(profile["native_h"])
    except Exception:
        nw, nh = int(float(w)), int(float(h))
    fit_w = round(1080 * nw / nh / 2) * 2 if nh else 1920
    # The C++ "resolution" stepper uses `options` for BOTH the display and the value it
    # sends back on change, and matches the current `value` against `options[i]` verbatim.
    # So `value` must equal one of `options`; lindbergh.set extracts the WxH out of it.
    options = [f"{nw}x{nh}  (native)", f"{fit_w}x1080  (fit, aspect-correct)",
               "1920x1080  (fill, stretches)"]
    cur = f"{int(float(w))}x{int(float(h))}"
    value = next((o for o in options if o.split()[0] == cur), None)
    if value is None:
        value = f"{cur}  (current)"
        options.insert(0, value)
    return {"key": "Resolution", "label": "Resolution / aspect", "type": "resolution",
            "value": value, "options": options}


def _crosshair_row(text, key, label):
    raw = cfgutil.ini_read(text, "CrossHairs", key)
    if raw is None:
        return None
    cur = raw.strip().strip('"')
    files = sorted(p for p in CROSSHAIR_DIR.glob("*")
                   if p.suffix.lower() in (".png", ".bmp", ".jpg") and ".orig" not in p.name) \
        if CROSSHAIR_DIR.is_dir() else []
    stored = [f'"{p}"' for p in files]
    display = [p.name for p in files]
    if cur and raw.strip() not in stored:
        stored.insert(0, raw.strip())
        display.insert(0, Path(cur).name + "  (current)")
    item = {"options_stored": stored, "write_mode": "option"}
    _, val = cfgutil._enum_get({"options_stored": stored, "options_display": display,
                                "write_mode": "option"}, raw.strip())
    return {"key": key, "label": label, "type": "enum", "options": display, "value": val,
            "section": "CrossHairs", "options_stored": stored, "write_mode": "option"}


def _cpufreq_row(text):
    raw = cfgutil.ini_read(text, "GameSpecific", "CPU_FREQ_GHZ")
    if raw is None:
        return None
    raw = raw.strip()
    auto = _detect_cpu_ghz()
    pairs = [("0.0", "Off"), (f"{auto}", f"Auto ({auto} GHz)"),
             ("3.0", "3.0 GHz"), ("3.2", "3.2 GHz"), ("3.5", "3.5 GHz")]
    stored, display, seen = [], [], set()
    for s, dsp in pairs:                  # exact de-dup (e.g. auto == "3.5")
        if s in seen:
            continue
        seen.add(s)
        stored.append(s)
        display.append(dsp)
    # Mirror cfgutil._enum_write's EXACT-string membership so write_mode "option" indices
    # line up (a non-canonical on-disk value like "3" must prepend on BOTH sides, not one).
    if raw and raw not in stored:
        stored.insert(0, raw)
        display.insert(0, f"{raw} GHz  (current)")
    item = {"options_stored": stored, "options_display": display, "write_mode": "option"}
    _, idx = cfgutil._enum_get(item, raw)
    return {"key": "CPU_FREQ_GHZ", "label": "HOTD4 speed fix (CPU GHz)", "type": "enum",
            "options": display, "value": idx, "section": "GameSpecific",
            "options_stored": stored, "write_mode": "option"}


# Per-game extra bools the loader reads, by the gameID(s) they apply to.
_GAMESPECIFIC = [
    ("RAMBO_GUNS_SWITCH", "GameSpecific", "Swap P1/P2 guns", {"SBQL", "SBSS"}),
    ("SKIP_OUTRUN_CABINET_CHECK", "GameSpecific", "Skip cabinet check", {"SBMB"}),
    ("OUTRUN_LENS_GLARE_ENABLED", "Graphics", "Lens glare", {"SBMB"}),  # loader reads it from [Graphics]
    ("ID5_CHINESE_LANGUAGE", "GameSpecific", "Chinese language", {"SBRY", "SBTS", "SBQZ"}),
    ("MJ4_ENABLED_ALL_THE_TIME", "GameSpecific", "Always playable (ignore clock)", {"SBPN", "SBTA"}),
]


def _build_groups(text, profile) -> list:
    gun = bool(profile and profile.get("gun"))
    gameid = profile.get("gameid") if profile else None
    groups = []

    display = []
    if profile:
        r = _resolution_row(text, profile)
        if r:
            display.append(r)
    for key, label in [("FULLSCREEN", "Fullscreen"), ("KEEP_ASPECT_RATIO", "Keep aspect ratio"),
                       ("BOOST_RENDER_RES", "Boost render resolution"),
                       ("BORDER_ENABLED", "On-screen border"), ("HIDE_CURSOR", "Hide cursor")]:
        row = _bool_row(text, "Display", key, label)
        if row:
            display.append(row)
    if display:
        groups.append({"title": "Display", "note": "", "settings": display})

    emu = []
    r = _enum_row(text, "Emulation", "REGION", "Region", ["JP", "US", "EX"],
                  ["Japan", "USA", "Export"])
    if r:
        emu.append(r)
    fp = _bool_row(text, "Emulation", "FREEPLAY", "Free play")
    if fp:
        emu.append(fp)
    if emu:
        groups.append({"title": "Emulation", "note": "", "settings": emu})

    if gun:
        ch = []
        ec = _bool_row(text, "CrossHairs", "ENABLE_CROSSHAIRS", "Crosshairs")
        if ec:
            ch.append(ec)
        for key, label in [("P1_CROSSHAIR_PATH", "P1 crosshair"), ("P2_CROSSHAIR_PATH", "P2 crosshair")]:
            row = _crosshair_row(text, key, label)
            if row:
                ch.append(row)
        if ch:
            groups.append({"title": "Crosshairs", "note": "", "settings": ch})

    extra = []
    if gameid in ("SBLC", "SBLS"):     # CPU_FREQ_GHZ only affects HOTD4 / HOTD4 SP
        cf = _cpufreq_row(text)
        if cf:
            extra.append(cf)
    for key, section, label, gids in _GAMESPECIFIC:
        if gameid in gids:
            row = _bool_row(text, section, key, label)
            if row:
                extra.append(row)
    dm = _bool_row(text, "System", "DEBUG_MSGS", "Debug logging")
    if dm:
        extra.append(dm)
    if extra:
        groups.append({"title": "Game-specific", "note": "", "settings": extra})
    return groups


def _item(groups, key):
    for g in groups:
        for s in g["settings"]:
            if s["key"] == key:
                return s
    return None


# ── RPC methods ───────────────────────────────────────────────────────────────
def _is_gun(titleid: str) -> bool:
    try:
        return bool((_profile_of(_gamedir(titleid)) or {}).get("gun"))
    except Exception:
        return False


@method("lindbergh.games", slow=True)        # the pads filter CRCs each game's ELF -> off-thread
def _games_cmd(params):
    games = _games()
    if params and params.get("pads"):          # pads->players is non-lightgun only
        games = [g for g in games if not _is_gun(g["titleid"])]
    return {"games": games}


# ── per-game per-pad controller profiles (pads -> players); see lib/lindbergh_pads.py ──
# Dedicated RPCs for the dedicated pages: lindbergh.pads_get / .pads_set_order drive the
# priority page (GuiMadPageLindberghPads); lindbergh.pad_load / .pad_bind / .pad_clear drive
# the per-pad control map (GuiMadPageLindberghPadMap). Non-lightgun games only.
@method("lindbergh.pads_get", slow=True)
def _pads_get(params):
    """Connected pads + the saved priority order + per-pad mapped status, for the pads page.
    Rows are in priority order (then connected extras, then mapped-but-disconnected)."""
    titleid = params["titleid"]
    gd = _gamedir(titleid)
    data = lindbergh_pads.load(gd)
    priority = data.get("priority") or []
    mapped = {t for t, m in (data.get("pads") or {}).items() if m}
    conn = lindbergh_pads.connected_pads()                 # [{tag,name,label,path}]
    conn_tags = [c["tag"] for c in conn]
    label_by_tag = {c["tag"]: c["label"] for c in conn}
    ordered, seen = [], set()
    for t in list(priority) + conn_tags + sorted(mapped):  # priority, connected, then mapped-but-off
        if t not in seen:
            seen.add(t)
            ordered.append(t)
    rows = [{"tag": t, "label": label_by_tag.get(t, t),
             "connected": t in conn_tags, "mapped": t in mapped} for t in ordered]
    return {"titleid": titleid, "players": lindbergh_pads.DEFAULT_PLAYERS, "pads": rows,
            "caption": ("Order controllers (top = Player 1); at launch the top connected ones become "
                        "the players, each using its own buttons. Map a controller, then use "
                        "Make Player 1 to reorder.")}


@method("lindbergh.pads_set_order")
def _pads_set_order(params):
    titleid = params["titleid"]
    gd = _gamedir(titleid)
    data = lindbergh_pads.load(gd)
    data["priority"] = [str(t) for t in (params.get("order") or [])]
    lindbergh_pads.save(gd, data)
    staterev.bump("config")
    return {"titleid": titleid, "order": data.get("priority", []),
            "message": "Saved — applied when you launch the game."}


def _captured_tag(token: str, name: str) -> str:
    """The loader device tag from a capture result: the token is '<tag>_<name>', so the tag
    is the token minus the trailing '_<name>'. Recovers the exact tag including a _2 dup
    suffix (e.g. token 'XBOX..._2_BTN_SOUTH' + name 'BTN_SOUTH' -> 'XBOX..._2'). '' if it
    doesn't fit that shape."""
    return token[:len(token) - len(name) - 1] if name and token.endswith("_" + name) else ""


def _pad_rows(gd, tag) -> dict:
    """The control rows for one pad's map (slot-agnostic), for the pad-mode binder."""
    pad = (lindbergh_pads.load(gd).get("pads") or {}).get(tag, {})
    labels = {"BUTTON_START": "Start", "COIN": "Coin", "BUTTON_SERVICE": "Service",
              "BUTTON_UP": "Up", "BUTTON_DOWN": "Down", "BUTTON_LEFT": "Left", "BUTTON_RIGHT": "Right"}
    rows = {}
    for ctrl in lindbergh_pads.CONTROLS:
        code = pad.get(ctrl)
        label = labels.get(ctrl) or ctrl.replace("BUTTON_", "Button ").replace("_", " ").title()
        rows[ctrl] = {"key": ctrl, "label": label, "axis": False,
                      "display": code if code else "— unbound", "warn": not code}
    return rows


@method("lindbergh.pad_load", slow=True)
def _pad_load(params):
    gd = _gamedir(params["titleid"])
    tag = params["tag"]
    name = next((c["label"] for c in lindbergh_pads.connected_pads() if c["tag"] == tag), tag)
    return {"titleid": params["titleid"], "tag": tag, "pad_name": name,
            "caption": f"Map {name}'s buttons for this game (saved to this controller's profile). "
                       "Press A on a control, then press it on THIS controller.",
            "controls": list(lindbergh_pads.CONTROLS), "rows": _pad_rows(gd, tag)}


@method("lindbergh.pad_bind", slow=True)
def _pad_bind(params):
    gd = _gamedir(params["titleid"])
    tag, control, label = params["tag"], params["control"], params.get("label", params["control"])
    argv = [sys.executable, str(CAPTURE), "--timeout", "10"]
    event("input.lock", {"locked": True})
    res = {"error": "timeout"}
    proc = None
    try:
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        out, _ = proc.communicate(timeout=14)
        rc = proc.returncode
        if rc == 0 and out.strip():
            res = json.loads(out.strip())
        elif rc == 4:
            res = {"error": "no_devices"}
        elif rc == 3:
            res = {"error": "no_evdev"}
    except Exception:
        if proc is not None:
            try:
                proc.kill()
            except Exception:
                pass
    finally:
        event("input.lock", {"locked": False})

    if res.get("error"):
        msg = {"no_devices": "No input devices found.", "no_evdev": "Input capture unavailable.",
               "timeout": f"No input detected for {label}."}.get(res["error"], "Cancelled.")
        return {"message": msg, "warn": True, "rows": {}}
    token, name = res.get("token", ""), res.get("name", "")
    captured = _captured_tag(token, name)   # exact device tag (handles the _2 dup suffix)
    if captured != tag:
        return {"message": f"That was a different controller — press {label} on this one.",
                "warn": True, "rows": {}}
    data = lindbergh_pads.load(gd)
    data.setdefault("pads", {}).setdefault(tag, {})[control] = name
    if tag not in (data.get("priority") or []):
        data.setdefault("priority", []).append(tag)     # first map => add to the order
    lindbergh_pads.save(gd, data)
    staterev.bump("config")
    return {"message": f"{label} -> {name}.", "warn": False,
            "rows": {control: _pad_rows(gd, tag)[control]}}


@method("lindbergh.pad_clear")
def _pad_clear(params):
    gd = _gamedir(params["titleid"])
    tag, control = params["tag"], params["control"]
    label = params.get("label", control)
    data = lindbergh_pads.load(gd)
    pad = (data.get("pads") or {}).get(tag, {})
    pad.pop(control, None)
    if not pad:                       # last binding gone -> drop the pad from the order too (no ghost row)
        (data.get("pads") or {}).pop(tag, None)
        data["priority"] = [t for t in (data.get("priority") or []) if t != tag]
    lindbergh_pads.save(gd, data)
    staterev.bump("config")
    return {"row": _pad_rows(gd, tag)[control], "message": f"{label} unbound."}


# An analog trigger captured as a digital button used to be stored as the loader's
# ANALOGUE_TO_DIGITAL_MAX token (..._ABS_<axis>_MAX). That token's release to 0 is gated behind
# value>=midpoint in the loader (evdevInput.c:1366-1382), so a trigger snapping back to rest (0)
# never clears and the button STICKS on. The bare axis token (..._ABS_<axis>, no suffix) binds the
# loader's ungated digital path (evdevInput.c:1341-1344) and releases cleanly. On load we convert any
# stuck digital-button binding to the bare token so existing inis self-heal (Save to apply). The match
# is axis-generic (ABS_Z=LT, ABS_RZ=RT, ...) and player-generic; ANALOGUE_n channels are left alone.
# ABS_HAT* (D-pad) is EXCLUDED: a hat rests at its midpoint so its _MAX/_MIN release correctly (no stick
# bug) and the bare token is asymmetric for a hat -> a hat D-pad legitimately uses _MAX/_MIN; don't strip.
_STUCK_TRIGGER_RE = re.compile(
    r'(?m)^([ \t]*(?!ANALOGUE_)[A-Z0-9_]+[ \t]*=[ \t]*")(.+_ABS_(?!HAT)[A-Z0-9]+)_MAX(")')


def _migrate_stuck_triggers(text: str) -> tuple[str, int]:
    """Strip the buggy _MAX suffix from digital-button EVDEV bindings. Returns (text, count)."""
    span = cfgutil._ini_span(text, "EVDEV")
    if not span:
        return text, 0
    a, b = span
    new_seg, n = _STUCK_TRIGGER_RE.subn(r"\1\2\3", text[a:b])
    return (text[:a] + new_seg + text[b:], n) if n else (text, 0)


def _load_buffer(titleid: str) -> None:
    gd = _gamedir(titleid)
    ini = _ini_of(gd)
    text, migrated = _migrate_stuck_triggers(cfgutil.read_text(ini) or "")
    _buf.update(titleid=titleid, ini=ini, text=text,
                dirty=bool(migrated), profile=_profile_of(gd), migrated=migrated)


@method("lindbergh.get", slow=True)
def _get(params):
    titleid = params.get("titleid", "")
    if not titleid:
        raise RpcError("EINVAL", "lindbergh.get needs a titleid")
    _load_buffer(titleid)   # page entry = fresh from disk (no stale cross-page/-game buffer)
    groups = _build_groups(_buf["text"], _buf.get("profile"))
    note = "Edit, then Save to write the game's lindbergh.ini (a .bak is made first)."
    m = _buf.get("migrated") or 0
    if m:  # surfaced in the always-rendered note (the C++ page ignores the dirty flag)
        note = (f"Fixed {m} stuck analog-trigger binding{'s' if m != 1 else ''} "
                "(safe form); press SAVE to apply.  ") + note
    return {"exists": True, "buffered": True, "dirty": _buf["dirty"],
            "note": note, "groups": groups}


@method("lindbergh.set")
def _set(params):
    titleid = params.get("titleid", "")
    if _buf["titleid"] != titleid or _buf.get("ini") is None:
        _load_buffer(titleid)
    key, value = params["key"], params["value"]
    groups = _build_groups(_buf["text"], _buf.get("profile"))
    item = _item(groups, key)
    if item is None:
        raise RpcError("EINVAL", f"{key!r} is not an editable setting")

    if item["type"] == "resolution":
        m = re.search(r"(\d{2,5})\s*[xX]\s*(\d{2,5})", str(value))  # value is a decorated label
        if not m:
            raise RpcError("EINVAL", f"bad resolution {value!r}")
        for sec_key, v in (("WIDTH", m.group(1)), ("HEIGHT", m.group(2))):
            nt = cfgutil.ini_replace(_buf["text"], "Display", sec_key, v)
            if nt is None:
                raise RpcError("ENOKEY", f"{sec_key} not in [Display]")
            _buf["text"] = nt
        _buf["dirty"] = True
        return {"key": key, "value": f"{m.group(1)}x{m.group(2)}"}

    raw_cur = cfgutil.ini_read(_buf["text"], item["section"], key) or ""
    write = cfgutil.compute_write(item, value, raw_cur)
    nt = cfgutil.ini_replace(_buf["text"], item["section"], key, write)
    if nt is None:
        raise RpcError("ENOKEY", f"{key!r} not present in [{item['section']}]")
    _buf["text"] = nt
    _buf["dirty"] = True
    back = cfgutil.ini_read(nt, item["section"], key) or ""
    if item["type"] == "bool":
        return {"key": key, "value": back.strip().lower() in cfgutil._TRUE}
    _, v = cfgutil._enum_get(item, back.strip())
    return {"key": key, "value": v}


@method("lindbergh.save")
def _save(params):
    titleid = params.get("titleid", "")
    if _buf.get("ini") is None or (titleid and titleid != _buf["titleid"]):
        raise RpcError("EINVAL", "no edits loaded for this game — reopen the page")
    if _buf["dirty"]:
        cfgutil.ensure_bak(_buf["ini"])
        cfgutil.atomic_write(_buf["ini"], _buf["text"])
        _buf["dirty"] = False
    return {"message": "Saved. Applies the next time you launch this game."}


@method("lindbergh.cancel", slow=True)
def _cancel(params):
    titleid = params.get("titleid", "")
    if _buf.get("ini") is None or (titleid and titleid != _buf["titleid"]):
        raise RpcError("EINVAL", "no edits loaded for this game — reopen the page")
    _buf.update(text=cfgutil.read_text(_buf["ini"]) or "", dirty=False)
    return {"buffered": True, "dirty": False,
            "groups": _build_groups(_buf["text"], _buf.get("profile")),
            "message": "Reverted to what's saved on disk."}


# ── input binder (the Input-mapping section, GuiMadPageLindbergh) ──────────────
CAPTURE = LAUNCHERS / "lib" / "lindbergh_capture.py"


def _generic_rows(gun: bool) -> list:
    """The loader's full bindable key set: the no-profile fallback, and the source of
    generic-labelled rows unioned in for keys a profile omits but the ini actually binds."""
    rows = []
    for p in (1, 2):
        for b in range(1, 9):
            rows.append({"key": f"PLAYER_{p}_BUTTON_{b}", "label": f"P{p} Button {b}", "axis": False})
        for d, lbl in (("UP", "Up"), ("DOWN", "Down"), ("LEFT", "Left"), ("RIGHT", "Right")):
            rows.append({"key": f"PLAYER_{p}_BUTTON_{d}", "label": f"P{p} {lbl}", "axis": False})
        rows.append({"key": f"PLAYER_{p}_BUTTON_START", "label": f"P{p} Start", "axis": False})
        rows.append({"key": f"PLAYER_{p}_COIN", "label": f"P{p} Coin", "axis": False})
        rows.append({"key": f"PLAYER_{p}_BUTTON_SERVICE", "label": f"P{p} Service", "axis": False})
    rows.append({"key": "TEST_BUTTON", "label": "Test", "axis": False})
    if not gun:
        for a in range(1, 9):
            rows.append({"key": f"ANALOGUE_{a}", "label": f"Analog {a}", "axis": True})
    return rows


def _binder_row(text, key, label, axis) -> dict:
    raw = cfgutil.ini_read(text, "EVDEV", key)
    tok = (raw or "").strip().strip('"')
    return {"key": key, "label": label, "axis": bool(axis),
            "display": tok if tok else "— unbound", "warn": not tok}


def _evdev_keys_in(text) -> set:
    """The [EVDEV] keys actually present in this game's ini, so a profile that omits a key
    the cabinet binds (e.g. a 2-gun game's P2 buttons) still gets a bindable row."""
    span = cfgutil._ini_span(text, "EVDEV")
    if not span:
        return set()
    return set(re.findall(r"(?m)^[ \t]*([A-Z0-9_]+)[ \t]*=", text[span[0]:span[1]]))


def _binder_data(text, profile, gun) -> tuple[dict, dict]:
    rows = list(profile["rows"]) if (profile and profile.get("rows")) else []
    if rows:
        # union: add generic-labelled rows for any loader key the ini binds but the profile
        # omits (covers 2-gun games whose TP profile lists only P1).
        have = {r["key"] for r in rows}
        present = _evdev_keys_in(text)
        rows += [g for g in _generic_rows(gun) if g["key"] not in have and g["key"] in present]
    else:
        rows = _generic_rows(gun)
    sections = {"p1": [], "p2": [], "system": [], "axes": []}
    out = {}
    for r in rows:
        key, label, axis = r["key"], r["label"], r.get("axis", False)
        if axis:
            if gun:                       # aim axes are static/templated for gun games -> hidden
                continue
            sections["axes"].append(key)
        elif key == "TEST_BUTTON" or key.endswith("_SERVICE"):
            sections["system"].append(key)
        elif key.startswith("PLAYER_2"):
            sections["p2"].append(key)
        else:
            sections["p1"].append(key)
        out[key] = _binder_row(text, key, label, axis)
    return {k: v for k, v in sections.items() if v}, out


@method("lindbergh.load", slow=True)
def _binder_load(params):
    titleid = params.get("titleid", "")
    if not titleid:
        return {"games": _games(), "rows": {}, "sections": {}}    # picker only, nothing loaded
    _load_buffer(titleid)   # page entry / game-switch = fresh from disk
    profile = _buf.get("profile")
    gun = bool(profile and profile.get("gun"))
    sections, rows = _binder_data(_buf["text"], profile, gun)
    name = next((g["name"] for g in _games() if g["titleid"] == titleid), titleid)
    caption = ("Focus a control, press A, then actuate it on the gun/pad/wheel. "
               "Save writes this game's lindbergh.ini.")
    if gun:
        caption += "  Turn on Gun capture mode first so the gun is live."
    m = _buf.get("migrated") or 0
    if m:  # surfaced in the always-rendered caption (the C++ page ignores the dirty flag)
        caption = (f"Fixed {m} stuck analog-trigger binding{'s' if m != 1 else ''} "
                   "(safe form); press SAVE to apply.  ") + caption
    return {"titleid": titleid, "game_name": name, "gun": gun, "dirty": _buf["dirty"],
            "caption": caption, "sections": sections, "rows": rows, "games": _games(),
            "quit_combo": _quit_combo_for(titleid)}


def _quit_combo_for(titleid: str) -> dict:
    """The per-game hold-to-quit combo for display: scope key + evdev codes + button names, read
    from merged policy [quit_combo.lindbergh-<titleid>]. The MAD page sets it via policy.set_quit_combo
    with this exact scope (and clears it via policy.clear_quit_combo); the game-start hook feeds the
    same key to the watcher as --system. Reuses the existing quit-combo machinery; empty when unset."""
    scope = f"lindbergh-{titleid}"
    codes: list[int] = []
    try:
        from ..policy import load_merged
        ent = (load_merged().get("quit_combo") or {}).get(scope)
        if isinstance(ent, dict) and ent.get("buttons"):
            codes = [int(b) for b in ent["buttons"]]
    except Exception:
        codes = []
    names: list[str] = []
    if codes:
        try:
            from .capture_cmds import btn_name
            names = [btn_name(c) for c in codes]
        except Exception:
            names = [str(c) for c in codes]
    return {"scope": scope, "buttons": codes, "names": names,
            "display": " + ".join(names) if names else ""}


@method("lindbergh.bind", slow=True)
def _bind(params):
    titleid = params.get("titleid", "")
    if _buf["titleid"] != titleid or _buf.get("ini") is None:
        _load_buffer(titleid)
    key = params["key"]
    axis = bool(params.get("axis"))
    label = params.get("label", key)
    argv = [sys.executable, str(CAPTURE), "--timeout", "10"]
    if axis:
        argv.append("--axis")

    event("input.lock", {"locked": True})
    res = {"error": "timeout"}
    proc = None
    try:
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        out, _ = proc.communicate(timeout=14)
        rc = proc.returncode
        if rc == 0 and out.strip():
            res = json.loads(out.strip())
        elif rc == 4:
            res = {"error": "no_devices"}
        elif rc == 3:
            res = {"error": "no_evdev"}
    except Exception:
        if proc is not None:
            try:
                proc.kill()
            except Exception:
                pass
    finally:
        event("input.lock", {"locked": False})

    if res.get("error"):
        msg = {"no_devices": "No input devices found — is the controller/gun connected?",
               "no_evdev": "Input capture unavailable (python-evdev missing).",
               "timeout": f"No input detected for {label}."}.get(res["error"], "Cancelled.")
        return {"message": msg, "warn": True, "rows": {}, "dirty": _buf["dirty"]}

    token = res["token"]
    nt = cfgutil.ini_set_or_insert(_buf["text"], "EVDEV", key, f'"{token}"')
    if nt is None:
        return {"message": "This game's ini has no [EVDEV] section.", "warn": True, "rows": {}}
    _buf["text"] = nt
    _buf["dirty"] = True
    return {"message": f"{label} → {token}.  Save to apply.", "warn": False, "dirty": True,
            "rows": {key: _binder_row(_buf["text"], key, label, axis)}}


@method("lindbergh.clear")
def _clear_bind(params):
    titleid = params.get("titleid", "")
    if _buf["titleid"] != titleid or _buf.get("ini") is None:
        _load_buffer(titleid)
    key = params["key"]
    label = params.get("label", key)
    nt = cfgutil.ini_replace(_buf["text"], "EVDEV", key, '""')
    if nt is None:
        return {"message": f"{label} isn't set in this game's ini.", "rows": {}}
    _buf["text"] = nt
    _buf["dirty"] = True
    return {"row": _binder_row(_buf["text"], key, label, bool(params.get("axis"))),
            "message": f"{label} unbound. Save to apply."}


@method("lindbergh.test_fire", slow=True)
def _test_fire(params):
    """Capture one press and report its token WITHOUT binding — the gun-capture-mode
    readout: confirm the gun (or pad) is actually emitting before you start mapping.
    Pipeline start/stop reuses the existing sinden.driver/sinden.status methods."""
    argv = [sys.executable, str(CAPTURE), "--timeout", "8"]
    event("input.lock", {"locked": True})
    res = {"error": "timeout"}
    proc = None
    try:
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        out, _ = proc.communicate(timeout=12)
        if proc.returncode == 0 and out.strip():
            res = json.loads(out.strip())
        elif proc.returncode == 4:
            res = {"error": "no_devices"}
        elif proc.returncode == 3:
            res = {"error": "no_evdev"}
    except Exception:
        if proc is not None:
            try:
                proc.kill()
            except Exception:
                pass
    finally:
        event("input.lock", {"locked": False})
    if res.get("error"):
        msg = {"no_devices": "No input devices found — is the controller/gun connected?",
               "no_evdev": "Input capture unavailable (python-evdev missing)."
               }.get(res["error"], "Nothing fired — start the gun pipeline and check the gun/pad is on.")
        return {"message": msg, "warn": True}
    return {"message": f"✓ fired: {res['token']}", "warn": False, "token": res["token"]}


# ── gun-capture-mode LIVE readout: a passive monitor pushing one event per press ──
_monitor = {"proc": None}


def _monitor_alive() -> bool:
    p = _monitor.get("proc")
    return p is not None and p.poll() is None


@method("lindbergh.monitor_start")
def _monitor_start(params):
    """Start a passive evdev monitor that pushes a 'lindbergh.fired' event for each press,
    feeding the binder's live readout. It re-scans devices (so it catches the gun once the
    pipeline is up) and self-terminates after a cap, so it can never leak."""
    if _monitor_alive():
        return {"running": True}
    proc = subprocess.Popen([sys.executable, str(CAPTURE), "--monitor", "--timeout", "600"],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    _monitor["proc"] = proc

    def _pump(p):
        try:
            for line in p.stdout:
                line = line.strip()
                if line:
                    try:
                        event("lindbergh.fired", json.loads(line))
                    except Exception:
                        pass
        except Exception:
            pass

    threading.Thread(target=_pump, args=(proc,), daemon=True).start()
    return {"running": True}


@method("lindbergh.monitor_stop")
def _monitor_stop(params):
    p = _monitor.get("proc")
    if p is not None:
        try:
            p.terminate()
        except Exception:
            pass
        _monitor["proc"] = None
    return {"running": False}
