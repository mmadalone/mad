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
    """Where Steam CREATES a new prefix: the home library. Callers that ask "where would
    this prefix go" want this; callers that ask "where IS it" want compatdata_dir()."""
    return home() / ".local/share/Steam/steamapps/compatdata"


_LIBRARY_PATH_RE = re.compile(r'"path"\s+"([^"]+)"')

# One parse per (paths, mtimes) signature, same convention as _SHORTCUTS_CACHE below:
# _payload_allowed() consults the roots once per candidate AND once per peer, so a
# re-parse per call would read libraryfolders.vdf hundreds of times for one asset page.
# The signature carries the paths themselves, so a test that repoints home() misses the
# cache instead of inheriting another test's answer. The (sig, value) pair lives in ONE
# dict slot as ONE immutable tuple: madsrv handlers run on a thread pool, and a
# two-statement publish would let a reader pair the new sig with the old (or initial
# empty) value - review 2026-08-04 demonstrated exactly that race.
_LIBRARY_CACHE: dict = {"entry": (None, [])}


def _libraryfolders_paths() -> list:
    return [home() / ".local/share/Steam/steamapps/libraryfolders.vdf",
            home() / ".steam/steam/steamapps/libraryfolders.vdf"]


def library_roots() -> list:
    """Every Steam library root, from Steam's own libraryfolders.vdf (both root
    spellings) plus the two home roots — the authoritative list, parsed at call time so
    an added/removed SD library changes the answer. A library that is not mounted right
    now still appears; callers filter on existence. Deduped by realpath, home first
    (the default install target, so it wins an ambiguous lookup)."""
    paths = _libraryfolders_paths()
    sig = []
    for p in paths:
        try:
            sig.append((str(p), p.stat().st_mtime_ns))
        except OSError:
            sig.append((str(p), None))
    sig = tuple(sig)
    cached_sig, cached_val = _LIBRARY_CACHE["entry"]     # ONE read: never a torn pair
    if sig == cached_sig:
        return list(cached_val)
    seen, out = set(), []
    for cand in (home() / ".local/share/Steam", home() / ".steam/steam"):
        rp = os.path.realpath(str(cand))
        if rp not in seen:
            seen.add(rp)
            out.append(Path(rp))
    for lf in paths:
        try:
            text = lf.read_text(errors="replace")
        except OSError:
            continue
        for p in _LIBRARY_PATH_RE.findall(text):
            rp = os.path.realpath(os.path.expanduser(p))
            if rp not in seen:
                seen.add(rp)
                out.append(Path(rp))
    _LIBRARY_CACHE["entry"] = (sig, tuple(out))          # ONE atomic store
    return out


def compatdata_roots() -> list:
    """Every EXISTING <library>/steamapps/compatdata. The home root is always included
    (even when absent) so the guards and the "where would it go" answer never depend on
    a prefix having been created yet."""
    seen, out = set(), []
    home_root = compatdata_root()
    seen.add(os.path.realpath(str(home_root)))
    out.append(home_root)
    for lib in library_roots():
        cd = lib / "steamapps" / "compatdata"
        rp = os.path.realpath(str(cd))
        if rp in seen or not os.path.isdir(rp):
            continue
        seen.add(rp)
        out.append(cd)
    return out


def compatdata_dir(appid: int) -> Path:
    """The prefix dir for one appid: the library that ACTUALLY holds it, else the home
    root (where Steam would create it). Steam puts a prefix in whichever library is the
    default install target, which is not always the home one."""
    appid_s = str(int(appid))
    for root in compatdata_roots():
        cand = root / appid_s
        if os.path.isdir(str(cand)):
            return cand
    return compatdata_root() / appid_s


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
# ONE (sig, value) slot, same atomic-publish reasoning as _LIBRARY_CACHE.
_SHORTCUTS_CACHE: dict = {"entry": (None, {})}


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
    cached_sig, cached_val = _SHORTCUTS_CACHE["entry"]   # ONE read: never a torn pair
    if sig == cached_sig:
        return cached_val
    merged: dict = {}
    for p in paths:
        try:
            merged.update(parse_shortcuts(p.read_bytes()))
        except OSError:
            continue
    _SHORTCUTS_CACHE["entry"] = (sig, merged)            # ONE atomic store
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


def _payload_dir(sc: dict):
    """One shortcut's raw payload dir: StartDir, else the exe's parent, realpath'd. None
    when empty, RELATIVE, or not a directory. Relative is refused outright (Steam writes
    "./" for e.g. the Nested Desktop shortcut): realpath would resolve it against the
    CALLING PROCESS's cwd, and a cwd-dependent path must never join the peer set that
    bounds other games' widening."""
    cand = (sc.get("start_dir") or "").strip()
    if not cand:
        exe = sc.get("exe") or ""
        cand = os.path.dirname(exe) if exe else ""
    if not cand:
        return None
    cand = os.path.expanduser(cand)
    if not os.path.isabs(cand):
        return None
    rp = os.path.realpath(cand)
    return rp if os.path.isdir(rp) else None


def _payload_allowed(rp: str) -> bool:
    """The containment every game-dir answer must pass: inside $HOME but not $HOME
    itself, not under ANY library's compatdata, not under a deny root. That keeps repack
    installs like ~/Games/OutRun2006 in, and emulator ROMs / app dirs / other categories'
    trees out. (~/ROMs realpaths to the SD card, outside $HOME, so it is doubly
    excluded.)"""
    h = os.path.realpath(str(home()))
    if rp == h or not rp.startswith(h + os.sep):
        return False
    for croot in compatdata_roots():
        cr = os.path.realpath(str(croot))
        if rp == cr or rp.startswith(cr + os.sep):
            return False
    for d in _DENY_ROOTS:
        droot = os.path.join(h, d)
        if rp == droot or rp.startswith(droot + os.sep):
            return False
    return True


def _ancestors(rp: str, h: str) -> list:
    """[rp, its parent, ...] up to but EXCLUDING $HOME — deepest first."""
    out, cur = [], rp
    while cur.startswith(h + os.sep):
        out.append(cur)
        cur = os.path.dirname(cur)
    return out


def _norm_name(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _names_match(a: str, b: str) -> bool:
    """Normalised folder-name vs shortcut-name match: "Transformers - War for Cybertron"
    == "Transformers: War for Cybertron", "Ultimate.Spider.Man" == "Ultimate Spider-Man".

    EQUALITY ONLY, 3 chars minimum. Both restrictions are review findings (2026-08-04):
    a prefix match let "Metal Slug" widen onto a MetalSlugCollection folder holding
    other games, and let a "Deadpool Mod" shortcut widen onto Deadpool's whole root;
    and normalisation strips non-ASCII, so an unfloored equality let a mostly-CJK title
    collapse to a 1-2 char token that matched an unrelated short folder name. Every
    real match on this Deck (10 of 10 ~/Games games) is exact equality, so the prefix
    form carried only risk. The floor still admits real short titles (Ico, Rez)."""
    return bool(a) and len(a) >= 3 and a == b


_PEERS_CACHE: dict = {"entry": (None, ())}   # ONE (key, value) slot - see _LIBRARY_CACHE


def _peer_payloads() -> list:
    """[(appid, payload_dir)] for every shortcut whose payload passes the guards — the
    input to the container test. The appid rides along so _peer_ceiling can exclude the
    shortcut being resolved from its own evidence.

    The raw payload dirs (a realpath+isdir per shortcut, ~1 ms for the ~45 here) are
    recomputed on EVERY call and form part of the cache key, so the key tracks the
    FILESYSTEM as well as the shortcut data: a payload dir created or deleted after
    first resolution changes the key and re-runs the expensive _payload_allowed pass
    (review 2026-08-04: a data-only key served a stale peer set for the process
    lifetime, and this set feeds the restore containment bound). A test that patches
    nonsteam_shortcuts or home() also misses the key."""
    sc_all = nonsteam_shortcuts()
    resolved = tuple(sorted((aid, _payload_dir(sc) or "")
                            for aid, sc in sc_all.items()))
    key = (os.path.realpath(str(home())), resolved)
    cached_key, cached_val = _PEERS_CACHE["entry"]       # ONE read: never a torn pair
    if key == cached_key:
        return list(cached_val)
    out = [(aid, q) for aid, q in resolved if q and _payload_allowed(q)]
    _PEERS_CACHE["entry"] = (key, tuple(out))            # ONE atomic store
    return out


def _peer_ceiling(chain: list, self_payload: str, self_appid: int) -> str | None:
    """The highest folder on `chain` we may climb to WITHOUT swallowing another
    shortcut's game. None = no other shortcut's payload anywhere on the path, so peers
    say nothing.

    An ancestor becomes a container the moment ANY OTHER shortcut's payload lives in a
    different subtree below it: climbing to that ancestor would put the other game
    inside this game's backup - and inside its RESTORE bound, where a stale backup
    could overwrite it. The limit is the container's child on our own path; deepest
    container wins, so a nested layout (~/Games/Compilation/GameA, .../GameB) is
    bounded at GameA rather than the whole compilation.

    Two deliberate exclusions from the evidence (both review findings, 2026-08-04):
    - OUR OWN payload: it separates nothing from us, and counting it made the bound
      UNSTABLE - adding a second shortcut into the same game re-clamped the first
      one's answer, so an older backup suddenly refused to restore.
    - a peer sitting EXACTLY ON the ancestor (q == a): two shortcuts into the same
      game (one at the root, one at its exe subfolder) must not fence each other out.

    This is a CEILING only, never an answer on its own: "the container's child" is the
    game root for a flat library, but for a launcher tree (one shortcut deep inside
    ~/Games/Heroic) it would be the launcher, and backing up every Heroic game to fix
    one is worse than the under-report we are fixing."""
    peers = [(aid, q) for aid, q in _peer_payloads() if aid != self_appid]
    for a in chain:                                   # deepest first
        mine = None if a == self_payload else self_payload[len(a) + 1:].split(os.sep)[0]
        for _aid, q in peers:
            if q == a or not q.startswith(a + os.sep):
                continue
            if mine is None:                          # we ARE this folder: cannot climb
                return a
            if q[len(a) + 1:].split(os.sep)[0] != mine:
                return os.path.join(a, mine)          # another game's subtree: fence it
    return None


def game_dir(appid: int):
    """The shortcut's external game payload dir as a realpath'd Path, or None.

    Steam's StartDir is the folder holding the EXE, which for a repack is often a
    subfolder of the game (~/Games/Deadpool/Binaries, 0.05 GB, inside a 22 GB game). So
    the payload dir is only the STARTING point: from there we climb to the real game
    root, bounded by two signals both derived from Steam's own data at call time (never
    a curated list of folder names):

      name rule (widens) - the deepest ancestor whose name matches this shortcut's
                  AppName (_names_match). Positive evidence that the folder IS this game,
                  and it needs no peers, so a lone game resolves too.
      peer rule (bounds) - never climb to or above a folder that separates 2+ shortcut
                  payloads (_peer_ceiling), so widening can never swallow another game.

    With no name match the payload dir is returned unchanged: this widens only on
    positive evidence, never on a guess, so the worst case is the under-report we already
    had rather than a backup that writes over a different game. _payload_allowed() gates
    the start AND the final answer."""
    sc = nonsteam_shortcuts().get(int(appid))
    if not sc:
        return None
    rp = _payload_dir(sc)
    if rp is None or not _payload_allowed(rp):
        return None
    h = os.path.realpath(str(home()))
    chain = _ancestors(rp, h)
    if not chain:
        return None

    best = rp
    want = _norm_name(sc.get("name"))
    for a in chain:                                   # deepest first: first hit is deepest
        if _names_match(_norm_name(os.path.basename(a)), want):
            best = a
            break
    ceiling = _peer_ceiling(chain, rp, int(appid))
    if ceiling is not None and best.count(os.sep) < ceiling.count(os.sep):
        best = ceiling                                # a peer's tree starts above here
    if not _payload_allowed(best):
        return None
    return Path(best)


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
