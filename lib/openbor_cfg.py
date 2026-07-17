"""OpenBOR per-game binary settings (Saves/<pak>.cfg) — locate, decode, splice.

The cfg is a raw fwrite of the engine's s_savedata struct: no checksum, dword0
= a version stamp the bundled engine always matches. The control map is
keys[4 players][N slots] int32 LE, but THREE things vary by engine generation
and FILE SIZE ALONE CANNOT disambiguate them (all verified against
DCurrent/openbor source at v7533, v6391, and dated commits; engine census in
deck-docs/openbor.md):

  offset+slots: 2016-era 3.0 (GUG/AUBF/vsr): 324 B, keys[4][12] @ 0x34
                official 4.0 build 7530:     324 B, keys[4][13] @ 0x28
                3.0 line 2013-2023:          332-352 B, keys[4][12 or 13] @ 0x34
                2010-era (build 2862):       248 B — layout unverified, SKIPPED
  sentinel:     unbound = -999 (3.0 line) or 6937 (4.0-7530); per-file detected
  port stride:  keycode = 600 + port*JOY_MAX_INPUTS + (1+input), and
                JOY_MAX_INPUTS flipped 32 -> 64 between 2018-05 and 2018-07;
                port 0 is stride-independent, ports 1-3 are NOT. The stride is
                resolved from the game's OWN engine fingerprint (the compile
                date its OpenBorLog.txt prints — the census trick).

The writer therefore RESOLVES the layout from size + the engine's compile era
(never searches: heuristic searching rejected real files — players legitimately
mix ports within a row, and ESC is sometimes bound to a pad button), then
VALIDATES it: every value must be a binding or the file's sentinel, and the
four movement slots must be pairwise distinct when bound (the one misalignment
signature with no legitimate counter-example). It then splices ONLY the keys
block — size, dword0 and tail byte-identical, file permissions preserved.

The engine rewrites the whole cfg from memory on quit, so this write happens
AT LAUNCH (openbor.sh) and is authoritative for that session; no restore.

CLI (used by launch, tests, and every on-device gate):
    python3 -m lib.openbor_cfg dump <game_dir>
    python3 -m lib.openbor_cfg apply <game_dir> [dir_key]
    python3 -m lib.openbor_cfg inventory <openbor_root>
"""
from __future__ import annotations

import os
import re
import shutil
import struct
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

try:
    from . import openbor_maps
except ImportError:                       # run as a plain script
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from lib import openbor_maps

MAX_PLAYERS = openbor_maps.MAX_PLAYERS                  # 4

_SIZES = {324, 332, 340, 348, 352}   # known writable struct sizes
SKIP_SIZES = {248}          # 2010-era struct: never write

_JOY_LO = 601
_KB_HI = 600                # keyboard scancodes (SDL_NUM_SCANCODES=512) < 600
_KNOWN_SENTINELS = {openbor_maps.UNMAPPED, 6937}
_ESC_SLOT = 12              # slot index of ESC in 13-slot layouts

_MONTHS = {m: i + 1 for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])}
_COMPILE_RE = re.compile(rb"Compile Date:\s*([A-Z][a-z]{2})\s+\d+\s+(\d{4})")


_SLOTS_FLIP = (2017, 6)     # MAX_BTN_NUM 12 -> 13: between 2017-01 (12) and
                            # 2017-10 (13) in source; no game compiled between.


_GEOM_RE = re.compile(rb"(\d+) axes,\s*(\d+) buttons")


def pad_geometry(game_dir: str | Path) -> tuple[int, int] | None:
    """(buttons, axes) as THIS engine reports the pad, from its own log line —
    or None if it never logged one (no pad was connected on the last run).

    Not constant across the library, which is the whole point of reading it:
        "XInput Controller #1 - 6 axes, 11 buttons, 1 hat(s)"   (SDL2 engines)
        "Wine joystick driver - 5 axes, 10 buttons, 1 hat(s)"   (pre-SDL2)
    The SAME physical pad, two different views — so every offset (the hat base
    above all) shifts with the engine generation. Hardcoding one geometry
    mis-binds the other (proven on-device 2026-07-16).

    Same one-launch staleness as engine_era: this describes the pad of the
    PREVIOUS run. Steady state is correct; the launch after a pad change can be
    stale, and the structural validation is what catches a bad resolution."""
    log = Path(game_dir) / "Logs" / "OpenBorLog.txt"
    try:
        m = _GEOM_RE.search(log.read_bytes())
    except OSError:
        return None
    if not m:
        return None
    return int(m.group(2)), int(m.group(1))          # (buttons, axes)


def engine_era(game_dir: str | Path) -> tuple[int, int] | None:
    """(year, month) the game's bundled engine was compiled, from the banner
    its own engine prints into Logs/OpenBorLog.txt (the census trick). None if
    no log / no parsable date — the caller then refuses to write rather than
    guess a generation.

    STALENESS: the engine truncates and rewrites this log on every launch, so
    the banner describes the engine of the PREVIOUS run. That is correct for
    every steady state (each game ships one engine and never changes it). It is
    stale for exactly one launch after someone swaps a game's .exe for a
    different build — the layout resolved that once could be wrong, and the
    structural validation is what catches it (it refuses rather than writes)."""
    log = Path(game_dir) / "Logs" / "OpenBorLog.txt"
    try:
        m = _COMPILE_RE.search(log.read_bytes())
    except OSError:
        return None
    if not m:
        return None
    month = _MONTHS.get(m.group(1).decode())
    if month is None:                       # unrecognized month -> refuse
        return None
    return int(m.group(2)), month


@dataclass(frozen=True)
class Layout:
    offset: int
    slots: int              # per player (12 = no ESC slot)
    sentinel: int           # this file's "unbound" value
    stride: int             # JOY_MAX_INPUTS of the game's engine

    @property
    def joy_hi(self) -> int:
        return _JOY_LO + MAX_PLAYERS * self.stride      # exclusive

    def is_binding(self, v: int) -> bool:
        return 0 <= v < _KB_HI or _JOY_LO <= v < self.joy_hi

    def port_of(self, v: int) -> int | None:
        return (v - _JOY_LO) // self.stride if _JOY_LO <= v < self.joy_hi else None

    def rows(self, data: bytes) -> list[list[int]]:
        n = MAX_PLAYERS * self.slots
        flat = struct.unpack_from(f"<{n}i", data, self.offset)
        return [list(flat[p * self.slots:(p + 1) * self.slots])
                for p in range(MAX_PLAYERS)]


def _valid(lay: Layout, rows: list[list[int]]) -> bool:
    """Validation only (the layout is RESOLVED from the engine era, never
    searched — earlier structural-search heuristics each rejected real files:
    players legitimately mix ports within a row, and ESC is sometimes bound to
    a pad button):
    - every value is a binding or an unbound sentinel;
    - the four movement slots are pairwise distinct when bound (the one
      misalignment signature with no legitimate counter-example)."""
    for row in rows:
        for v in row:
            if not lay.is_binding(v) and v != lay.sentinel \
                    and v not in _KNOWN_SENTINELS:
                return False
        moves = [v for v in row[:4] if lay.is_binding(v)]
        if len(moves) != len(set(moves)):
            return False
    return True


def _file_sentinel(vals: tuple, stride: int) -> int:
    """This file's unbound marker: the modal non-binding value (known
    sentinels preferred); fully-bound files fall back to -999."""
    joy_hi = _JOY_LO + MAX_PLAYERS * stride
    non = Counter(v for v in vals
                  if not (0 <= v < _KB_HI or _JOY_LO <= v < joy_hi))
    if not non:
        return openbor_maps.UNMAPPED
    known = [v for v, _ in non.most_common() if v in _KNOWN_SENTINELS]
    return known[0] if known else non.most_common(1)[0][0]


def resolve_layout(data: bytes, era: tuple[int, int]) -> Layout | None:
    """The file's keys layout, RESOLVED from size + the engine's compile era
    (slots: 12 before mid-2017, 13 after; stride: 32 before mid-2018, 64
    after; the 324-byte size collision resolves via slots — the 12-slot 2016
    struct keys sit at 0x34, the 13-slot 4.0-7530 struct at 0x28), then
    VALIDATED structurally. None = unknown size or validation failed."""
    size = len(data)
    if size not in _SIZES:
        return None
    slots = 13 if era >= _SLOTS_FLIP else 12
    stride = (openbor_maps.STRIDE_NEW if era >= openbor_maps.STRIDE_FLIP
              else openbor_maps.STRIDE_OLD)
    offset = (0x28 if slots == 13 else 0x34) if size == 324 else 0x34
    n = MAX_PLAYERS * slots
    if offset + n * 4 > size:
        return None
    vals = struct.unpack_from(f"<{n}i", data, offset)
    lay = Layout(offset, slots, _file_sentinel(vals, stride), stride)
    return lay if _valid(lay, lay.rows(data)) else None


def locate_cfg(game_dir: str | Path) -> Path | None:
    """The cfg the ENGINE will actually load, or None.

    The engine names its settings file after the pak it runs
    (Saves/<pakstem>.cfg), so prefer exactly that when a pak is present —
    newest-mtime alone can pick a stale sibling (several games carry leftovers
    from older versions), which would mean writing a file the engine ignores
    while the one it reads keeps the old bindings.

    Never touch a default.cfg twin (several games ship one). A game with NO cfg
    yet gets engine defaults on its first run and ours from the second launch
    on (we cannot synthesize dword0 for an unknown engine).

    Ties break by NAME, deterministically. Equal mtimes are not hypothetical: a
    kernel stamps inodes from a coarse per-tick clock (Ubuntu 22.04's does), and
    a zip stores only 2-second granularity, so two cfgs unpacked from the same
    archive routinely share an mtime. Bare max() would then pick by directory
    order — filesystem luck, differing per machine for the very same game."""
    game_dir = Path(game_dir)
    saves = game_dir / "Saves"
    cands = [p for p in saves.glob("*.cfg") if p.name.lower() != "default.cfg"]
    if not cands:
        return None
    paks = [p for p in (game_dir / "Paks").glob("*.pak")
            if p.name.lower() != "menu.pak"]
    if len(paks) == 1:
        want = (paks[0].stem + ".cfg").lower()
        for p in cands:
            if p.name.lower() == want:
                return p
    return max(cands, key=lambda p: (p.stat().st_mtime, p.name))


def is_manageable(game_dir: str | Path) -> bool:
    """False only when MAD can NEVER write this game's cfg, whatever the user does.

    Distinguishes a PERMANENT refusal from a TEMPORARY one, which is the whole
    point — most skips resolve themselves and must NOT be treated as "MAD does not
    do this game":
      * skip-no-cfg / skip-no-fingerprint / skip-no-geometry are TEMPORARY. A game
        that has never run has no cfg and no engine log; it seeds on a later launch
        once the engine has written both. Answer True — nothing is wrong.
      * skip-248 is FOREVER. That is the 2010-era (build 2862) struct, whose layout
        is unverified, so `SKIP_SIZES` refuses it by design rather than guess at
        offsets and corrupt a save. No launch changes the engine a game ships with.

    On this rig that is exactly one game: Jennifer_By_MasterDerico (Saves/jeni.cfg,
    248 B). MAD never seeds it, so the reset row must not offer to reset it — it
    would clear a seed mark that was never set and promise a write that never
    happens. Its controls belong to the game's own Options -> Controls menu.

    Cheap on purpose (stat, not read): ~4.4 ms for all 33 games, so the Controllers
    page can call it per render. An unreadable/absent cfg answers True — see above,
    it is the not-yet case, not the never case."""
    cfg = locate_cfg(game_dir)
    if cfg is None:
        return True
    try:
        return cfg.stat().st_size not in SKIP_SIZES
    except OSError:
        return True


def _write_preserving(path: Path, data: bytes) -> None:
    """Atomic same-dir replace keeping the original's mode/times (the cfgs
    carry odd permissions from their Windows origins; fsutil's swap would
    reset them to umask defaults)."""
    tmp = path.with_name(path.name + ".mad-tmp")
    try:
        tmp.write_bytes(data)
        shutil.copystat(path, tmp)
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def is_bricked(lay: Layout, rows: list[list[int]]) -> bool:
    """True when Player 1 cannot MOVE with a pad — the game is unplayable here.

    This is the shape the engine's own defaults have. OpenBOR's Setup Player N
    screen carries a `Default` row one line below OK (and Configuration Settings
    has "Restore OpenBoR Defaults"), and both run clearbuttons(): P1 goes to
    KEYBOARD scancodes, P2-P4 to fully unbound. The same shape appears whenever
    the engine regenerates a cfg it could not load — e.g. after the savedata is
    deleted, which on this rig is the only way to clear the fullscreen crash on
    Contrav2/Jennifer, because video settings and keys share one struct.

    There is no keyboard on this rig, docked or handheld, so that state is a
    dead game with a CLI-only recovery. We seed once and hand off, so nothing
    else would ever heal it. Deliberately narrow: it asks only whether P1's four
    movement slots reach a JOYSTICK at all. A real player map always does; no
    pad-configured game is a false positive."""
    return not any(lay.port_of(v) is not None for v in rows[0][:4])


def apply_map(game_dir: str | Path, dir_key: str | None = None) -> str:
    """Splice the game's effective token map into its cfg for all 4 ports, ONCE.

    Returns a status for the launcher log: applied | healed | unchanged |
    skip-seeded | skip-no-cfg | skip-248 | skip-no-fingerprint |
    skip-no-geometry | skip-unknown-layout. Every skip is a deliberate refusal
    (never a guess) and leaves the file untouched.

    SEED ONCE, THEN HANDS OFF: once a game carries the map we intend, its own cfg
    is the truth and the in-game Options -> Controls menu is the editor.
    Re-applying on every launch (what this did until 2026-07-16) meant an in-game
    rebind lasted exactly until the next launch.

    Hands-off is not the same as frozen. We re-seed on exactly two triggers:
      - our INTENT changed (see openbor_maps.seed_fingerprint): a DEFAULT_MAP or
        geometry fix, or a per-game override edit, reaches the game once
      - the game is BRICKED (see is_bricked): P1 cannot move with a pad, so the
        file we would be respecting is a dead game
    Neither fires on a player's own rebind, which is the state hands-off exists
    to protect. `openbor_maps.clear_seeded()` (MAD's reset row, or the `reseed`
    CLI) forces one for anything else."""
    game_dir = Path(game_dir)
    dir_key = dir_key or game_dir.name
    cfg = locate_cfg(game_dir)
    if cfg is None:
        return "skip-no-cfg"
    data = cfg.read_bytes()
    if len(data) in SKIP_SIZES:
        return "skip-248"
    era = engine_era(game_dir)
    if era is None:
        return "skip-no-fingerprint"
    # The pad's offsets shift with how THIS engine's SDL enumerates it, so read
    # that from its own log rather than assuming the XInput view. Without it we
    # cannot place a single binding correctly -> refuse.
    geom = pad_geometry(game_dir)
    if geom is None:
        return "skip-no-geometry"
    lay = resolve_layout(data, era)
    if lay is None:
        return "skip-unknown-layout"
    current = lay.rows(data)
    # Hands off — UNLESS the game is bricked. The seed check sits here, after the
    # cfg is readable, precisely so we can look before we decline: a game that
    # cannot be played is the one case where re-applying beats respecting the
    # player's file, because the state we would be respecting is "no working
    # controls" and the only other way out is a CLI.
    healed = is_bricked(lay, current)
    if openbor_maps.is_seeded(dir_key) and not healed:
        return "skip-seeded"
    # Resolve the map to THIS engine before writing: a control it cannot see gets
    # the nearest one it can (special = ax:rt -> ax:lt on the 5-axis engines), so
    # the slot is bound rather than falling to the preserve branch below and
    # ending up unbound. See openbor_maps.for_geometry.
    token_map = openbor_maps.for_geometry(
        openbor_maps.effective_map(dir_key), geom)
    slots = openbor_maps.SLOTS[:lay.slots]          # 12-slot files have no esc
    patched = bytearray(data)
    for port in range(MAX_PLAYERS):
        # Pass 1: every slot our map CAN express. These are the bindings we are
        # asserting, so they own their keycodes.
        row = [None] * len(slots)
        keep = []
        for i, slot in enumerate(slots):
            token = token_map[slot]
            v = openbor_maps.keycode(token, port, lay.stride, geom)
            if v != openbor_maps.UNMAPPED:
                row[i] = v
            elif token == openbor_maps.NONE_TOKEN:
                row[i] = lay.sentinel               # asked for, so honour it
            else:
                keep.append(i)                      # decided in pass 2
        # Pass 2: slots our vocabulary cannot reach on THIS engine (special =
        # ax:rt on a 5-axis build has no axis 5 to point at). That is our map
        # failing, NOT a request to unbind, so keep what the game already has --
        # but ONLY where it does not collide with a binding we just wrote.
        #
        # The collision is the point. OpenBOR's own menu keeps binds unique via
        # safe_set(), but safe_set does NOT run on load, so a cfg WE write is
        # never de-duped: the engine reads both slots and fires BOTH. Preserving
        # blind did exactly that on all four 5-axis games -- it saved GHDC's
        # special (btn:rb) onto DEFAULT_MAP's atk2 (btn:rb), and Golden Axe's
        # special (btn:x) onto atk1, so every punch would also cast the magic the
        # preserve rule existed to protect. Contrav2 was worse: P2-P4 special
        # landed on `down`, so walking down casts.
        #
        # So: keep it when the keycode is free, else the sentinel -- unbound,
        # which is where the slot sat before 305d58a and is fixable in-game.
        # NEVER write a duplicate.
        taken = {v for v in row if v is not None and v != lay.sentinel}
        for i in keep:
            cur = current[port][i]
            row[i] = cur if (lay.is_binding(cur) and cur not in taken) else lay.sentinel
            if row[i] != lay.sentinel:
                taken.add(row[i])
        struct.pack_into(f"<{lay.slots}i", patched,
                         lay.offset + port * lay.slots * 4, *row)
    # Mark AFTER the map is genuinely on disk, and only then: every skip above
    # leaves the game unseeded on purpose, so it gets its default on a later
    # launch. A brand-new game has no engine log yet -> skip-no-geometry -> it
    # seeds on the second launch, once the log exists to read the pad shape from.
    if bytes(patched) != data:
        _write_preserving(cfg, bytes(patched))
        status = "healed" if healed else "applied"
    else:
        status = "unchanged"                   # already ours = already seeded
    # ONE mark for all three outcomes, deliberately. Reaching here means the map
    # we intend IS on disk -- whether we just wrote it or it was already there --
    # and "unchanged" needs the mark just as much as "applied" does: it is the
    # state right after a reset-row press, and leaving it unmarked means hands-off
    # never engages and the player's next in-game rebind dies on the launch after.
    # As two separate calls (until 2026-07-17) the unchanged one was unpinned and
    # deletable with the suite green; now dropping this line breaks every seeding
    # test at once. See test_the_unchanged_path_marks_the_game_seeded.
    openbor_maps.mark_seeded(dir_key)
    return status


# ── decoding / CLI ─────────────────────────────────────────────────────────────
def _describe(v: int, lay: Layout,
              geom: tuple[int, int] | None = None) -> str:
    """Human-readable token for a raw keycode, decoded under the pad geometry
    the engine reported (falls back to the XInput view when unknown, flagged by
    the caller — a wrong geometry renames controls but never moves bytes)."""
    if v == lay.sentinel or v in _KNOWN_SENTINELS:
        return "--"
    if 0 <= v < _KB_HI:
        return f"kb:{v}"
    p = lay.port_of(v)
    if p is None:
        return f"?{v}"
    off = (v - _JOY_LO) % lay.stride
    for name, o in openbor_maps.offsets_for(*(geom or openbor_maps.GEOM_XINPUT)).items():
        if o == off:
            return f"J{p}.{name if ':' in name else _pretty(name)}"
    return f"J{p}.off{off}"


def _pretty(name: str) -> str:
    """offsets_for keys are bare for btn/ax (hat keys already carry 'hat:')."""
    return f"ax:{name}" if name[-1] in "+-" or name in ("lt", "rt") else f"btn:{name}"


def dump(game_dir: str | Path) -> str:
    game_dir = Path(game_dir)
    cfg = locate_cfg(game_dir)
    if cfg is None:
        return f"{game_dir.name}: no cfg"
    data = cfg.read_bytes()
    head = (f"{game_dir.name}: {cfg.name}  size={len(data)}  "
            f"dword0=0x{struct.unpack_from('<I', data, 0)[0]:08x}")
    if len(data) in SKIP_SIZES:
        return head + "  [SKIP: 248-byte 2010 layout]"
    era = engine_era(game_dir)
    if era is None:
        return head + "  [NO ENGINE FINGERPRINT — would refuse to write]"
    lay = resolve_layout(data, era)
    if lay is None:
        return head + f"  era={era}  [LAYOUT VALIDATION FAILED — would refuse to write]"
    geom = pad_geometry(game_dir)
    gtxt = (f"geom={geom[0]}btn/{geom[1]}ax hat@{geom[0] + 2 * geom[1]}"
            if geom else "geom=? [NO PAD LINE — would refuse to write]")
    lines = [head + f"  era={era[0]}-{era[1]:02d} keys@{hex(lay.offset)} "
                    f"slots={lay.slots} stride={lay.stride} "
                    f"sentinel={lay.sentinel} {gtxt}"]
    for p, row in enumerate(lay.rows(data)):
        if all(not lay.is_binding(v) or v == 0 for v in row):
            continue
        pairs = " ".join(f"{s}={_describe(v, lay, geom)}"
                         for s, v in zip(openbor_maps.SLOTS, row))
        lines.append(f"  P{p + 1}: {pairs}")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[0] == "dump":
        print(dump(argv[1]))
        return 0
    if len(argv) >= 2 and argv[0] == "apply":
        status = apply_map(argv[1], argv[2] if len(argv) > 2 else None)
        print(f"openbor_cfg apply {Path(argv[1]).name}: {status}")
        # Every skip is an expected, deliberate refusal (the 2010-era game, a
        # first-ever launch with no cfg yet, an unreadable engine banner) — the
        # game still launches fine, so they are NOT launcher errors. Only a
        # crash (an exception escaping apply_map) is a real failure.
        return 0
    if argv and argv[0] == "reseed":
        # Hands-off is the whole point, so this is the only road back to the
        # default for a game whose in-game controls have been edited into a
        # corner. Takes a dir_key or a game path; --all forgets every game.
        if len(argv) < 2:
            print("usage: openbor_cfg reseed <dir_key|game_dir>|--all",
                  file=sys.stderr)
            return 2
        key = None if argv[1] == "--all" else Path(argv[1]).name
        gone = openbor_maps.clear_seeded(key)
        if not gone:
            print(f"openbor_cfg reseed: nothing to do "
                  f"({'no game is' if key is None else key + ' is not'} seeded)")
        else:
            print("openbor_cfg reseed: will re-apply the default at next launch "
                  "for: " + ", ".join(gone))
        return 0
    if len(argv) >= 2 and argv[0] == "inventory":
        root = Path(argv[1])
        for d in sorted(p for p in root.iterdir() if p.is_dir()):
            if (d / "Saves").is_dir():
                print(dump(d))
        return 0
    print("usage: openbor_cfg dump|apply|inventory <path> [dir_key]\n"
          "       openbor_cfg reseed <dir_key|game_dir>|--all",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
