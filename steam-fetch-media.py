#!/usr/bin/env python3
"""
Populate ES-DE media for the `steam` system. Sources, in order of reliability,
all keyed to the correct game (NO online name-scraping — ES-DE's scraper mis-matches
these, e.g. it scraped "Death Trash" as "2Dark"):

  1. LOCAL Steam art  (offline): ~/.steam/steam/appcache/librarycache/<appid>/
        library_600x900.jpg → cover, library_hero.jpg → fanart, logo.png → marquee
  2. Steam CDN        (online, by appid) for Steam games still missing any of those.
  3. Non-Steam games  : custom grid art in userdata/<u>/config/grid/ (local only).
  4. Videos           (online, Steam games only): the store API trailer (DASH) → mp4
        via ffmpeg, only for games that don't already have a video.

Each launcher ~/ROMs/steam/<stem>.sh embeds `steam steam://rungameid/<id>`:
  id < 2^32      → Steam appid;   id >= 2^32 → non-Steam (shortcut appid = id >> 32).
Media → MediaDirectory/steam/{covers,fanart,marquees,videos}/<stem>.<ext> (ES-DE
discovers by ROM filename stem). Overwrites art so prior mis-scrapes are corrected.

Flags: --offline (local art only), --no-videos, --dry-run.
"""
import os
import re
import sys
import json
import glob
import time
import shutil
import subprocess
import urllib.request
import urllib.error
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import sgdb                                          # noqa: E402

HOME = Path.home()
ROMS = Path(os.path.realpath(HOME / "ROMs")) / "steam"
LIBCACHE = HOME / ".steam/steam/appcache/librarycache"
GRIDS = sorted((HOME / ".steam/steam/userdata").glob("*/config/grid"))
MEDIA = Path("/run/media/deck/1tbDeck/downloaded_media/steam")
SUBDIR = {"cover": "covers", "fanart": "fanart", "marquee": "marquees"}
CDN = "https://steamcdn-a.akamaihd.net/steam/apps/{appid}/{f}"
CDN_FILE = {"cover": "library_600x900.jpg", "fanart": "library_hero.jpg", "marquee": "logo.png"}
UA = {"User-Agent": "Mozilla/5.0"}
VIDEO_SECONDS = 30
# Be a polite client to the rate-limited Steam STORE API (the CDN/video hosts are
# tolerant; appdetails is the one that throttles → can escalate to a temp IP ban).
# Pace calls, and on the first HTTP 429 STOP making more (back off, don't escalate).
API_DELAY = 1.5          # seconds between store-API calls
_api_last = [0.0]


def first_existing(*cands):
    for c in cands:
        if c and Path(c).is_file():
            return Path(c)
    return None


def grid_art(appid, rgid, kind):
    pats = []
    for key in (appid, rgid):
        if kind == "cover":
            pats += [f"{key}p.png", f"{key}p.jpg", f"{key}.png", f"{key}.jpg"]
        else:
            pats += [f"{key}_{kind}.png", f"{key}_{kind}.jpg"]
    for g in GRIDS:
        hit = first_existing(*[g / p for p in pats])
        if hit:
            return hit
    return None


def local_sources(rgid):
    if rgid < 2**32:
        d = LIBCACHE / str(rgid)
        return {"cover": first_existing(d / "library_600x900.jpg"),
                "fanart": first_existing(d / "library_hero.jpg"),
                "marquee": first_existing(d / "logo.png")}
    appid = rgid >> 32
    return {"cover": grid_art(appid, rgid, "cover"),
            "fanart": grid_art(appid, rgid, "hero"),
            "marquee": grid_art(appid, rgid, "logo")}


def have_media(sub, stem):
    return any((MEDIA / sub).glob(glob.escape(stem) + ".*"))


def place(sub, stem, src_path):
    for other in (MEDIA / sub).glob(glob.escape(stem) + ".*"):
        other.unlink()
    dst = MEDIA / sub / (stem + Path(src_path).suffix)
    shutil.copy2(src_path, dst)


def cdn_download(appid, kind, stem):
    url = CDN.format(appid=appid, f=CDN_FILE[kind])
    ext = Path(CDN_FILE[kind]).suffix
    dst = MEDIA / SUBDIR[kind] / (stem + ext)
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=30) as r:
            if r.status != 200:
                return False
            data = r.read()
        if len(data) < 1000:        # tiny = placeholder/404 body
            return False
        for other in (MEDIA / SUBDIR[kind]).glob(glob.escape(stem) + ".*"):
            other.unlink()
        dst.write_bytes(data)
        return True
    except Exception:
        return False


def sgdb_download(url, kind, stem):
    """Download a SteamGridDB art URL into the kind's media dir (ext from URL)."""
    ext = os.path.splitext(urllib.parse.urlparse(url).path)[1].lower()
    if ext not in (".png", ".jpg", ".jpeg", ".webp"):
        ext = ".png"
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=30) as r:
            if r.status != 200:
                return False
            data = r.read()
        if len(data) < 1000:
            return False
        for other in (MEDIA / SUBDIR[kind]).glob(glob.escape(stem) + ".*"):
            other.unlink()
        (MEDIA / SUBDIR[kind] / (stem + ext)).write_bytes(data)
        return True
    except Exception:
        return False


def fetch_video(appid, stem):
    """Returns 'ok' | 'none' | 'throttled'. On 429 the caller stops calling the
    store API so we never hammer it into a temp ban."""
    try:
        wait = API_DELAY - (time.monotonic() - _api_last[0])   # pace store-API calls
        if wait > 0:
            time.sleep(wait)
        _api_last[0] = time.monotonic()
        req = urllib.request.Request(
            f"https://store.steampowered.com/api/appdetails?appids={appid}&filters=movies",
            headers=UA)
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                j = json.load(r)
        except urllib.error.HTTPError as he:
            if he.code == 429:
                return "throttled"
            return "none"
        entry = j.get(str(appid), {})
        if not entry.get("success"):
            return "none"
        movies = entry["data"].get("movies", [])
        if not movies:
            return "none"
        url = movies[0].get("dash_h264") or movies[0].get("hls_h264")
        if not url:
            return "none"
        tmp = MEDIA / "videos" / (stem + ".part.mp4")
        out = MEDIA / "videos" / (stem + ".mp4")
        rc = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", url, "-c", "copy",
             "-t", str(VIDEO_SECONDS), "-movflags", "+faststart", str(tmp)],
            timeout=180).returncode
        if rc == 0 and tmp.is_file() and tmp.stat().st_size > 10000:
            tmp.replace(out)
            return "ok"
        tmp.unlink(missing_ok=True)
        return "none"
    except Exception:
        return "none"


def main():
    dry = "--dry-run" in sys.argv
    online = "--offline" not in sys.argv and not dry
    do_videos = "--no-videos" not in sys.argv and online
    for sub in list(SUBDIR.values()) + ["videos"]:
        (MEDIA / sub).mkdir(parents=True, exist_ok=True)

    shs = sorted(glob.glob(str(ROMS / "*.sh")))
    got = {k: 0 for k in SUBDIR}
    cdn = {k: 0 for k in SUBDIR}
    sgc = {k: 0 for k in SUBDIR}
    vids = 0
    throttled = False
    still_missing = []
    for sh in shs:
        stem = Path(sh).stem
        m = re.search(r"rungameid/(\d+)", Path(sh).read_text())
        if not m:
            continue
        rgid = int(m.group(1))
        is_steam = rgid < 2**32
        src = local_sources(rgid)

        for kind in SUBDIR:
            if src[kind]:
                got[kind] += 1
                if not dry:
                    place(SUBDIR[kind], stem, src[kind])
                continue
            if not online:
                continue
            # Steam CDN (by appid) for Steam games, then SteamGridDB fallback
            # (Steam games by appid, non-Steam by name) when Steam has no art.
            if is_steam and cdn_download(rgid, kind, stem):
                cdn[kind] += 1
                continue
            url = sgdb.art_url(kind, appid=(rgid if is_steam else None),
                               name=(None if is_steam else stem))
            if url and sgdb_download(url, kind, stem):
                sgc[kind] += 1

        if do_videos and not throttled and is_steam and not have_media("videos", stem):
            res = fetch_video(rgid, stem)
            if res == "ok":
                vids += 1
            elif res == "throttled":
                throttled = True
                print("⚠ Steam store API returned 429 (rate limited) — STOPPING video "
                      "fetches to avoid a ban. Re-run later to finish the rest.")

        # report what's still absent after all sources (local + cdn + sgdb)
        miss = [k for k in SUBDIR if not (src[k] or have_media(SUBDIR[k], stem))]
        if miss:
            still_missing.append((stem, miss))

    print(("[dry-run] " if dry else "") +
          f"{len(shs)} games · local " + " ".join(f"{k}:{got[k]}" for k in SUBDIR) +
          (" · cdn " + " ".join(f"{k}:{cdn[k]}" for k in SUBDIR) if online else "") +
          (" · sgdb " + " ".join(f"{k}:{sgc[k]}" for k in SUBDIR) if online and sgdb.available() else "") +
          (f" · videos+{vids}" if do_videos else ""))
    if still_missing:
        print(f"\n{len(still_missing)} game(s) still missing art:")
        for stem, miss in still_missing:
            print(f"  {stem}: {', '.join(miss)}")


if __name__ == "__main__":
    main()
