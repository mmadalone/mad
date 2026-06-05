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
import argparse, json, re, shutil, struct, subprocess, sys, time
import xml.etree.ElementTree as ET
from pathlib import Path

ROMS   = Path("/run/media/deck/1tbDeck/ROMs/openbor")
MED    = Path("/run/media/deck/1tbDeck/downloaded_media/openbor")
GAMELIST = Path.home()/"ES-DE/gamelists/openbor/gamelist.xml"
GRID   = Path.home()/".steam/steam/userdata/109754127/config/grid"
VDF    = Path.home()/".steam/steam/userdata/109754127/config/shortcuts.vdf"
YTDLP  = Path.home()/".local/bin/yt-dlp"
LOG    = MED/"fetch-media.log"
# Steam shortcut folder name differs from the ROM folder for these:
MANUAL_APPID = {"TMNT_RP_1_1_5": 3238248460}

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
    man=(ROMS/f"{stem}.openbor").read_text()
    m=re.search(r'^PREFIX=(.*)$', man, re.M)
    if m:
        base=m.group(1).rsplit("/",1)[-1]
        if base.isdigit(): return int(base)
    dirn=re.search(r'^DIR=(.*)$', man, re.M).group(1)
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
        dst=MED/cat/(stem+src.suffix)
        if dst.exists() and not force: got[cat]="exists"; continue
        shutil.copy2(src,dst); got[cat]="copied"
    return got

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
    pick=pick_video(name)
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
        for c in MED.joinpath("videos").glob(f"{stem}.*"):
            if c.suffix.lower() in (".mkv",".webm",".mp4"):
                c.rename(vdst); break
    if not vdst.exists(): return "dl-failed"
    # screenshot ~20s into the 60s clip
    shot=MED/"screenshots"/f"{stem}.jpg"
    if not shot.exists() or force:
        subprocess.run(["ffmpeg","-y","-loglevel","error","-ss","20","-i",str(vdst),
                        "-vframes","1","-q:v","3",str(shot)], capture_output=True)
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
    a=ap.parse_args()
    gl=games()
    log(f"=== openbor-fetch-media: {len(gl)} games ===")
    # which already have a cover after art pass (to decide placeholder)
    art_summary={}
    if not a.videos_only:
        for stem in gl:
            art_summary[stem]=copy_art(stem, a.force)
        nc=sum(1 for v in art_summary.values() if v.get("covers"))
        log(f"art: {nc}/{len(gl)} games have a Steam cover")
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
