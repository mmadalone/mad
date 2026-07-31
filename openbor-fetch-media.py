#!/usr/bin/env python3
"""
Fetch ES-DE media for the OpenBOR collection.

OpenBOR fan-games aren't in any scraper DB, so media is sourced two ways:

  1. LOCAL Steam grid art — the games were added to Steam, so the user's
     userdata/<id>/config/grid holds cover (`<appid>p`), logo (`<appid>_logo`)
     and hero (`<appid>_hero`) art. Each game's Steam appid is recovered from the
     `.openbor` manifest's PREFIX (compatdata id) or, failing that, by matching
     the game folder against each shortcut's Exe/StartDir path in shortcuts.vdf.
     Copied to downloaded_media/openbor/{covers,marquees,fanart}/<stem>.

  2. VIDEO + SCREENSHOT — a short gameplay segment per game via yt-dlp (search
     "<name> OpenBOR", scored to prefer the actual fan-game longplay), recoded to
     mp4 in downloaded_media/openbor/videos/<stem>.mp4, with a frame extracted to
     screenshots/<stem>.jpg. Coverless games also get that frame as a cover
     placeholder (flagged for manual replacement).

ES-DE finds media by filename stem (the ROM basename), so every output is named
<stem>.<ext>. Idempotent: existing files are skipped. Re-runnable.

Usage: openbor-fetch-media.py [--videos-only] [--art-only] [--force]
"""
from __future__ import annotations
import argparse, glob, hashlib, json, os, re, shutil, struct, subprocess, sys, time
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import esde_settings, sgdb, launchbox  # noqa: E402

ROMS   = Path("/run/media/deck/1tbDeck/ROMs/openbor")
MED    = Path("/run/media/deck/1tbDeck/downloaded_media/openbor")
# esde_settings.APPDATA honors $ESDE_APPDATA_DIR (default ~/ES-DE).
GAMELIST = esde_settings.APPDATA / "gamelists" / "openbor" / "gamelist.xml"
GRID   = Path.home()/".steam/steam/userdata/109754127/config/grid"
VDF    = Path.home()/".steam/steam/userdata/109754127/config/shortcuts.vdf"
YTDLP  = Path.home()/".local/bin/yt-dlp"
LOG    = MED/"fetch-media.log"
# Steam shortcut folder name differs from the ROM folder for these:
MANUAL_APPID = {"TMNT_RP_1_1_5": 3238248460}

# Extra title spellings to try on SteamGridDB, for games whose ES-DE name does not
# match how SGDB spells it. The best-scoring spelling across the list wins; see
# lib/sgdb.find_game. Adding a query here can only ever help — a bad spelling is
# rejected by the similarity gate rather than silently accepted.
#
# DELIBERATE: three of these games are faithful remakes of ONE specific, unambiguous
# arcade/console original (ZVitor's Captain America, X-Men: Mutant Apocalypse and
# Punisher remakes), and SGDB has no entry for the fan version. Falling back to the
# ORIGINAL game's boxart is intended for those, and is why the bare title is listed
# as a second spelling. Do NOT extend this to fan games that merely share a
# franchise (Ultimate Double Dragon, TMNT Recolored, Evil Dead Redux): there is no
# single "the original" for those, so they are left alone rather than given the art
# of whatever the search happens to surface.
SGDB_QUERY = {
    "MFA2":                    ["Marvel First Alliance 2"],
    "Silver_Nights_Crusaders": ["Silver Night's Crusaders", "Silver Nights Crusaders"],
    "Maximun_Carnage_Returns": ["Maximum Carnage Returns"],
    "UDD_ver3.0":              ["Ultimate Double Dragon"],
    "TMNT_Recolored_and_Extended": ["TMNT Recolored and Extended"],
    "CARNAGEv101":             ["Maximum Carnage"],
    "PUNIv1":                  ["The Punisher Arcade Remake", "The Punisher"],
    "CAPAv104":                ["Captain America and The Avengers Remake"],
    "XMEN_MAv1":               ["X-Men Mutant Apocalypse Remake"],
    "MIW_Definitive":          ["Marvel Infinity War"],
}
# SGDB art kind -> (ES-DE media subdir, filename stem suffix)
SGDB_KINDS = {"cover": "covers", "marquee": "marquees", "fanart": "fanart"}

# Title spellings for the LaunchBox OpenBOR catalogue (lib/launchbox). Needed where
# the catalogue spells a game differently from ES-DE — including one entry that is
# simply misspelled upstream ("Shodown Revenge"), which the similarity gate still
# clears at 0.97. Each of these was verified against the game's own files or its
# LaunchBox description before being listed here.
LAUNCHBOX_QUERY = {
    # ZVitor's "Maximum Carnage" (2025) remakes the 1994 16-bit Spider-Man and Venom:
    # Maximum Carnage, and has no fan-game entry of its own, so it takes the ORIGINAL's
    # boxart (same rule as CAPAv104 / PUNIv1 / XMEN_MAv1 above). Pinned explicitly
    # because the bare title "Maximum Carnage" is one edit away from
    # "Maximum Carnage Returns" — a DIFFERENT game by HeatGames that this collection
    # also owns as Maximun_Carnage_Returns. A scraper made exactly that mistake on
    # 2026-08-01 and relabelled this game as the HeatGames one.
    "CARNAGEv101":                 ["Spider-Man and Venom Maximum Carnage"],
    "evildead":                    ["Evil Dead"],            # Thatcher Productions, 2021
    "UDD_ver3.0":                  ["Ultimate Double Dragon"],
    "Neon_Lightning_Force_1.5_demo": ["Neon Lightning Force"],
    "TMNT_Recolored_and_Extended": ["TMNT 8 Bit Recolored and Extended",
                                    "TMNT Recolored and Extended"],
    "showdown_revenge":            ["Shodown Revenge", "Showdown Revenge"],
}

# YouTube search text for the gameplay video, where the game's own name pulls in the
# WRONG game. pick_video scores titles by token overlap, so when one game's name is a
# strict prefix of another's it loses every time: searching "Maximum Carnage OpenBOR"
# ranks "Maximum Carnage RETURNS ..." top, which is HeatGames' separate game (already
# used for Maximun_Carnage_Returns). Naming the game more specifically fixes it.
VIDEO_QUERY = {
    "CARNAGEv101": "Spiderman and Venom Maximum Carnage Remake",
}

def log(m):
    line=f"[{time.strftime('%H:%M:%S')}] {m}"
    print(line, flush=True)
    try: LOG.open("a").write(line+"\n")
    except OSError: pass

def games() -> dict:
    """stem -> curated name, from the gamelist."""
    out={}
    for g in ET.parse(GAMELIST).getroot().findall("game"):
        stem=(g.findtext("path") or "").lstrip("./").rsplit(".",1)[0]
        if stem: out[stem]=g.findtext("name") or stem
    return out

def _vdf_entries():
    raw=VDF.read_bytes()
    ms=list(re.finditer(rb'\x02appid\x00(....)', raw))
    for i,m in enumerate(ms):
        aid=struct.unpack('<I', m.group(1))[0]
        ch=raw[m.end():(ms[i+1].start() if i+1<len(ms) else len(raw))]
        def f(k):
            x=re.search(rb'\x01'+k+rb'\x00([^\x00]*)\x00', ch, re.I)
            return x.group(1).decode('utf-8','replace') if x else ""
        yield aid, f(b'AppName'), f(b'Exe')+" "+f(b'StartDir')

def appid_for(stem) -> int|None:
    if stem in MANUAL_APPID: return MANUAL_APPID[stem]
    try:
        man=(ROMS/f"{stem}.openbor").read_text()
    except (FileNotFoundError, OSError):
        return None     # missing/unreadable manifest -> skip this game, don't abort the batch
    m=re.search(r'^PREFIX=(.*)$', man, re.M)
    if m:
        base=m.group(1).rsplit("/",1)[-1]
        if base.isdigit(): return int(base)
    dm=re.search(r'^DIR=(.*)$', man, re.M)
    dirn=dm.group(1) if dm else ''     # no DIR= line -> skip this game (return None), don't crash the run
    for aid,name,path in _vdf_entries():
        if dirn and dirn in path: return aid
    for aid,name,path in _vdf_entries():
        if name==dirn or name==stem: return aid
    return None

def _grid(aid, suf):
    for x in (".png",".jpg"):
        p=GRID/f"{aid}{suf}{x}"
        if p.is_file(): return p
    return None

def copy_art(stem, force) -> dict:
    aid=appid_for(stem)
    got={}
    if not aid: return got
    for cat,suf in (("covers","p"),("marquees","_logo"),("fanart","_hero")):
        (MED/cat).mkdir(parents=True, exist_ok=True)
        src=_grid(aid,suf)
        if not src: continue
        # Steam's grid folder can hold a PLACEHOLDER rather than real art: Maximum
        # Carnage Returns shipped a 68-byte 1x1 _hero.png, which ES-DE then stretches
        # across the screen as that game's background. Anything this small is not art.
        d=_dims(src)
        if src.stat().st_size < 1024 or (d and min(d) < 32):
            got[cat]=f"skip-placeholder{'' if not d else f' {d[0]}x{d[1]}'}"; continue
        # Match on the STEM, not on one extension: ES-DE picks a media file by stem, so
        # writing <stem>.png beside an existing <stem>.jpg leaves two files fighting
        # over the same slot (and the loser may be the better image).
        have=_existing(cat, stem)
        if have and not force: got[cat]="exists"; continue
        dst=MED/cat/(stem+src.suffix)
        shutil.copy2(src,dst)
        if have and have != dst: _retire(have)
        got[cat]="copied"
    return got

# ── SteamGridDB art (for games with no Steam shortcut) ──
def _existing(cat, stem):
    """The media file for this game in `cat`, whatever its extension."""
    for p in sorted((MED/cat).glob(glob.escape(stem)+".*")):
        if p.suffix.lower() in (".png",".jpg",".jpeg",".webp"): return p
    return None

def _dims(p: Path):
    """(width, height) of an image, via ffprobe (already required for screenshots)."""
    try:
        out=subprocess.run(["ffprobe","-v","error","-select_streams","v",
                            "-show_entries","stream=width,height","-of","csv=p=0",str(p)],
                           capture_output=True, text=True, timeout=20).stdout.strip()
        w,h=out.split(",")[:2]; return int(w),int(h)
    except Exception:
        return None

def cover_is_weak(stem) -> bool:
    """True if this game's cover is not box-art shaped, so it is worth upgrading.

    ES-DE draws the cover in a portrait boxart slot. Two kinds of wrong art end up
    there for OpenBOR games, and BOTH are landscape:
      - the video frame fetch_video() drops in for coverless games (640x360), and
      - small Steam-grid banners some of these games got instead of a capsule
        (396x224 on the ZVitor titles).
    Orientation is the honest test. Byte-comparing the cover against the screenshot
    is NOT — the two are re-extracted independently and differ even when the cover
    plainly is a video frame (verified on all six placeholders, 2026-08-01).
    """
    cov=_existing("covers", stem)
    if not cov: return False
    d=_dims(cov)
    return bool(d and d[0] > d[1])

def sgdb_art(stem, name, force) -> dict:
    """Fill missing cover/marquee/fanart for one game from SteamGridDB.

    Only touches what is actually absent (or a video-frame placeholder), so it can be
    re-run safely and never overwrites better art that Steam already provided.
    """
    want=[]
    for kind,cat in SGDB_KINDS.items():
        have=_existing(cat, stem)
        if force or not have or (kind=="cover" and cover_is_weak(stem)):
            want.append((kind,cat))
    if not want:
        return {}
    hit=sgdb.find_game(SGDB_QUERY.get(stem, [name]))
    if not hit:
        log(f"    sgdb: no confident match for {name!r} — left alone"); return {}
    gid,mname,score,q=hit
    log(f"    sgdb: {mname!r} (id {gid}, match {score:.2f} on {q!r})")
    got={}
    for kind,cat in want:
        item=sgdb.best_item(kind, gid)
        if not item:
            got[kind]="none-on-sgdb"; continue
        w,h=item.get("width") or 0, item.get("height") or 0
        if kind=="cover" and not cover_acceptable(stem, w, h):
            got[kind]=f"skip {w}x{h} not-better"; continue
        dest=_download(item["url"], MED/cat, stem)
        if not dest:
            got[kind]="dl-failed"; continue
        # A placeholder cover kept its .jpg name; drop it if the new art is a .png
        # so ES-DE cannot pick the stale one up instead.
        for old in (MED/cat).glob(glob.escape(stem)+".*"):
            if old != dest and old.suffix.lower() in (".png",".jpg",".jpeg",".webp"):
                _retire(old)
        got[kind]=f"ok {item.get('width')}x{item.get('height')}"
    return got

def cover_acceptable(stem, w, h) -> bool:
    """Is a candidate cover of size w x h an improvement on what this game has?

    Two different situations:
      - the game already has a PROPER (portrait) cover: only take another portrait
        one, never trade down to a landscape image;
      - the game only has a weak cover (a video frame / landscape banner): take
        anything that is not itself landscape, since square real artwork still
        beats a random mid-game frame.
    """
    if not _existing("covers", stem):
        return True                       # nothing to lose
    if not w or not h:
        return False                      # unknown size, existing art is safer
    return w <= h if cover_is_weak(stem) else w < h

def _missing_kinds(stem, force):
    """Which of cover/marquee/fanart this game still needs."""
    out=[]
    for kind,cat in SGDB_KINDS.items():
        if force or not _existing(cat, stem) or (kind=="cover" and cover_is_weak(stem)):
            out.append((kind,cat))
    return out

def launchbox_art(stem, name, force) -> dict:
    """Fill still-missing art from the LaunchBox OpenBOR catalogue.

    Runs after SteamGridDB: LaunchBox is the only source that carries real BOXART
    for these fan games, but it has no API, so it is the slower second pass rather
    than the first thing tried.
    """
    want=_missing_kinds(stem, force)
    if not want: return {}
    hit=launchbox.find(LAUNCHBOX_QUERY.get(stem, [name]))
    if not hit:
        return {}
    slug,mname,score,q=hit
    items=launchbox.images(slug)
    log(f"    launchbox: {mname!r} ({slug}, match {score:.2f} on {q!r})")
    got={}
    for kind,cat in want:
        item=launchbox.best(slug, kind, items)
        if not item:
            got[kind]="none-on-launchbox"; continue
        w,h=item["width"], item["height"]
        if kind=="cover" and not cover_acceptable(stem, w, h):
            got[kind]=f"skip {w}x{h} not-better"; continue
        dest=_download(item["url"], MED/cat, stem)
        if not dest:
            got[kind]="dl-failed"; continue
        for old in (MED/cat).glob(glob.escape(stem)+".*"):
            if old != dest and old.suffix.lower() in (".png",".jpg",".jpeg",".webp"):
                _retire(old)
        got[kind]=f"ok {w}x{h}"
    return got

def _download(url, dest_dir: Path, stem) -> Path|None:
    import urllib.request, urllib.parse
    ext=Path(urllib.parse.urlparse(url).path).suffix.lower()
    if ext not in (".png",".jpg",".jpeg",".webp"): ext=".png"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest=dest_dir/f"{stem}{ext}"; tmp=dest_dir/f".{stem}{ext}.part"
    try:
        # SGDB's CDN 403s urllib's default UA, same as the API itself.
        req=urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as r: data=r.read()
        if len(data)<512: return None      # a few-byte 'image' is an error page
        tmp.write_bytes(data)
        # Same-name replacement still displaces a real file, so retire it first —
        # only now that the new bytes are safely on disk.
        if dest.exists(): _retire(dest)
        tmp.replace(dest); return dest
    except Exception as ex:
        log(f"      download failed: {ex!r}")
        try: tmp.unlink(missing_ok=True)
        except OSError: pass
        return None

def _retire(p: Path):
    """Move a superseded media file aside — never delete the user's files."""
    box=Path.home()/"Downloads/_TMP"/f"{time.strftime('%Y%m%d')}-openbor-media"/"media-replaced"/p.parent.name
    box.mkdir(parents=True, exist_ok=True)
    try: shutil.move(str(p), str(box/p.name)); log(f"      retired {p.name} -> {box}")
    except OSError as ex: log(f"      could not retire {p.name}: {ex!r}")

# ── video ──
_GOOD=["longplay","playthrough","full game","full playthrough","gameplay",
       "walkthrough","1cc","no commentary","complete"]
_BAD =["review","reaction","trailer","how to","install","tutorial","setup",
       "download","top 10","top ten","best openbor","mugen","update","news"]
def _tokens(name): return [t for t in re.split(r'[^a-z0-9]+', name.lower()) if len(t)>2]

def pick_video(name):
    q=f"{name} OpenBOR"
    try:
        out=subprocess.run([str(YTDLP),"--no-warnings","--no-playlist",
            "--print","%(id)s\t%(duration)s\t%(title)s", f"ytsearch6:{q}"],
            capture_output=True, text=True, timeout=120).stdout
    except Exception as ex:
        log(f"  search failed: {ex!r}"); return None
    toks=_tokens(name); best=None; bestscore=-999
    for ln in out.splitlines():
        parts=ln.split("\t")
        if len(parts)<3: continue
        vid,dur,title=parts[0],parts[1],parts[2]
        try: dur=float(dur)
        except ValueError: dur=0
        tl=title.lower(); s=0
        if "openbor" in tl: s+=5
        s+=2*sum(1 for t in toks if t in tl)
        s+=2*sum(1 for k in _GOOD if k in tl)
        s-=4*sum(1 for b in _BAD if b in tl)
        if dur and dur<60: s-=3
        if dur and 90<=dur<=4*3600: s+=1
        if s>bestscore: bestscore=s; best=(vid,dur,title)
    return best

def fetch_video(stem, name, coverless, force) -> str:
    (MED/"videos").mkdir(parents=True, exist_ok=True)
    (MED/"screenshots").mkdir(parents=True, exist_ok=True)
    vdst=MED/"videos"/f"{stem}.mp4"
    if vdst.exists() and not force: return "video-exists"
    pick=pick_video(VIDEO_QUERY.get(stem, name))
    if not pick: return "no-candidate"
    vid,dur,title=pick
    # 60s window starting ~20% in (clamped), or near-start for short clips
    start=min(120, int(dur*0.2)) if dur else 60
    if dur and dur<90: start=2
    sec=f"*{start}-{start+60}"
    log(f"  -> {title!r} ({int(dur)}s) [{vid}] seg {sec}")
    cmd=[str(YTDLP),"--no-warnings","--no-playlist",
         "-f","b[height<=720]/bv*[height<=720]+ba/best",
         "--download-sections",sec,"--force-keyframes-at-cuts","--recode-video","mp4",
         "-o",str(MED/"videos"/f"{stem}.%(ext)s"),
         f"https://www.youtube.com/watch?v={vid}"]
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except Exception as ex:
        return f"dl-error:{ex!r}"
    if not vdst.exists():
        # recode may have produced .mkv/.webm; rename the first match
        for c in MED.joinpath("videos").glob(glob.escape(stem) + ".*"):
            if c.suffix.lower() in (".mkv",".webm",".mp4"):
                c.rename(vdst); break
    if not vdst.exists(): return "dl-failed"
    # screenshot ~20s into the 60s clip
    shot=MED/"screenshots"/f"{stem}.jpg"
    if not shot.exists() or force:
        try:    # screenshot is an optional placeholder; a pathological clip must not hang the batch
            subprocess.run(["ffmpeg","-y","-loglevel","error","-ss","20","-i",str(vdst),
                            "-vframes","1","-q:v","3",str(shot)], capture_output=True, timeout=60)
        except (subprocess.TimeoutExpired, OSError):
            pass
    # coverless game: use the frame as a placeholder cover
    if coverless:
        cov=MED/"covers"/f"{stem}.jpg"
        if shot.exists() and (not cov.exists() or force):
            (MED/"covers").mkdir(parents=True, exist_ok=True)
            shutil.copy2(shot, cov)
    return f"ok:{title[:60]}"

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--videos-only", action="store_true")
    ap.add_argument("--art-only", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--sgdb", action="store_true",
                    help="after the Steam-grid pass, fill still-missing cover/marquee/"
                         "fanart from SteamGridDB (strict name match; needs an API key)")
    ap.add_argument("--launchbox", action="store_true",
                    help="then fill whatever is still missing from the LaunchBox "
                         "OpenBOR catalogue (the only source with real fan-game boxart)")
    ap.add_argument("--only", metavar="STEM", action="append",
                    help="restrict the run to these game stems (repeatable)")
    a=ap.parse_args()
    if not os.path.ismount("/run/media/deck/1tbDeck"):
        print("SD card /run/media/deck/1tbDeck is not mounted — aborting "
              "(refusing to write media to the root partition).")
        return
    gl=games()
    if a.only:
        missing=[s for s in a.only if s not in gl]
        if missing:
            log(f"WARN: not in the gamelist, ignored: {', '.join(missing)}")
        gl={s:n for s,n in gl.items() if s in set(a.only)}
        if not gl:
            log("nothing to do — no requested game is in the gamelist"); return
    log(f"=== openbor-fetch-media: {len(gl)} games ===")
    # which already have a cover after art pass (to decide placeholder)
    art_summary={}
    if not a.videos_only:
        for stem in gl:
            art_summary[stem]=copy_art(stem, a.force)
        nc=sum(1 for v in art_summary.values() if v.get("covers"))
        log(f"art: {nc}/{len(gl)} games have a Steam cover")
        if a.sgdb:
            if not sgdb.available():
                log("sgdb: no API key (set SGDB_API_KEY or ~/.claude/tokens/steamgriddb.md) — skipped")
            else:
                log("sgdb: filling gaps Steam could not")
                for i,(stem,name) in enumerate(sorted(gl.items()),1):
                    res=sgdb_art(stem, name, a.force)
                    if res: log(f"  [{i}/{len(gl)}] {stem}: " +
                                ", ".join(f"{k}={v}" for k,v in res.items()))
        if a.launchbox:
            log("launchbox: filling what is still missing")
            for i,(stem,name) in enumerate(sorted(gl.items()),1):
                res=launchbox_art(stem, name, a.force)
                if res: log(f"  [{i}/{len(gl)}] {stem}: " +
                            ", ".join(f"{k}={v}" for k,v in res.items()))
    if a.art_only:
        log("art-only: done"); return
    coverless=[s for s in gl if not (MED/"covers"/f"{s}.png").exists()
               and not (MED/"covers"/f"{s}.jpg").exists()]
    log(f"videos: fetching for {len(gl)} games ({len(coverless)} coverless -> frame as placeholder)")
    results={}
    for i,(stem,name) in enumerate(sorted(gl.items()),1):
        log(f"[{i}/{len(gl)}] {stem}  ({name})")
        results[stem]=fetch_video(stem, name, stem in coverless, a.force)
        log(f"    {results[stem]}")
    ok=sum(1 for v in results.values() if v.startswith(("ok","video-exists")))
    log(f"=== DONE: video ok {ok}/{len(gl)} ===")
    for s,v in results.items():
        if not v.startswith(("ok","video-exists")): log(f"   MISS {s}: {v}")

if __name__=="__main__":
    main()
