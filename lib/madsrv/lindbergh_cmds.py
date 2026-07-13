"""lindbergh.* methods — the Sega Lindbergh per-game Settings + input binder (MAD).

Lindbergh-loader config is strictly per-game: every game owns its
~/ROMs/lindbergh/<name>.lindbergh/elf/lindbergh.ini. So this is a "pick a game,
edit that game's ini" flow (settings_pergame -> GuiMadPagePergameBrowser, the media+info
per-game browser -> the game's lindbergh.ini), NOT a global-settings emulator.

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
_buf = {"titleid": "", "text": "", "ini": None, "dirty": False, "disk": ""}


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
                # stem = the ES-DE FileData getStem. lindbergh is a folder-as-file system, and
                # ES-DE getStem does NOT strip the extension for a DIRECTORY, so getStem of
                # "<name>.lindbergh" is "<name>.lindbergh" (not "<name>"). Emit the full folder name
                # so the media browser resolves this game's art/video. titleid stays the bare stem
                # (the lindbergh RPCs' game identity). Every lindbergh game IS its own per-game ini,
                # so summary reflects that (there is no global to inherit from).
                row = {"titleid": p.stem, "name": names.get(p.stem, p.stem),
                       "stem": p.name, "summary": "Per-game config"}
                # The game-first per-game menu (settings_pergame_menu) offers Controllers
                # (pads->players) for every game; hide that leaf on games where it is inert --
                # lightgun titles and profile-less / empty-rows games (which would blank PLAYER_2
                # at launch). Same criterion the old dedicated Controllers picker used (its
                # pads:true filter); the browser drops any leaf whose `key` is in this hide list.
                if not _pad_eligible(p.stem):
                    row["hide"] = ["lindbergh_pads"]
                out.append(row)
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


def _pad_eligible(titleid: str) -> bool:
    """Whether a game is offered on the pads->players page: non-lightgun AND has a profile with
    digital controls. A profile-less / empty-rows game defaults to 2-human and would blank PLAYER_2
    at launch — which a mahjong/quiz panel or an unrecognised revision may legitimately bind — so it
    is steered to the per-PLAYER Input binder (which maps the ini's actual keys) instead."""
    try:
        prof = _profile_of(_gamedir(titleid))
    except Exception:
        return False
    return bool(prof and prof.get("rows") and not prof.get("gun"))


@method("lindbergh.games", slow=True)        # the pads filter CRCs each game's ELF -> off-thread
def _games_cmd(params):
    games = _games()
    if params and params.get("pads"):          # pads->players: non-lightgun + has a usable profile
        games = [g for g in games if _pad_eligible(g["titleid"])]
    # system = the ES-DE system whose media the browser resolves.
    return {"games": games, "system": "lindbergh"}


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
    profile = _profile_of(gd)
    _heal_sidecar(gd, profile)              # migrate a pre-rework / stale sidecar before reading it
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
    one_human = not _two_human(profile)
    caption = ("Order controllers (top = the active one); whichever top controller is connected drives "
               "this 1-player game with its own buttons. Map a controller, then use Make Player 1 to reorder."
               if one_human else
               "Order controllers (top = Player 1); at launch the top connected ones become the players, "
               "each using its own buttons. Map a controller, then use Make Player 1 to reorder.")
    return {"titleid": titleid, "players": 1 if one_human else lindbergh_pads.DEFAULT_PLAYERS,
            "pads": rows, "caption": caption}


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


_DIRS = ("BUTTON_UP", "BUTTON_DOWN", "BUTTON_LEFT", "BUTTON_RIGHT")
_GENERIC_PAD_LABELS = {"BUTTON_START": "Start", "COIN": "Coin", "BUTTON_SERVICE": "Service",
                       "BUTTON_UP": "Up", "BUTTON_DOWN": "Down", "BUTTON_LEFT": "Left",
                       "BUTTON_RIGHT": "Right"}


def _generic_pad_label(ctrl: str) -> str:
    return _GENERIC_PAD_LABELS.get(ctrl) or ctrl.replace("BUTTON_", "Button ").replace("_", " ").title()


def _analog_functions(profile) -> list:
    """Slot-agnostic analog functions [{fn:"ANALOG_<i>", label, p1, p2}]. Shared source lives in
    lindbergh_pads (also drives the handheld launch auto-map), so the docked editor and the launch
    CLI can never disagree on the channel layout."""
    return lindbergh_pads.analog_functions(profile)


def _control_kind(key: str) -> str:
    """Capture/UX kind from a control key — handles both slot-agnostic BUTTON_* and explicit
    PLAYER_<n>_* keys: an analog axis, a digital direction (D-pad / stick), or a plain button."""
    if key.startswith("ANALOG_"):
        return "analog"
    if key.endswith(_DIRS):
        return "direction"
    return "button"


def _two_human(profile) -> bool:
    """Whether JVS PLAYER_2 is a SECOND HUMAN (so the slot-agnostic, one-pad-per-player model fits),
    versus a single human whose controls merely span both JVS slots. A 2-human game is a symmetric
    versus layout (P1 and P2 hold the SAME digital control set, e.g. VF5) or has a Player-2 analog
    channel (a second wheel, e.g. Hummer). Single-driver games (Initial D / Outrun / Race TV /
    R-Tuned) stash the gear shifter / boost on PLAYER_2 of ONE driver -> 1-human, and those PLAYER_2
    controls must NOT be blanked. No profile -> True (the generic symmetric assumption = today's behavior)."""
    if not (profile and profile.get("rows")):
        return True
    p1, p2, p2_analog = set(), set(), False
    for r in profile["rows"]:
        k = r.get("key", "")
        if k.startswith("PLAYER_1_"):
            p1.add(k[len("PLAYER_1_"):])
        elif k.startswith("PLAYER_2_"):
            p2.add(k[len("PLAYER_2_"):])
        elif k.startswith("ANALOGUE_") and (r.get("label") or "").strip().endswith(" Player 2"):
            p2_analog = True
    return p2_analog or (bool(p1) and p1 == p2)


def _heal_sidecar(gd, profile) -> None:
    """Backfill/refresh a sidecar's single_player shape from the CURRENT profile and migrate legacy
    slot-agnostic digital keys to PLAYER_1_* for a now-single-human game. Without this:
      - a pre-rework v1 sidecar (no flag) for a single-driver game takes the 2-human launch path and
        blanks the PLAYER_2 gear shifter (the exact HIGH bug this rework fixes);
      - a profile reclassification could leave a stale flag so the page and the game disagree;
      - existing digital binds (and the gear) would be orphaned when the keyspace switches.
    Saves only when something changed; no-op when there is no sidecar. Called on page entry."""
    data = lindbergh_pads.load(gd)
    if not data:
        return
    one = not _two_human(profile)
    changed = data.get("single_player") != one
    data["single_player"] = one
    pfx = "PLAYER_1_"
    for m in (data.get("pads") or {}).values():
        if one:                               # -> single-human: slot-agnostic BUTTON_* -> PLAYER_1_*
            for k in [k for k in m if k in lindbergh_pads.CONTROLS]:
                m[f"{pfx}{k}"] = m.pop(k)
                changed = True
        else:                                 # -> 2-human: PLAYER_1_<ctrl> -> slot-agnostic <ctrl>
            for k in [k for k in m if k.startswith(pfx) and k[len(pfx):] in lindbergh_pads.CONTROLS]:
                m[k[len(pfx):]] = m.pop(k)
                changed = True
            # a slot-agnostic 2-human pad cannot own a P2 slot -> drop any orphan single-human gear keys
            for k in [k for k in m
                      if k.startswith("PLAYER_2_") and k[len("PLAYER_2_"):] in lindbergh_pads.CONTROLS]:
                m.pop(k)
                changed = True
    if changed:
        lindbergh_pads.save(gd, data)
        staterev.bump("config")             # invariant hygiene: a config write bumps the page cache


def _collapse_func(l1: str, l2: str) -> str:
    """Merge a control's P1/P2 function labels for the slot-agnostic (2-human) view: drop a pure
    player-number difference (VF5 'Player 1 Punch'/'Player 2 Punch' -> 'Punch', 'Coin 1'/'Coin 2'
    -> 'Coin'); keep both when the function genuinely differs ('ViewChange / Boost')."""
    def norm(s):
        s = re.sub(r"\bPlayer [12]\b", "", s or "")
        s = re.sub(r"\b[12]\b", "", s)
        return re.sub(r"\s+", " ", s).strip()
    if l1 and l2:
        return norm(l1) if norm(l1) == norm(l2) else f"{l1} / {l2}"
    return l1 or l2 or ""


def _pad_digital(profile) -> list:
    """SLOT-AGNOSTIC digital controls a 2-human game uses, in CONTROLS order, with per-game function
    labels [(key, label), ...]. Profile keys are PLAYER_<n>_<ctrl>; we union P1+P2 and label each
    "<JVS control> (<function>)". Falls back to the full CONTROLS list / generic labels with no profile."""
    rows = (profile or {}).get("rows") or []
    p1lab, p2lab, used = {}, {}, set()
    for r in rows:
        key = r.get("key", "")
        for pfx, store in (("PLAYER_1_", p1lab), ("PLAYER_2_", p2lab)):
            if key.startswith(pfx):
                ctrl = key[len(pfx):]
                if ctrl in lindbergh_pads.CONTROLS:
                    store[ctrl] = (r.get("label") or "").strip()
                    used.add(ctrl)
                break
    if not used:                              # no profile / no digital rows -> full generic set
        return [(c, _generic_pad_label(c)) for c in lindbergh_pads.CONTROLS]
    out = []
    for ctrl in lindbergh_pads.CONTROLS:      # keep CONTROLS order, only the used ones
        if ctrl not in used:
            continue
        func = _collapse_func(p1lab.get(ctrl), p2lab.get(ctrl))
        base = _generic_pad_label(ctrl)
        # only show the function parenthetical when it adds info beyond the generic JVS label (so a
        # digit-stripped "Button" against base "Button 1" isn't shown as a redundant "Button 1 (Button)")
        redundant = func and func.lower().replace(" ", "") in base.lower().replace(" ", "")
        out.append((ctrl, f"{base} ({func})" if func and func != base and not redundant else base))
    return out


def _one_human_controls(profile) -> list:
    """For a SINGLE-human game: the distinct PLAYER_<n>_<ctrl> digital controls it actually uses
    (both JVS slots belong to the one driver), each with the profile's function label, in
    (slot, CONTROLS) order. Keys are the REAL ini keys so the gear shifter on PLAYER_2 binds
    directly (not collapsed onto PLAYER_1) and is never blanked."""
    present = {r.get("key", ""): (r.get("label") or "").strip() for r in (profile.get("rows") or [])}
    out = []
    for slot in (1, 2):
        for ctrl in lindbergh_pads.CONTROLS:
            key = f"PLAYER_{slot}_{ctrl}"
            if key in present:
                out.append((key, present[key] or _generic_pad_label(ctrl)))
    return out


def _pad_controls(profile) -> list:
    """(key, label) digital rows for the page: slot-agnostic for a 2-human game, or the real
    per-slot PLAYER_<n> keys for a single-human game (whose controls span both JVS slots)."""
    return _pad_digital(profile) if _two_human(profile) else _one_human_controls(profile)


def _pad_sections(profile) -> dict:
    """Ordered display groups for the pad binder (buttons / dpad / analog / system). Works for both
    slot-agnostic (BUTTON_*) and explicit (PLAYER_<n>_*) keys via suffix matching."""
    keys = [k for k, _ in _pad_controls(profile)]

    def group(pred):
        return [k for k in keys if pred(k)]

    groups = [
        ("buttons", group(lambda k: any(k.endswith(f"BUTTON_{i}") for i in range(1, 9)))),
        ("dpad", group(lambda k: _control_kind(k) == "direction")),
        ("analog", [f["fn"] for f in _analog_functions(profile)]),
        ("system", group(lambda k: k.endswith(("BUTTON_START", "COIN", "BUTTON_SERVICE")))),
    ]
    return {name: ks for name, ks in groups if ks}


def _pad_rows(gd, tag, profile="__unset__") -> dict:
    """The control rows for one pad's map, keyed by control, for the pad binder. Digital controls +
    per-game function labels; analog functions carry axis/kind so the page binds them by MOVING.
    kind drives the capture mode + status text: button / direction / analog. For a single-human game
    the digital keys are the real PLAYER_<n>_<ctrl> (one pad drives both JVS slots)."""
    if profile == "__unset__":
        profile = _profile_of(gd)
    pad = (lindbergh_pads.load(gd).get("pads") or {}).get(tag, {})
    rows = {}
    for key, label in _pad_controls(profile):
        code = pad.get(key)
        rows[key] = {"key": key, "label": label, "axis": False, "kind": _control_kind(key),
                     "display": code if code else "— unbound", "warn": not code}
    for fn in _analog_functions(profile):
        code = pad.get(fn["fn"])
        rows[fn["fn"]] = {"key": fn["fn"], "label": fn["label"], "axis": True, "kind": "analog",
                          "display": code if code else "— unbound", "warn": not code}
    return rows


@method("lindbergh.pad_load", slow=True)
def _pad_load(params):
    gd = _gamedir(params["titleid"])
    tag = params["tag"]
    profile = _profile_of(gd)
    _heal_sidecar(gd, profile)              # migrate a pre-rework / stale sidecar before reading it
    name = next((c["label"] for c in lindbergh_pads.connected_pads() if c["tag"] == tag), tag)
    return {"titleid": params["titleid"], "tag": tag, "pad_name": name,
            "caption": f"Map {name}'s controls for this game (saved to this controller's profile). "
                       "Press A on a control, then actuate it on THIS controller.",
            "sections": _pad_sections(profile), "rows": _pad_rows(gd, tag, profile)}


@method("lindbergh.pad_bind", slow=True)
def _pad_bind(params):
    gd = _gamedir(params["titleid"])
    tag, control, label = params["tag"], params["control"], params.get("label", params["control"])
    # Capture mode follows the control kind (authoritative server-side, can't desync from the page):
    # an analog function moves an axis (--axis, bare token); a direction is the D-pad OR a stick push
    # (--direction, _MIN/_MAX); everything else is a button press. _control_kind handles both the
    # slot-agnostic BUTTON_* keys and the explicit PLAYER_<n>_* keys used by single-human games.
    kind = _control_kind(control)
    argv = [sys.executable, str(CAPTURE), "--timeout", "10"]
    if kind == "analog":
        argv.append("--axis")
    elif kind == "direction":
        argv.append("--direction")
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
    profile = _profile_of(gd)
    data = lindbergh_pads.load(gd)
    data.setdefault("pads", {}).setdefault(tag, {})[control] = name
    if tag not in (data.get("priority") or []):
        data.setdefault("priority", []).append(tag)     # first map => add to the order
    # Persist the game shape for the profile-less launch materializer: a single-human game (gears on
    # PLAYER_2 of one driver) is materialized onto BOTH JVS slots from the one pad and never blanked.
    data["single_player"] = not _two_human(profile)
    if control.startswith("ANALOG_"):
        # fn->channel layout so the materializer knows which global ANALOGUE_<n> each function drives.
        data["analog"] = [{"fn": f["fn"], "p1": f["p1"], "p2": f["p2"]}
                          for f in _analog_functions(profile)]
    lindbergh_pads.save(gd, data)
    staterev.bump("config")
    is_analog = control.startswith("ANALOG_")
    row = _pad_rows(gd, tag).get(control) or {"key": control, "label": label, "axis": is_analog,
                                              "kind": "analog" if is_analog else "button",
                                              "display": name, "warn": False}
    return {"message": f"{label} -> {name}.", "warn": False, "rows": {control: row}}


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
    row = _pad_rows(gd, tag).get(control) or {"key": control, "label": label, "axis": False,
                                              "kind": "button", "display": "— unbound", "warn": True}
    return {"row": row, "message": f"{label} unbound."}


# An analog trigger captured as a digital button used to be stored as the loader's
# ANALOGUE_TO_DIGITAL_MAX token (..._ABS_<axis>_MAX). That token's release to 0 is gated behind
# value>=midpoint in the loader (evdevInput.c:1366-1382), so a trigger snapping back to rest (0)
# never clears and the button STICKS on. The bare axis token (..._ABS_<axis>, no suffix) binds the
# loader's ungated digital path (evdevInput.c:1341-1344) and releases cleanly. On load we convert any
# stuck digital-button binding to the bare token so existing inis self-heal (Save to apply). The match
# is RESTRICTED to the trigger axes (Z=LT, RZ=RT, and the wheel-pedal gas/brake/throttle/rudder codes),
# which rest at an EXTREME; player-generic; ANALOGUE_n channels are left alone. Centered axes are
# deliberately NOT stripped: a hat (ABS_HAT*) and a thumbstick (ABS_X/Y/RX/RY) rest at their MIDPOINT,
# so their _MIN/_MAX release correctly (no stick bug) and a D-pad / stick-direction binding legitimately
# uses _MIN/_MAX -> stripping its _MAX would silently break "down"/"right".
_STUCK_TRIGGER_RE = re.compile(
    r'(?m)^([ \t]*(?!ANALOGUE_)[A-Z0-9_]+[ \t]*=[ \t]*")(.+_ABS_(?:RZ|Z|GAS|BRAKE|THROTTLE|RUDDER))_MAX(")')


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
    raw = cfgutil.read_text(ini) or ""
    text, migrated = _migrate_stuck_triggers(raw)
    _buf.update(titleid=titleid, ini=ini, text=text, disk=raw,
                dirty=(text != raw), profile=_profile_of(gd), migrated=migrated)


def _recompute_dirty() -> None:
    _buf["dirty"] = (_buf["text"] != _buf.get("disk", ""))


@method("lindbergh.get", slow=True)
def _get(params):
    titleid = params.get("titleid", "")
    if not titleid:
        raise RpcError("EINVAL", "lindbergh.get needs a titleid")
    _load_buffer(titleid)   # page entry = fresh from disk (no stale cross-page/-game buffer)
    groups = _build_groups(_buf["text"], _buf.get("profile"))
    note = "Edit, then Save to write the game's lindbergh.ini."
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
        _recompute_dirty()
        return {"key": key, "value": f"{m.group(1)}x{m.group(2)}", "dirty": _buf["dirty"]}

    raw_cur = cfgutil.ini_read(_buf["text"], item["section"], key) or ""
    write = cfgutil.compute_write(item, value, raw_cur)
    nt = cfgutil.ini_replace(_buf["text"], item["section"], key, write)
    if nt is None:
        raise RpcError("ENOKEY", f"{key!r} not present in [{item['section']}]")
    _buf["text"] = nt
    _recompute_dirty()
    back = cfgutil.ini_read(nt, item["section"], key) or ""
    if item["type"] == "bool":
        return {"key": key, "value": back.strip().lower() in cfgutil._TRUE, "dirty": _buf["dirty"]}
    _, v = cfgutil._enum_get(item, back.strip())
    return {"key": key, "value": v, "dirty": _buf["dirty"]}


@method("lindbergh.save")
def _save(params):
    titleid = params.get("titleid", "")
    if _buf.get("ini") is None or (titleid and titleid != _buf["titleid"]):
        raise RpcError("EINVAL", "no edits loaded for this game — reopen the page")
    if _buf["dirty"]:
        cfgutil.ensure_bak(_buf["ini"])
        cfgutil.atomic_write(_buf["ini"], _buf["text"])
        _buf["disk"] = _buf["text"]
        _buf["dirty"] = False
    return {"message": "Saved. Applies the next time you launch this game."}


@method("lindbergh.cancel", slow=True)
def _cancel(params):
    titleid = params.get("titleid", "")
    if _buf.get("ini") is None or (titleid and titleid != _buf["titleid"]):
        raise RpcError("EINVAL", "no edits loaded for this game — reopen the page")
    fresh = cfgutil.read_text(_buf["ini"]) or ""
    _buf.update(text=fresh, disk=fresh, dirty=False)
    return {"buffered": True, "dirty": False,
            "groups": _build_groups(_buf["text"], _buf.get("profile")),
            "message": "Reverted to what's saved on disk."}


# ── input binder (the Input-mapping section, GuiMadPageLindbergh) ──────────────
CAPTURE = LAUNCHERS / "lib" / "lindbergh_capture.py"

# The loader-bindable [EVDEV] keys (mirror of tools/tp2lindbergh.py VALID_KEYS). Used to filter the
# ini's present keys down to real controls (excludes ANALOGUE_DEADZONE_*, geometry, etc.).
_VALID_KEYS = (
    {f"ANALOGUE_{i}" for i in range(1, 9)}
    | {f"PLAYER_{p}_BUTTON_{b}" for p in (1, 2)
       for b in [str(n) for n in range(1, 9)] + ["UP", "DOWN", "LEFT", "RIGHT", "SERVICE", "START"]}
    | {f"PLAYER_{p}_COIN" for p in (1, 2)}
    | {"TEST_BUTTON"}
)


def _clean_tok(raw) -> str:
    """The device token from an [EVDEV] value: strip surrounding quotes and any inline '# ...' comment.
    The loader's own parser does NOT strip inline comments (they silently break a binding), so MAD
    shows the clean token and a re-bind rewrites the value without the comment."""
    s = (raw or "").strip()
    if s.startswith('"'):
        m = re.match(r'"([^"]*)"', s)
        return m.group(1) if m else s.strip('"')
    return s.split("#", 1)[0].strip().strip('"')


def _evdev_present(text) -> list:
    """The bindable control keys actually present in this game's [EVDEV], in file order. This is the
    per-game control SET shown by the binder: the ini is authoritative, labels come from the profile."""
    span = cfgutil._ini_span(text, "EVDEV")
    if not span:
        return []
    seen: set = set()
    out: list = []
    for m in re.finditer(r"(?m)^[ \t]*([A-Z0-9_]+)[ \t]*=", text[span[0]:span[1]]):
        k = m.group(1)
        if k in _VALID_KEYS and k not in seen:
            seen.add(k)
            out.append(k)
    return out


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


# friendly fallback labels for keys a profile doesn't name (built from the generic set incl. analogs).
_GENERIC_LABELS = {r["key"]: r["label"] for r in _generic_rows(False)}


def _binder_row(text, key, label, axis) -> dict:
    tok = _clean_tok(cfgutil.ini_read(text, "EVDEV", key))
    return {"key": key, "label": label, "axis": bool(axis),
            "display": tok if tok else "— unbound", "warn": not tok}


def _binder_data(text, profile, gun) -> tuple[dict, dict]:
    # The control SET is whatever this game's ini actually binds (authoritative, loader-safe); the
    # friendly labels come from our profile (data/lindbergh-profiles.json), then the generic fallback.
    prof_labels = {r["key"]: r["label"]
                   for r in (profile["rows"] if (profile and profile.get("rows")) else [])}
    present = _evdev_present(text)
    if present:
        rows = [{"key": k, "label": prof_labels.get(k) or _GENERIC_LABELS.get(k, k),
                 "axis": k.startswith("ANALOGUE_")} for k in present]
    else:
        # no [EVDEV] section yet (e.g. a fresh game): fall back to the profile rows, else the generic set.
        rows = (list(profile["rows"]) if (profile and profile.get("rows")) else _generic_rows(gun))
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
    _recompute_dirty()
    return {"message": f"{label} → {token}.  Save to apply.", "warn": False, "dirty": _buf["dirty"],
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
    _recompute_dirty()
    return {"row": _binder_row(_buf["text"], key, label, bool(params.get("axis"))),
            "message": f"{label} unbound. Save to apply.", "dirty": _buf["dirty"]}


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


# ── lindbergh_hhinput.* — On-the-go per-game HANDHELD Deck-pad input (dropdown editor) ──────────────
# A per-game editor for the handheld Deck-pad map (the override lindbergh_pads.load_handheld overlays
# on DEFAULT_DECK_MAP when undocked). DROPDOWNS, not the docked live-capture page: the Deck's built-in
# pad is not reliably capturable at config time (same reason the RA-handheld + Daphne handheld editors
# use dropdowns). Reached via `settings_pergame` under On-the-go, so it reuses GuiMadPageEmuSettings +
# the pergame browser verbatim (mirrors the WiiU cemures pattern) -- no fork rebuild. Docked cabinet
# binds (`pads`/`priority`) are never touched; this writes only the `handheld` slice, applied
# transiently at an undocked launch and reverted on game-end by the existing [EVDEV] rail.
_DECK_EVDEV_OPTS = [
    ("A", "BTN_SOUTH"), ("B", "BTN_EAST"), ("X", "BTN_NORTH"), ("Y", "BTN_WEST"),
    ("L1", "BTN_TL"), ("R1", "BTN_TR"), ("L2 (trigger)", "ABS_Z"), ("R2 (trigger)", "ABS_RZ"),
    ("L3", "BTN_THUMBL"), ("R3", "BTN_THUMBR"), ("Start", "BTN_START"), ("Select", "BTN_SELECT"),
    ("D-pad Up", "ABS_HAT0Y_MIN"), ("D-pad Down", "ABS_HAT0Y_MAX"),
    ("D-pad Left", "ABS_HAT0X_MIN"), ("D-pad Right", "ABS_HAT0X_MAX"),
]
_DECK_EVDEV_LABELS = [l for l, _c in _DECK_EVDEV_OPTS]
_DECK_EVDEV_CODES = [c for _l, c in _DECK_EVDEV_OPTS]
_DECK_CODE_LABELS = {c: l for l, c in _DECK_EVDEV_OPTS}
# Deck-pad ANALOG axes offered for a racing/flight game's analog functions (wheel / gas / brake /
# throttle). Value 0 = "Default (auto)" = the label-matched auto-map (lindbergh_pads.auto_axis).
_DECK_AXIS_OPTS = [("L-stick X", "ABS_X"), ("L-stick Y", "ABS_Y"),
                   ("R-stick X", "ABS_RX"), ("R-stick Y", "ABS_RY"),
                   ("L trigger", "ABS_Z"), ("R trigger", "ABS_RZ")]
_DECK_AXIS_LABELS = [l for l, _c in _DECK_AXIS_OPTS]
_DECK_AXIS_CODES = [c for _l, c in _DECK_AXIS_OPTS]
_DECK_AXIS_CODE_LABELS = {c: l for l, c in _DECK_AXIS_OPTS}


def _hh_controls(profile) -> list:
    """(slot-agnostic control, label) pairs editable HANDHELD. The Deck default binds PLAYER_1 only,
    so these are the controls the game reads on PLAYER_1: a 2-human game -> the shared slot-agnostic
    set (_pad_digital); a single-human game -> its PLAYER_1 controls (its PLAYER_2 controls are the
    cabinet's second station, not reachable from the single Deck pad, so they are not offered)."""
    if _two_human(profile):
        return _pad_digital(profile)
    present = {r.get("key", ""): (r.get("label") or "").strip() for r in (profile.get("rows") or [])}
    out = []
    for ctrl in lindbergh_pads.CONTROLS:
        key = f"PLAYER_1_{ctrl}"
        if key in present:
            out.append((ctrl, present[key] or _generic_pad_label(ctrl)))
    return out


@method("lindbergh_hhinput.games", slow=True)   # CRCs each ELF via _pad_eligible -> off-thread
def _hhinput_games(params):
    rows = []
    for g in _games():
        tid = g["titleid"]
        if not _pad_eligible(tid):              # gun / profile-less games have no Deck-pad remap
            continue
        n = len(lindbergh_pads.load_handheld(_gamedir(tid)))
        rows.append({"titleid": tid, "name": g["name"], "stem": g["stem"],
                     "summary": f"Handheld: {n} remapped" if n else "Deck defaults"})
    return {"games": rows, "system": "lindbergh"}


@method("lindbergh_hhinput.get", slow=True)
def _hhinput_get(params):
    gd = _gamedir(params.get("titleid", ""))
    profile = _profile_of(gd)
    override = lindbergh_pads.load_handheld(gd)
    settings = []
    for control, label in _hh_controls(profile):
        default_code = lindbergh_pads.DEFAULT_DECK_MAP.get(control, "")
        dlabel = _DECK_CODE_LABELS.get(default_code, default_code)
        opts = [f"Default ({dlabel})" if dlabel else "Default"] + _DECK_EVDEV_LABELS
        cur = override.get(control)
        val = (1 + _DECK_EVDEV_CODES.index(cur)) if cur in _DECK_EVDEV_CODES else 0
        settings.append({"key": control, "label": label, "type": "enum", "options": opts, "value": val})
    note = ("Which Deck control drives each button for this game, handheld only. 'Default' uses the "
            "Deck's built-in layout. The docked cabinet binds are untouched; applied only when undocked.")
    groups = [{"title": "Deck buttons", "note": "", "settings": settings}]
    # Racing / flight titles: a "Deck analog" group -- one axis picker per analog function. Value 0 =
    # "Default (auto)" = the label-matched auto-map applied at launch (wheel->L-stick, pedals->triggers).
    ana_fns = _analog_functions(profile)
    if ana_fns:
        ana_ovr = lindbergh_pads.load_handheld_analog(gd)
        ana_settings = []
        for fn in ana_fns:
            auto = lindbergh_pads.auto_axis(fn["label"])
            auto_lbl = _DECK_AXIS_CODE_LABELS.get(auto, "unbound") if auto else "unbound"
            cur = ana_ovr.get(fn["fn"])
            val = (1 + _DECK_AXIS_CODES.index(cur)) if cur in _DECK_AXIS_CODES else 0
            ana_settings.append({"key": fn["fn"], "label": fn["label"], "type": "enum",
                                 "options": [f"Default ({auto_lbl})"] + _DECK_AXIS_LABELS, "value": val})
        groups.append({"title": "Deck analog", "note": "", "settings": ana_settings})
    return {"exists": True, "running": False, "note": note, "groups": groups}


def _hhinput_set_analog(gd, profile, control, value):
    """Persist one analog function's Deck-axis override; index 0 (Default/auto) or the auto-mapped
    axis clears it (kept sparse -- launch re-derives the auto-map)."""
    fn = next((f for f in _analog_functions(profile) if f["fn"] == control), None)
    if fn is None:
        raise RpcError("EINVAL", f"unknown analog function {control!r}")
    try:
        idx = int(float(value))
    except (TypeError, ValueError):
        raise RpcError("EINVAL", "bad option index")
    ovr = lindbergh_pads.load_handheld_analog(gd)
    if idx <= 0:
        ovr.pop(control, None)                                   # Default (auto) -> clear
    elif 1 <= idx <= len(_DECK_AXIS_CODES):
        code = _DECK_AXIS_CODES[idx - 1]
        if code == lindbergh_pads.auto_axis(fn["label"]):
            ovr.pop(control, None)                               # equals the auto-default -> sparse
        else:
            ovr[control] = code
    else:
        raise RpcError("EINVAL", "option index out of range")
    lindbergh_pads.save_handheld_analog(gd, ovr)
    staterev.bump("config")
    return {"key": control, "value": idx}


@method("lindbergh_hhinput.set", slow=True)
def _hhinput_set(params):
    gd = _gamedir(params.get("titleid", ""))
    control = params.get("key", "")
    profile = _profile_of(gd)
    if control.startswith("ANALOG_"):                           # a "Deck analog" axis picker
        return _hhinput_set_analog(gd, profile, control, params.get("value"))
    if control not in {c for c, _l in _hh_controls(profile)}:
        raise RpcError("EINVAL", f"unknown control {control!r}")
    try:
        idx = int(float(params.get("value")))
    except (TypeError, ValueError):
        raise RpcError("EINVAL", "bad option index")
    override = lindbergh_pads.load_handheld(gd)
    if idx <= 0:
        override.pop(control, None)                              # Default -> clear the override
    elif 1 <= idx <= len(_DECK_EVDEV_CODES):
        code = _DECK_EVDEV_CODES[idx - 1]
        if code == lindbergh_pads.DEFAULT_DECK_MAP.get(control):
            override.pop(control, None)                          # equals the default -> keep sparse
        else:
            override[control] = code
    else:
        raise RpcError("EINVAL", "option index out of range")
    lindbergh_pads.save_handheld(gd, override)
    staterev.bump("config")
    return {"key": control, "value": idx}


# ── lindbergh_hhres.* — On-the-go per-game HANDHELD resolution (transient, battery) ─────────────────
# A per-game handheld resolution + boost-render override, applied transiently when undocked by
# lindbergh_pads.apply_handheld_settings (per-key marker rail) and reverted on game-end; docked ini
# untouched. Reached via `settings_pergame` under the game-first Per-game menu -> GuiMadPageEmuSettings
# (no rebuild). Resolution rungs are aspect-correct from the game's native res.
_HHBOOST_TOKENS = ["", "off", "on"]             # index 0 = Inherit


def _hhres_offered(gd) -> tuple:
    """(docked WIDTH, docked HEIGHT, [rung tokens <= docked height]) for a game -- the rungs the editor
    offers and the set maps its indices through. Rungs come from the DOCKED resolution ('where
    possible'), so 1080p appears only where the game runs at >= 1080p docked."""
    text = cfgutil.read_text(lindbergh_pads.ini_of(gd)) or ""
    cw = cfgutil.ini_read(text, "Display", "WIDTH")
    ch = cfgutil.ini_read(text, "Display", "HEIGHT")
    return cw, ch, [str(r) for r in lindbergh_pads.offered_rungs(ch)]


@method("lindbergh_hhres.get", slow=True)
def _hhres_get(params):
    gd = _gamedir(params.get("titleid", ""))
    cw, ch, toks = _hhres_offered(gd)
    ovr = lindbergh_pads.load_handheld_settings(gd)
    opts = ["Inherit (docked)"]
    for tok in toks:
        wxh = lindbergh_pads.res_wxh(cw, ch, int(tok))
        opts.append(f"{tok}p ({wxh[0]}x{wxh[1]})" if wxh else f"{tok}p")
    cur = ovr.get("res", "")
    settings = [{"key": "res", "label": "Handheld resolution", "type": "enum",
                 "value": (1 + toks.index(cur)) if cur in toks else 0, "options": opts}]
    # The boost row only appears when the game's ini actually has the key (else the toggle is a no-op).
    text = cfgutil.read_text(lindbergh_pads.ini_of(gd)) or ""
    if cfgutil.ini_read(text, "Display", "BOOST_RENDER_RES") is not None:
        b_val = _HHBOOST_TOKENS.index(ovr["boost"]) if ovr.get("boost") in _HHBOOST_TOKENS else 0
        settings.append({"key": "boost", "label": "Boost render resolution", "type": "enum",
                         "value": b_val, "options": ["Inherit", "Off (save battery)", "On"]})
    note = ("Set the render resolution for this game when handheld -- lower to save battery + gain FPS, "
            "or 1080p where the game supports it. 'Inherit' keeps the docked resolution. Applied only "
            "undocked; docked play unchanged.")
    return {"exists": True, "running": False, "note": note,
            "groups": [{"title": "Handheld resolution", "note": "", "settings": settings}]}


@method("lindbergh_hhres.set", slow=True)
def _hhres_set(params):
    gd = _gamedir(params.get("titleid", ""))
    key = params.get("key", "")
    try:
        idx = int(float(params.get("value")))
    except (TypeError, ValueError):
        raise RpcError("EINVAL", "bad option index")
    ovr = lindbergh_pads.load_handheld_settings(gd)
    if key == "res":
        toks = _hhres_offered(gd)[2]                # the same dynamic rung list .get built
        if 1 <= idx <= len(toks):
            ovr["res"] = toks[idx - 1]
        else:
            ovr.pop("res", None)                    # index 0 (Inherit) or out-of-range -> clear
    elif key == "boost":
        tok = _HHBOOST_TOKENS[idx] if 0 <= idx < len(_HHBOOST_TOKENS) else ""
        if tok:
            ovr["boost"] = tok
        else:
            ovr.pop("boost", None)
    else:
        raise RpcError("EINVAL", f"unknown key {key!r}")
    lindbergh_pads.save_handheld_settings(gd, ovr)
    staterev.bump("config")
    return {"key": key, "value": idx}


@method("lindbergh_hhmenu.games", slow=True)        # the game-first Per-game picker (Settings + Input)
def _hhmenu_games(params):
    rows = []
    for g in _games():
        tid = g["titleid"]
        if _is_gun(tid):                            # lightgun titles are useless handheld -> drop
            continue
        row = {"titleid": tid, "name": g["name"], "stem": g["stem"], "summary": "Per-game handheld"}
        if not _pad_eligible(tid):                  # profile-less: resolution applies, input does not
            row["hide"] = ["input"]
        rows.append(row)
    return {"games": rows, "system": "lindbergh"}
