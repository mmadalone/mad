#!/usr/bin/env python3
"""
Fill ES-DE metadata + screenshots for the `steam` system from the Steam STORE API
(appdetails) — correct-game, no online name-scraper (which mislabels these).

For each Steam game (stem→appid via its `.sh` rungameid):
  • writes <desc>/<developer>/<publisher>/<genre>/<releasedate> into the gamelist
    (preserves <path>/<name> and any other tags),
  • downloads one screenshot → downloaded_media/steam/screenshots/<stem>.jpg,
  • COVER FIX: if the cover is missing or LANDSCAPE (w>h, a "sideways banner"),
    fetch the real portrait (library_600x900); if Steam has none, delete the bad
    landscape cover so ES-DE doesn't render it sideways.

Non-Steam games are skipped (no store API). Rate-safe: store-API calls paced
≥1.5s apart, STOP on HTTP 429. Flags: --dry-run, --no-screenshots, --no-metadata,
--only <stem> (repeatable: touch only these launcher stems' gamelist blocks/media,
leave every other entry byte-identical — use when adding new games).
"""
import os
import re
import sys
import glob
import json
import time
import html
import subprocess
import urllib.request
import urllib.error
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import sgdb                                          # noqa: E402
from lib import fsutil                                        # noqa: E402
from lib.proc_guard import abort_if_esde_running             # noqa: E402

HOME = Path.home()
ROMS = Path(os.path.realpath(HOME / "ROMs")) / "steam"
GL = HOME / "ES-DE" / "gamelists" / "steam" / "gamelist.xml"
MEDIA = Path("/run/media/deck/1tbDeck/downloaded_media/steam")
UA = {"User-Agent": "Mozilla/5.0"}
API_DELAY = 1.5
_last = [0.0]
META_TAGS = ("desc", "developer", "publisher", "genre", "releasedate")
PORTRAIT = lambda a: [
    f"https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/{a}/library_600x900.jpg",
    f"https://steamcdn-a.akamaihd.net/steam/apps/{a}/library_600x900.jpg",
]


def appdetails(appid):
    """Full appdetails dict, or None, or 'throttled' on HTTP 429."""
    wait = API_DELAY - (time.monotonic() - _last[0])
    if wait > 0:
        time.sleep(wait)
    _last[0] = time.monotonic()
    try:
        req = urllib.request.Request(
            f"https://store.steampowered.com/api/appdetails?appids={appid}&l=english",
            headers=UA)
        with urllib.request.urlopen(req, timeout=25) as r:
            j = json.load(r)
    except urllib.error.HTTPError as he:
        return "throttled" if he.code == 429 else None
    except Exception:
        return None
    e = j.get(str(appid), {})
    return e["data"] if e.get("success") else None


def parse_release(rd):
    s = (rd or {}).get("date", "").strip()
    if not s:
        return None
    for fmt in ("%d %b, %Y", "%b %d, %Y", "%d %B, %Y", "%B %d, %Y", "%b %Y", "%Y"):
        try:
            return time.strftime("%Y%m%dT000000", time.strptime(s, fmt))
        except ValueError:
            pass
    return None


def clean(s):
    return html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def dims(path):
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", str(path)],
            capture_output=True, text=True, timeout=15).stdout.strip()
        w, h = out.split("x")
        return int(w), int(h)
    except Exception:
        return None


def download(url, dst):
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=30) as r:
            if r.status != 200:
                return False
            data = r.read()
        if len(data) < 1000:
            return False
        dst.write_bytes(data)
        return True
    except Exception:
        return False


def fix_cover(appid, stem, dry):
    """Ensure a PORTRAIT cover. Returns 'ok'|'fixed'|'none'."""
    covers = MEDIA / "covers"
    existing = list(covers.glob(glob.escape(stem) + ".*"))
    if existing:
        d = dims(existing[0])
        if d and d[0] <= d[1]:
            return "ok"                      # already portrait/square
    # missing or landscape → try the real portrait: Steam CDN, then SteamGridDB
    candidates = list(PORTRAIT(appid))
    su = sgdb.art_url("cover", appid=appid)
    if su:
        candidates.append(su)
    for url in candidates:
        ext = os.path.splitext(urllib.parse.urlparse(url).path)[1].lower()
        if ext not in (".png", ".jpg", ".jpeg", ".webp"):
            ext = ".jpg"
        tmp = covers / (stem + ".portrait" + ext)
        if not dry and download(url, tmp):
            d = dims(tmp)
            if d and d[0] <= d[1]:
                if existing:                      # rule #5: never delete — move the
                    retired = fsutil.recoverable_delete(  # old cover(s) to a _TMP
                        existing, tmp_base=Path("/run/media/deck/1tbDeck"),
                        tag="steam-cover-replaced",
                        recovery_note=("steam-fetch-metadata replaced a landscape "
                                       "cover with a portrait; the old cover(s) are "
                                       "here, recoverable."))
                    print(f"  cover: old landscape cover moved to {retired}")
                tmp.replace(covers / (stem + ext))
                return "fixed"
            tmp.unlink(missing_ok=True)
    # No portrait anywhere — KEEP the existing landscape cover (a sideways cover
    # beats no cover); never delete the user's only cover for being landscape.
    return "none"


def fetch_screenshot(data, stem, dry):
    ss = data.get("screenshots", [])
    if not ss:
        return False
    dst = MEDIA / "screenshots" / (stem + ".jpg")
    if list((MEDIA / "screenshots").glob(glob.escape(stem) + ".*")):
        return True                          # already have one
    url = ss[len(ss) // 3].get("path_full") or ss[0].get("path_full")
    return bool(url) and (dry or download(url, dst))


def meta_from(data):
    devs = data.get("developers") or []
    pubs = data.get("publishers") or []
    gen = ", ".join(g["description"] for g in data.get("genres", []))
    return {
        "desc": clean(data.get("short_description", "")),
        "developer": clean(", ".join(devs)),
        "publisher": clean(", ".join(pubs)),
        "genre": clean(gen),
        "releasedate": parse_release(data.get("release_date")),
    }


def rebuild_block(block, meta):
    # IMPORTANT: parse the INNER content — matching <(\w+)> on the whole block
    # would capture the outer <game>…</game> itself and produce nested <game> tags.
    im = re.search(r"<game>(.*)</game>", block, re.S)
    inner = im.group(1) if im else block
    tags = re.findall(r"<(\w+)>(.*?)</\1>", inner, re.S)
    d = {t: v for t, v in tags}
    for k, v in meta.items():
        if v:
            d[k] = esc(v)
    order = ["path", "name", "desc", "developer", "publisher", "genre", "releasedate"]
    seen = set()
    lines = ["\t<game>"]
    for t in order + [t for t, _ in tags if t not in order]:
        if t in d and t not in seen:
            lines.append(f"\t\t<{t}>{d[t]}</{t}>")
            seen.add(t)
    lines.append("\t</game>")
    return "\n".join(lines)


def main():
    dry = "--dry-run" in sys.argv
    if not dry and abort_if_esde_running("update the Steam gamelist metadata"):
        return
    do_ss = "--no-screenshots" not in sys.argv
    do_meta = "--no-metadata" not in sys.argv
    for sub in ("covers", "screenshots"):
        (MEDIA / sub).mkdir(parents=True, exist_ok=True)

    only = {sys.argv[i + 1] for i, a in enumerate(sys.argv)
            if a == "--only" and i + 1 < len(sys.argv)}
    stem_appid = {}
    for sh in glob.glob(str(ROMS / "*.sh")):
        rg = int(re.search(r"rungameid/(\d+)", Path(sh).read_text()).group(1))
        if rg < 2**32:
            stem_appid[Path(sh).stem] = rg
    if only:
        missing = only - set(stem_appid)
        if missing:
            print(f"⚠ --only stems with no Steam launcher: {sorted(missing)}")
        stem_appid = {s: a for s, a in stem_appid.items() if s in only}

    txt = GL.read_text(encoding="utf-8")
    if not dry:
        GL.with_suffix(f".xml.bak-{time.strftime('%Y%m%d-%H%M%S')}").write_text(txt, encoding="utf-8")

    stats = {"meta": 0, "ss": 0, "cover_fixed": 0}
    throttled = [False]

    def process(m):
        block = m.group(0)
        pm = re.search(r"<path>\./([^<]+)\.sh</path>", block)
        if not pm:
            return block
        stem = pm.group(1)
        appid = stem_appid.get(stem)
        if appid is None or throttled[0]:
            return block                      # non-Steam or stopped
        data = appdetails(appid)
        if data == "throttled":
            throttled[0] = True
            print("⚠ 429 from Steam store API — stopping (re-run later).")
            return block
        if not data:
            return block
        if do_ss and fetch_screenshot(data, stem, dry):
            stats["ss"] += 1
        cv = fix_cover(appid, stem, dry)
        if cv == "fixed":
            stats["cover_fixed"] += 1
        if do_meta:
            stats["meta"] += 1
            return rebuild_block(block, meta_from(data))
        return block

    new = re.sub(r"\t<game>.*?</game>", process, txt, flags=re.S)
    if not dry and do_meta:
        fsutil.atomic_write(GL, new)
    print(("[dry-run] " if dry else "") +
          f"metadata:{stats['meta']} screenshots:{stats['ss']} "
          f"covers_fixed:{stats['cover_fixed']}")


if __name__ == "__main__":
    main()
