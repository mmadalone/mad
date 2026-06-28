#!/usr/bin/env python3
"""Generate data/lindbergh-profiles.json from the lindbergh-loader source + TeknoParrot Lindbergh profiles.

DEV-TIME generator (NOT used at runtime). It distills our own slim, lindbergh-specific profile data:
per-game genre, native resolution, and friendly input rows (label -> lindbergh.ini [EVDEV] key). We do
NOT ship TeknoParrot's XML files; only this distilled JSON is committed (Miquel's call). Re-run when the
loader or TeknoParrot add games.

Sources:
  config.h   -- #define <GAME> 0x<crc>. That CRC is the loader's per-rev id = zlib.crc32 of the 0x4000
                bytes at ELF program-header[2].p_offset + 10 (verified: ramboM.elf -> 0x048F49DD == RAMBO).
  gameData.c -- the per-rev table (genre, native width/height, gameID), keyed by the same <GAME> consts.
  TeknoParrot GameProfiles (EmulatorType=Lindbergh) -- friendly button/axis names + InputMapping codes,
                which map deterministically to lindbergh.ini [EVDEV] keys.

Output: data/lindbergh-profiles.json, keyed by CRC hex (lowercase, no 0x), each entry:
  {"gameid","name","genre","gun","native_w","native_h","rows":[{"label","key","axis"}]|null}
rows is null when no TeknoParrot profile matched the game (runtime falls back to generic per-genre rows).

Usage: python3 tools/tp2lindbergh.py [--loader-src DIR] [--tp-xml DIR] [--out FILE]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
LAUNCHERS = HERE.parent
DEFAULT_LOADER_SRC = Path("/tmp/lindbergh-loader/src/lindbergh")
DEFAULT_TP_XML = Path.home() / "Downloads" / "_lindbergh"
DEFAULT_OUT = LAUNCHERS / "data" / "lindbergh-profiles.json"

# The lindbergh.ini [EVDEV] keys the loader actually reads (config.c). Generated rows are filtered to this
# set so we never emit a dead row the loader ignores.
VALID_KEYS = (
    {f"ANALOGUE_{i}" for i in range(1, 9)}
    | {f"PLAYER_{p}_BUTTON_{b}" for p in (1, 2) for b in list(range(1, 9))
       + ["UP", "DOWN", "LEFT", "RIGHT", "SERVICE", "START"]}
    | {f"PLAYER_{p}_COIN" for p in (1, 2)}
    | {"TEST_BUTTON"}
)

# TeknoParrot profile filename (stem) -> the loader gameID(s) (gameData.c column 5) it provides rows for.
# One profile's button layout applies to every rev sharing that gameID. First profile to claim a gameID
# wins (later dups are skipped with a warning). StarTrekVoyager is not in gameData.c, so it is dropped.
PROFILE_GAMEIDS = {
    "HOTD4": ["SBLC"],
    "HOTD4SP": ["SBLS"],
    "Rambo": ["SBQL", "SBSS"],
    "2Spicy": ["SBMV"],
    "GSEVO": ["SBNJ"],
    "LGJS": ["SBNR"],
    "abc": ["SBLR", "SBMN"],
    "ID4Jap": ["SBML"],
    "ID4Exp": ["SBNK"],
    "ID5": ["SBQZ", "SBRY", "SBTS"],
    "or2spdlx": ["SBMB"],
    "R-Tuned": ["SBQW"],
    "segartv": ["SBPF"],
    "VF5B": ["SBLM"],
    "VF5C": ["SBLM"],
    "VT3": ["SBKX"],
}


def tp_to_inikey(mapping: str):
    """TeknoParrot InputMapping code -> lindbergh.ini [EVDEV] key, or None if not an evdev-bindable key
    (lightgun pseudo, relative-axis, or AnalogNSpecialM digital-fallback entries are dropped)."""
    m = mapping.strip()
    mo = re.fullmatch(r"P([12])Button([1-8])", m)
    if mo:
        return f"PLAYER_{mo.group(1)}_BUTTON_{mo.group(2)}"
    mo = re.fullmatch(r"P([12])Button(Start|Up|Down|Left|Right)", m)
    if mo:
        return f"PLAYER_{mo.group(1)}_BUTTON_{mo.group(2).upper()}"
    mo = re.fullmatch(r"Coin([12])", m)
    if mo:
        return f"PLAYER_{mo.group(1)}_COIN"
    mo = re.fullmatch(r"Service([12])", m)
    if mo:
        return f"PLAYER_{mo.group(1)}_BUTTON_SERVICE"
    if m == "Test":
        return "TEST_BUTTON"
    # Analog0/2/4/6 -> ANALOGUE_1/2/3/4 (k/2 + 1). Odd indices / *Special* / relative are not plain channels.
    mo = re.fullmatch(r"Analog([0-9]+)", m)
    if mo:
        k = int(mo.group(1))
        if k % 2 == 0:
            return f"ANALOGUE_{k // 2 + 1}"
    return None


def parse_config_h(path: Path) -> dict:
    """#define <GAME> 0x<crc>  ->  {GAME: crc_int}."""
    out = {}
    for m in re.finditer(r"#define\s+([A-Z0-9_]+)\s+0x([0-9a-fA-F]+)\b", path.read_text()):
        out[m.group(1)] = int(m.group(2), 16)
    return out


_GD_RE = re.compile(
    r'\{\s*([A-Z0-9_]+)\s*,'           # 1 const
    r'\s*"([^"]*)"\s*,'                # 2 title
    r'\s*"([^"]*)"\s*,'                # 3 short
    r'\s*"([^"]*)"\s*,'                # 4 dvp
    r'\s*"([^"]*)"\s*,'                # 5 gameID
    r'\s*"([^"]*)"\s*,'                # 6 year
    r'\s*"([^"]*)"\s*,'                # 7 native res string
    r'\s*([A-Z0-9_]+)\s*,'            # 8 status
    r'\s*([A-Z0-9_]+)\s*,'            # 9 jvsio
    r'\s*([A-Z0-9_]+)\s*,'            # 10 gameType
    r'\s*(-?\d+)\s*,'                 # 11 width
    r'\s*(-?\d+)\s*,'                 # 12 height
)


def parse_gamedata_c(path: Path) -> list:
    rows = []
    for m in _GD_RE.finditer(path.read_text()):
        rows.append({
            "const": m.group(1), "title": m.group(2), "short": m.group(3),
            "gameid": m.group(5), "genre": m.group(10).lower(),
            "width": int(m.group(11)), "height": int(m.group(12)),
        })
    return rows


def parse_tp_xml(path: Path) -> list:
    """One TeknoParrot GameProfile -> ordered, deduped rows [{label, key, axis}] (valid keys only)."""
    root = ET.parse(path).getroot()
    rows, seen = [], set()
    for jb in root.findall("./JoystickButtons/JoystickButtons"):
        label = (jb.findtext("ButtonName") or "").strip()
        key = tp_to_inikey(jb.findtext("InputMapping") or "")
        if not label or key is None or key not in VALID_KEYS or key in seen:
            continue
        seen.add(key)
        rows.append({"label": label, "key": key, "axis": key.startswith("ANALOGUE_")})
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--loader-src", type=Path, default=DEFAULT_LOADER_SRC)
    ap.add_argument("--tp-xml", type=Path, default=DEFAULT_TP_XML)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()

    crcs = parse_config_h(args.loader_src / "config.h")
    games = parse_gamedata_c(args.loader_src / "gameData.c")
    if not crcs or not games:
        print("error: could not parse config.h / gameData.c; check --loader-src", file=sys.stderr)
        return 1

    # gameID -> rows, from the curated TeknoParrot mapping (first profile to claim a gameID wins).
    rows_by_gameid: dict[str, list] = {}
    for stem, gameids in PROFILE_GAMEIDS.items():
        xml = args.tp_xml / f"{stem}.xml"
        if not xml.is_file():
            print(f"warn: TP profile not found, skipping: {xml}", file=sys.stderr)
            continue
        rows = parse_tp_xml(xml)
        for gid in gameids:
            if gid in rows_by_gameid:
                print(f"warn: gameID {gid} already has rows; skipping {stem}", file=sys.stderr)
                continue
            rows_by_gameid[gid] = rows

    out: dict[str, dict] = {}
    with_rows = 0
    for g in games:
        crc = crcs.get(g["const"])
        if crc is None or g["width"] <= 0:   # skip segaboot / unknown-CRC rows
            continue
        rows = rows_by_gameid.get(g["gameid"])
        if rows:
            with_rows += 1
        out[f"{crc:08x}"] = {
            "gameid": g["gameid"], "name": g["title"], "genre": g["genre"],
            "gun": g["genre"] == "shooting",
            "native_w": g["width"], "native_h": g["height"],
            "rows": rows or None,
        }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
    genres = sorted({v["genre"] for v in out.values()})
    print(f"wrote {args.out}: {len(out)} CRCs, {with_rows} with TP rows, genres={genres}")
    print(f"  gameIDs with rows: {sorted(rows_by_gameid)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
