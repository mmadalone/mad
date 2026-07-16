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

The writer therefore: resolves the stride from the game's log, DISCOVERS the
(offset, slots) layout structurally — every value must be a binding or the
file's sentinel, joystick codes within a player row must share one port, the
four movement slots must be distinct when bound, and a 13-slot parse must show
a keyboard/unbound ESC in slot 12 — then splices ONLY the keys block, size and
dword0 and tail untouched. A wrong alignment mixes ports mid-row or duplicates
movement bindings and is rejected (both failure modes were observed live).

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


def engine_era(game_dir: str | Path) -> tuple[int, int] | None:
    """(year, month) the game's bundled engine was compiled, from the banner
    its own engine prints into Logs/OpenBorLog.txt (the census trick). None if
    no log / no date — the caller then refuses to write rather than guess."""
    log = Path(game_dir) / "Logs" / "OpenBorLog.txt"
    try:
        m = _COMPILE_RE.search(log.read_bytes())
    except OSError:
        return None
    if not m:
        return None
    return int(m.group(2)), _MONTHS.get(m.group(1).decode(), 12)


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
    """The game's live cfg: newest non-default Saves/*.cfg (None if absent).

    Several games ship a default.cfg twin next to the real one — never touch it.
    A game with NO cfg yet gets engine defaults on its first run and ours from
    the second launch on (we cannot synthesize dword0 for an unknown engine)."""
    saves = Path(game_dir) / "Saves"
    cands = [p for p in saves.glob("*.cfg") if p.name.lower() != "default.cfg"]
    return max(cands, key=lambda p: p.stat().st_mtime) if cands else None


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


def apply_map(game_dir: str | Path, dir_key: str | None = None) -> str:
    """Splice the game's effective token map into its cfg for all 4 ports.

    Returns a status for the launcher log: applied | unchanged | skip-no-cfg |
    skip-248 | skip-no-stride | skip-unknown-layout."""
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
    lay = resolve_layout(data, era)
    if lay is None:
        return "skip-unknown-layout"
    token_map = openbor_maps.effective_map(dir_key)
    slots = openbor_maps.SLOTS[:lay.slots]          # 12-slot files have no esc
    patched = bytearray(data)
    for port in range(MAX_PLAYERS):
        row = []
        for slot in slots:
            v = openbor_maps.keycode(token_map[slot], port, lay.stride)
            row.append(lay.sentinel if v == openbor_maps.UNMAPPED else v)
        struct.pack_into(f"<{lay.slots}i", patched,
                         lay.offset + port * lay.slots * 4, *row)
    if bytes(patched) == data:
        return "unchanged"
    _write_preserving(cfg, bytes(patched))
    return "applied"


# ── decoding / CLI ─────────────────────────────────────────────────────────────
def _describe(v: int, lay: Layout) -> str:
    if v == lay.sentinel or v in _KNOWN_SENTINELS:
        return "--"
    if 0 <= v < _KB_HI:
        return f"kb:{v}"
    p = lay.port_of(v)
    if p is not None:
        off = (v - _JOY_LO) % lay.stride
        for table, prefix in ((openbor_maps._BTN_OFFSET, "btn"),
                              (openbor_maps._AX_OFFSET, "ax"),
                              (openbor_maps._HAT_OFFSET, "hat")):
            for name, o in table.items():
                if o == off:
                    return f"J{p}.{prefix}:{name}"
        return f"J{p}.off{off}"
    return f"?{v}"


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
    lines = [head + f"  era={era[0]}-{era[1]:02d} keys@{hex(lay.offset)} "
                    f"slots={lay.slots} stride={lay.stride} sentinel={lay.sentinel}"]
    for p, row in enumerate(lay.rows(data)):
        if all(not lay.is_binding(v) or v == 0 for v in row):
            continue
        pairs = " ".join(f"{s}={_describe(v, lay)}"
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
        return 0 if status in ("applied", "unchanged") else 1
    if len(argv) >= 2 and argv[0] == "inventory":
        root = Path(argv[1])
        for d in sorted(p for p in root.iterdir() if p.is_dir()):
            if (d / "Saves").is_dir():
                print(dump(d))
        return 0
    print("usage: openbor_cfg dump|apply|inventory <path> [dir_key]",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
