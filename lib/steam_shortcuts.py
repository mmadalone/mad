"""Non-Steam shortcut facts — the single owner of every Steam-side lookup MAD makes.

Everything here is DYNAMIC (parsed from Steam's own files at call time, never a curated
list): the Valve Steam backup tile, the per-game asset groups and the restore guards
must keep working when shortcuts are added, removed or renumbered. Read-only module:
nothing here writes.

Facts owned here:
- the binary shortcuts.vdf parser (moved from steam-collection-gen.py, which now
  delegates): the structural per-block parse that cannot mis-pair appid/appname
  (tests/test_steam_rungameid_pairing.py);
- the rungameid algebra: a non-Steam shortcut's launcher runs
  `steam steam://rungameid/<rgid>` with rgid = (appid & 0xffffffff) << 32 | 0x02000000,
  so is_nonsteam()/appid_of() decode a launcher .sh back to its shortcut;
- the join of the ES-DE steam gamelist to the live shortcuts (nonsteam_games): which
  launchers are non-Steam shortcuts, and whether each is still ALIVE in Steam;
- the Proton prefix (compatdata) and the shortcut's external game dir (game_dir), with
  the deny-roots containment that keeps emulator-launched shortcuts out;
- is_lutris(): a Lutris-launched shortcut has no Proton prefix of its own.

Path providers are plain module functions so tests can monkeypatch them
(tests/test_steam_shortcuts.py), the same convention as the rest of lib/.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

NONSTEAM_LOW32 = 0x02000000


# ---- path providers (test-overridable) --------------------------------------

def home() -> Path:
    return Path.home()


def shortcuts_paths() -> list:
    """Every user's shortcuts.vdf, across both Steam root spellings (~/.steam/steam is
    normally a symlink farm into ~/.local/share/Steam — dedupe by realpath)."""
    seen, out = set(), []
    for root in (".steam/steam", ".local/share/Steam"):
        try:
            hits = sorted((home() / root).glob("userdata/*/config/shortcuts.vdf"))
        except OSError:
            continue
        for p in hits:
            rp = os.path.realpath(str(p))
            if rp not in seen:
                seen.add(rp)
                out.append(p)
    return out


def compatdata_root() -> Path:
    return home() / ".local/share/Steam/steamapps/compatdata"


def compatdata_dir(appid: int) -> Path:
    return compatdata_root() / str(int(appid))


# ---- binary VDF (structural, type-aware) -------------------------------------

def _vdf_cstr(data, pos):
    """Read a NUL-terminated field from a binary VDF blob; return (bytes, next_pos)."""
    end = data.index(b'\x00', pos)
    return data[pos:end], end + 1


def _vdf_parse_map(data, pos):
    """Parse a binary-VDF map body starting at pos (just past the map's own key).

    Returns (entries, pos_after_terminator); entries is a list of
    (key_lowercased_bytes, type_byte, value). Type-aware: int32/int64 are read as
    fixed-width values, NOT scanned for a delimiter -- a shortcut appid legitimately
    contains \x00 and \x08 bytes that a byte-scan would misread as field/map ends.
    Raises ValueError when the data ends before the map's 0x08 terminator or mid
    fixed-width value (a crash-truncated vdf): a silently-partial parse would defeat
    strict callers' whole reason for existing.
    """
    entries = []
    n = len(data)
    while pos < n:
        t = data[pos]
        pos += 1
        if t == 0x08:                                  # end of this map
            return entries, pos
        key, pos = _vdf_cstr(data, pos)
        kl = key.lower()
        if t == 0x00:                                  # nested map (recurse)
            sub, pos = _vdf_parse_map(data, pos)
            entries.append((kl, t, sub))
        elif t == 0x01:                                # string (NUL-terminated)
            val, pos = _vdf_cstr(data, pos)
            entries.append((kl, t, val))
        elif t == 0x02:                                # int32
            if pos + 4 > n:
                raise ValueError("truncated binary VDF: int32 cut short at offset %d" % pos)
            val = int.from_bytes(data[pos:pos + 4], "little", signed=True)
            pos += 4
            entries.append((kl, t, val))
        elif t == 0x07:                                # uint64
            if pos + 8 > n:
                raise ValueError("truncated binary VDF: uint64 cut short at offset %d" % pos)
            val = int.from_bytes(data[pos:pos + 8], "little", signed=False)
            pos += 8
            entries.append((kl, t, val))
        else:
            raise ValueError("unknown binary-VDF type %#x at offset %d" % (t, pos - 1))
    raise ValueError("truncated binary VDF: map not terminated (ends at offset %d)" % pos)


def _unquote_exe(s: str) -> str:
    """A shortcut's Exe field, unquoted with launch options dropped (same shape
    steam-collection-sync.py has always used)."""
    s = (s or "").strip()
    if s.startswith('"'):
        return s[1:].split('"', 1)[0]
    return s.split(" ", 1)[0]


def _unquote_dir(s: str) -> str:
    """A shortcut's StartDir field, surrounding quotes stripped."""
    s = (s or "").strip()
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        s = s[1:-1]
    return s


def parse_shortcuts(data: bytes, strict: bool = False) -> dict:
    """{appid: {"name", "exe", "start_dir"}} from one shortcuts.vdf blob. appid is the
    UNSIGNED low-32 form (the value the rungameid algebra uses; the vdf stores a signed
    int32). Structural per-block parse with case-insensitive keys, so fields can never
    pair across blocks; a malformed blob yields {} — never a wrong pairing.
    strict=True re-raises the parse error instead, for callers that must tell a
    corrupt vdf apart from a genuinely empty one (a {} would silently look empty)."""
    try:
        root, _ = _vdf_parse_map(data, 0)
    except (ValueError, IndexError):
        if strict:
            raise
        return {}
    out: dict = {}
    shortcuts = next((v for k, t, v in root if k == b"shortcuts" and t == 0x00), [])
    for _k, t, block in shortcuts:
        if t != 0x00:
            continue
        appid = name = None
        exe = start_dir = options = ""
        for bk, bt, bv in block:
            if bk == b"appid" and bt == 0x02:
                appid = bv
            elif bk == b"appname" and bt == 0x01:
                name = bv.decode("utf-8", "replace")
            elif bk == b"exe" and bt == 0x01:
                exe = bv.decode("utf-8", "replace")
            elif bk == b"startdir" and bt == 0x01:
                start_dir = bv.decode("utf-8", "replace")
            elif bk == b"launchoptions" and bt == 0x01:
                # The IDENTIFYING half of a flatpak shortcut: "run net.lutris.Lutris
                # lutris:rungameid/<id>" etc. lives here, not in Exe (always
                # /usr/bin/flatpak for those).
                options = bv.decode("utf-8", "replace")
        if appid is None or name is None:
            continue
        # exe = the binary alone (unquoted, args dropped - what game_dir keys off);
        # exe_raw = the FULL field. Steam puts a wrapper's payload in either place:
        # a plain Lutris shortcut carries "run ... lutris:rungameid/N" in LaunchOptions,
        # but one wrapped by a %command% tool (e.g. the lsfg frame-gen script) keeps it
        # as the exe's own arguments - so lutris detection must see both raw strings.
        out[appid & 0xFFFFFFFF] = {"name": name, "exe": _unquote_exe(exe),
                                   "exe_raw": exe.strip(),
                                   "start_dir": _unquote_dir(start_dir),
                                   "options": options.strip()}
    return out


# One parse per (paths, mtimes) signature: the browse/asset/restore paths all call
# nonsteam_shortcuts() repeatedly and the vdf only changes when Steam writes it.
_SHORTCUTS_CACHE: dict = {"sig": None, "value": {}}


def nonsteam_shortcuts() -> dict:
    """{appid: {"name", "exe", "start_dir"}} across every user's shortcuts.vdf (a later
    file wins a duplicate appid). Cached on the files' (path, mtime) signature."""
    paths = shortcuts_paths()
    sig = []
    for p in paths:
        try:
            sig.append((str(p), p.stat().st_mtime_ns))
        except OSError:
            sig.append((str(p), None))
    sig = tuple(sig)
    if sig == _SHORTCUTS_CACHE["sig"]:
        return _SHORTCUTS_CACHE["value"]
    merged: dict = {}
    for p in paths:
        try:
            merged.update(parse_shortcuts(p.read_bytes()))
        except OSError:
            continue
    _SHORTCUTS_CACHE["sig"] = sig
    _SHORTCUTS_CACHE["value"] = merged
    return merged


# ---- rungameid algebra --------------------------------------------------------

def rungameid_of(appid: int) -> int:
    return ((int(appid) & 0xFFFFFFFF) << 32) | NONSTEAM_LOW32


def is_nonsteam(rgid: int) -> bool:
    """A steam://rungameid/<id> that launches a SHORTCUT: high 32 bits = the shortcut
    appid, low 32 = 0x02000000. A Steam-proper game's id is its plain appid (< 2^32)."""
    return rgid >= (1 << 32) and (rgid & 0xFFFFFFFF) == NONSTEAM_LOW32


def appid_of(rgid: int) -> int:
    return rgid >> 32


_RGID_RE = re.compile(r"steam://rungameid/(\d+)")


def launcher_rungameid(sh_path) -> int | None:
    """The rungameid a generated launcher .sh execs, or None (unreadable / no match)."""
    try:
        m = _RGID_RE.search(Path(sh_path).read_text(errors="replace"))
    except OSError:
        return None
    return int(m.group(1)) if m else None


def launcher_appid(stem: str) -> int | None:
    """The shortcut appid the LIVE launcher .sh for this ES-DE steam-gamelist stem
    targets, or None (missing launcher / a Steam-proper launcher)."""
    from . import game_files
    roms = game_files.resolve_rom("steam", stem)
    if not roms:
        return None
    rgid = launcher_rungameid(roms[0])
    if rgid is None or not is_nonsteam(rgid):
        return None
    return appid_of(rgid)


# ---- the ES-DE steam system, joined to the live shortcuts ----------------------

def nonsteam_games() -> dict:
    """{stem: {"appid", "rgid", "alive", "name", "sh"}} for every ES-DE steam-system
    launcher that targets a NON-STEAM shortcut — decoded from each launcher's own
    rungameid, never a curated list, so an added/removed shortcut changes the answer
    at the next call. alive=False = the launcher survives but Steam no longer has the
    shortcut (launcher+media restorable; prefix restore is guarded). Steam-proper
    launchers are excluded by construction (their id is a plain appid)."""
    from . import es_gamelist, game_files
    live = nonsteam_shortcuts()
    out: dict = {}
    try:
        recs = es_gamelist.visible_records("steam")
    except Exception:
        return {}
    for stem_lower, rec in recs.items():
        stem = rec.get("stem") or stem_lower
        roms = game_files.resolve_rom("steam", stem)
        if not roms:
            continue
        rgid = launcher_rungameid(roms[0])
        if rgid is None or not is_nonsteam(rgid):
            continue
        appid = appid_of(rgid)
        out[stem] = {"appid": appid, "rgid": rgid, "alive": appid in live,
                     "name": rec.get("name") or stem, "sh": roms[0]}
    return out


# ---- the shortcut's payload locations ------------------------------------------

# $HOME roots other backup categories own (or that must never ride a game-dir backup).
# game_dir() refuses anything under these even though the gamelist filter should never
# send an emulator-launched shortcut here — detection is dynamic, so belt and braces.
_DENY_ROOTS = ("Applications", "Emulation", "ROMs", "OpenBor", "ES-DE",
               ".local", ".config", ".var", ".steam", "Downloads")


def game_dir(appid: int):
    """The shortcut's external game payload dir (StartDir, else the exe's parent) as a
    realpath'd Path, or None. Accepted ONLY when it exists, realpaths INSIDE $HOME (not
    $HOME itself), and is not under compatdata or a deny root: that keeps repack installs
    like ~/games/OutRun2006 in, and emulator ROMs / app dirs / other categories' trees
    out. (~/ROMs realpaths to the SD card, outside $HOME, so it is doubly excluded.)"""
    sc = nonsteam_shortcuts().get(int(appid))
    if not sc:
        return None
    cand = sc.get("start_dir") or ""
    if not cand:
        exe = sc.get("exe") or ""
        cand = os.path.dirname(exe) if exe else ""
    if not cand:
        return None
    rp = os.path.realpath(os.path.expanduser(cand))
    if not os.path.isdir(rp):
        return None
    h = os.path.realpath(str(home()))
    if rp == h or not rp.startswith(h + os.sep):
        return None
    croot = os.path.realpath(str(compatdata_root()))
    if rp == croot or rp.startswith(croot + os.sep):
        return None
    for d in _DENY_ROOTS:
        droot = os.path.join(h, d)
        if rp == droot or rp.startswith(droot + os.sep):
            return None
    return Path(rp)


_LUTRIS_ID_RE = re.compile(r"lutris:rungameid/(\d+)")


def is_lutris(appid: int) -> bool:
    """A shortcut Steam launches through Lutris - identified by the lutris: URI in the
    exe OR the launch options (a bare flatpak exe is NOT enough: every flatpak shortcut
    - Kodi, Spotify, the mGBA Pokemon ones - has exe /usr/bin/flatpak, and only the
    options say WHICH app runs). Its game data lives in a LUTRIS wine prefix, not
    compatdata - see lib/lutris_games.py."""
    return lutris_game_id(appid) is not None


def lutris_game_id(appid: int):
    """The Lutris pga.db game id a shortcut launches (lutris:rungameid/<id> in the RAW
    exe field or the launch options - Steam stores it in either, see parse_shortcuts),
    or None. This is the join key into lib/lutris_games."""
    sc = nonsteam_shortcuts().get(int(appid))
    if not sc:
        return None
    for field in ("exe_raw", "exe", "options"):
        m = _LUTRIS_ID_RE.search((sc.get(field) or "").lower())
        if m:
            return int(m.group(1))
    return None
