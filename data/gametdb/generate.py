"""One-shot: download wiitdb.zip once, then
  1. regenerate launchers data/gametdb/cc_ids.json (now incl. nunchuk_ids)
  2. build data/gametdb/sideways_ids.json from a curated NAME list matched to wiitdb titles.
Run:  python3 data/gametdb/generate.py   (from the launchers dir; downloads wiitdb.zip ~10MB)
"""
import io
import json
import re
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path.home() / "Emulation/tools/launchers"))
from lib import dolphin_wii_tdb as tdb  # noqa: E402

SCRATCH = Path("/tmp/claude-1000") if Path("/tmp/claude-1000").is_dir() else Path("/tmp")
XML = SCRATCH / "wiitdb.xml"
DATA = Path.home() / "Emulation/tools/launchers/data/gametdb"

# Curated: Wii games where SIDEWAYS Wiimote is the primary/default scheme. Seeded from the
# community lists (racketboy "Sideways NES Control Games", Giant Bomb/IGDB "Sideways Wii Remote
# Gameplay" concept — 2026-08-04) . Aliases cover regional titles. CC-capable games are pointless
# here (the launch ladder checks CC first) and deliberately left out.
CURATED = {
    "New Super Mario Bros. Wii": [],
    "Donkey Kong Country Returns": [],
    "Kirby's Epic Yarn": [],
    "Kirby's Return to Dream Land": ["Kirby's Adventure Wii"],
    "Wario Land: Shake It!": ["Wario Land: The Shake Dimension"],
    "Excite Truck": [],
    "Excitebots: Trick Racing": [],
    "Punch-Out!!": [],
    "Metroid: Other M": [],
    "A Boy and His Blob": [],
    "Dream Pinball 3D": [],
    "Super Paper Mario": [],   # held sideways; GameTDB lists it nunchuk-optional only
    # WiiWare sideways staples (Mega Man 9/10, the ReBirth trio, Bit.Trip Runner, Fluidity,
    # Art Style: light trax) are NOT in the EN wiitdb dump (disc titles only) — add their
    # 6-char ids directly to sideways_ids.json "ids" if those .wads ever land in the library.
}

_norm_re = re.compile(r"[^a-z0-9]+")


def norm(s: str) -> str:
    return _norm_re.sub("", s.lower())


def main() -> None:
    if not XML.exists():
        print("downloading wiitdb.zip ...")
        req = urllib.request.Request(tdb.WIITDB_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=120) as r:
            blob = r.read()
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            name = next(n for n in z.namelist() if n.endswith("wiitdb.xml"))
            XML.write_bytes(z.read(name))
        print(f"extracted {XML} ({XML.stat().st_size} bytes)")

    parsed = tdb._parse_all(str(XML), strict=True)
    cc, motion, nunchuk = parsed["cc"], parsed["motion"], parsed["nunchuk"]
    print(f"cc={len(cc)} motion={len(motion)} nunchuk={len(nunchuk)}")
    assert len(cc) >= tdb._MIN_CC_IDS, "truncated parse"
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / "cc_ids.json").write_text(json.dumps(
        {"generated": int(time.time()), "source": tdb.WIITDB_URL,
         "ids": sorted(cc), "motion_ids": sorted(motion), "nunchuk_ids": sorted(nunchuk)}))
    print("wrote cc_ids.json")

    # name -> ids across all regions (skip VC / GameCube entries; keep Wii discs + WiiWare)
    by_name: dict[str, list[str]] = {}
    root = None
    for ev, elem in ET.iterparse(str(XML), events=("start", "end")):
        if root is None:
            root = elem
            continue
        if ev == "end" and elem.tag == "game":
            gid = (elem.findtext("id") or "").strip()
            gtype = (elem.findtext("type") or "").strip()
            if tdb._ID_RE.match(gid) and not gtype.startswith("VC-") and gtype != "GameCube":
                cands = [t.text.strip() for t in elem.findall("locale/title")
                         if t.text and t.text.strip()]
                gname = (elem.get("name") or "").strip()
                if gname:   # strip the trailing "(USA) (EN)"-style qualifier groups
                    cands.append(re.sub(r"(\s*\([^()]*\))+\s*$", "", gname))
                for cand in cands:
                    by_name.setdefault(norm(cand), []).append(gid)
            root.clear()

    names_out: dict[str, list[str]] = {}
    ids: set[str] = set()
    for primary, aliases in CURATED.items():
        got: list[str] = []
        for cand in [primary] + aliases:
            got += by_name.get(norm(cand), [])
        got = sorted(set(got))
        if not got:
            print(f"  UNMATCHED: {primary!r}")
        else:
            names_out[primary] = got
            ids.update(got)
            print(f"  {primary}: {len(got)} ids")
    payload = {
        "generated": int(time.time()),
        "sources": ["racketboy.com sideways NES-control list",
                    "Giant Bomb / IGDB 'Sideways Wii Remote Gameplay' concept",
                    "curated 2026-08-04 — edit `names` + rerun, or add ids directly"],
        "names": names_out,
        "ids": sorted(ids),
    }
    (DATA / "sideways_ids.json").write_text(json.dumps(payload, indent=1))
    print(f"wrote sideways_ids.json with {len(ids)} ids for {len(names_out)} games")


if __name__ == "__main__":
    main()
