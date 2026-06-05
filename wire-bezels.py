#!/usr/bin/env python3
"""
Wire RetroArch bezels for games missing them, three tiers:
  T1 exact/fuzzy name-match to the system's installed bezel pack (overlays/GameBezels/<pack>)
  T2 base-game match for translations/romhacks (norm() already strips English/Hack/vX tags)
  T3 generic per-system console bezel (overlays/pegasus/<x>) via a content-dir override

Only applies to libretro-core systems (RetroArch overlays). Per-game override lives in
config/<CORE>/<romstem>.cfg; content-dir override in config/<CORE>/<systemfolder>.cfg.
Bezels are written into EVERY core dir the system actually uses (detected by match-rate,
so cross-system name collisions don't create phantom cores). Dry-run unless --apply.
"""
import re, sys, unicodedata
from pathlib import Path

RA=Path("/home/deck/.var/app/org.libretro.RetroArch/config/retroarch")
CFG=RA/"config"; BEZ=RA/"overlays"/"GameBezels"; PEG=RA/"overlays"/"pegasus"; ROMS=Path("/home/deck/ROMs")
APPLY="--apply" in sys.argv
ROM_EXT={".zip",".7z",".sfc",".smc",".fig",".bs",".st",".md",".gen",".smd",".nes",".fds",
 ".pce",".cue",".chd",".iso",".32x",".gg",".sms",".n64",".z64",".v64",".cdi",".gdi",".hdm",".m3u"}
TRACK=re.compile(r"\(Track\s*\d+\)", re.I)  # CD track files aren't launchable games

# system -> (bezel pack folder or None, generic pegasus file or None, hardcoded core fallback if undetected)
SNES_CORES=["Snes9x - Current","bsnes","Snes9x","Snes9x 2010"]
MD_CORES=["Genesis Plus GX","BlastEm","PicoDrive"]
NES_CORES=["Nestopia","Mesen","FCEUmm"]
SYS={
 "snes":("SNES","snes.cfg",SNES_CORES), "sfc":("SNES","snes.cfg",SNES_CORES),
 "snesh":("SNES","snes.cfg",SNES_CORES), "snesmsu1":("SNES","snes.cfg",SNES_CORES),
 "genesis":("Megadrive","megadrive.cfg",MD_CORES), "megadrive":("Megadrive","megadrive.cfg",MD_CORES),
 "genh":("Megadrive","megadrive.cfg",MD_CORES),
 "segacd":("Sega CD","segacd.cfg",["Genesis Plus GX","PicoDrive"]),
 "sega32x":("Sega32X","sega32x.cfg",["PicoDrive","Genesis Plus GX"]),
 "mastersystem":("MasterSystem","mastersystem.cfg",["Genesis Plus GX","Gearsystem","PicoDrive"]),
 "gamegear":("GameGear","gg.cfg",["Genesis Plus GX","Gearsystem"]),
 "nes":("NES","nes.cfg",NES_CORES), "famicom":("Famicom","famicom.cfg",NES_CORES),
 "pcengine":("PC Engine","pcengine.cfg",["Beetle PCE","Beetle PCE Fast","Beetle SuperGrafx"]),
 "pcenginecd":("PC Engine","pcenginecd.cfg",["Beetle PCE","Beetle PCE Fast"]),
 "pcfx":("PCFX","pcfx.cfg",["Beetle PC-FX"]),
 "supergrafx":("SuperGrafx","SuperGrafx.cfg",["Beetle SuperGrafx","Beetle PCE"]),
 "saturn":("Saturn","saturn.cfg",["Beetle Saturn","Kronos","YabaSanshiro"]),
 "n64":("Nintendo 64","N64.cfg",["Mupen64Plus-Next","ParaLLEl N64"]),
 "fba":("MAME",None,["FB Alpha 2012","FinalBurn Neo"]),
 "dreamcast":("Dreamcast","Dreamcast.cfg",["Flycast"]),
 "3do":("3DO","3DO.cfg",["Opera"]),
}

def norm(s):
    s=unicodedata.normalize("NFKD",s).encode("ascii","ignore").decode().lower()
    s=re.sub(r"\(.*?\)|\[.*?\]"," ",s)
    s=re.sub(r"\b(english|translated|translation|hack|sample|proto|beta|unl|v\d[\d.]*)\b"," ",s)
    s=re.sub(r"[^a-z0-9]+"," ",s).strip()
    s=re.sub(r"\b(the|a|of|and|in)\b"," ",s)
    return " ".join(s.split())

def detect_cores(stems, fallback):
    """core dirs where >=15% of this system's rom stems already have a cfg (robust to name collisions)."""
    sample=list(stems)[:300]; out=[]
    for d in CFG.iterdir():
        if not d.is_dir(): continue
        hits=sum(1 for s in sample if (d/f"{s}.cfg").exists())
        if sample and hits/len(sample)>=0.15: out.append((d.name,hits))
    detected=[c for c,_ in sorted(out,key=lambda x:-x[1])]
    # union with hardcoded fallback (ordered: detected first, then any fallback dirs that exist)
    for c in fallback:
        if c not in detected and (CFG/c).exists() or c in fallback and c not in detected:
            detected.append(c)
    return detected or [c for c in fallback]

systems=[a for a in sys.argv[1:] if not a.startswith("-")] or list(SYS)
print(f"{'system':<12}{'roms':>5}{'have':>6}{'T1':>5}{'T2':>5}{'T3':>6}  cores")
for system in systems:
    if system not in SYS: continue
    pack,generic,fb=SYS[system]
    rd=ROMS/system
    if not rd.is_dir(): continue
    roms=[p for p in rd.iterdir() if p.is_file() and p.suffix.lower() in ROM_EXT and not TRACK.search(p.name)]
    stems={p.stem for p in roms}
    cores=detect_cores(stems,fb)
    default=cores[0] if cores else (fb[0] if fb else None)
    bez={}
    if pack and (BEZ/pack).is_dir():
        for b in (BEZ/pack).glob("*.cfg"): bez.setdefault(norm(b.stem),b.stem)
    gpath=PEG/generic if generic and (PEG/generic).is_file() else None
    have=t1=t2=t3=0
    wrote_generic=False
    for rp in roms:
        stem=rp.stem
        if any((CFG/c/f"{stem}.cfg").is_file() for c in cores): have+=1; continue
        m=bez.get(norm(stem))
        if m:
            t1+=1
            if APPLY:
                body=(f'# wire-bezels (T1)\ninput_overlay = "{BEZ}/{pack}/{m}.cfg"\n'
                      'input_overlay_enable = "true"\ninput_overlay_opacity = "1.000000"\n'
                      'video_fullscreen = "true"\naspect_ratio_index = "22"\n')
                for c in cores:
                    (CFG/c).mkdir(parents=True,exist_ok=True); (CFG/c/f"{stem}.cfg").write_text(body)
        else:
            t3+=1
            if APPLY and gpath and default and not wrote_generic:
                for c in cores:
                    (CFG/c).mkdir(parents=True,exist_ok=True)
                    tgt=CFG/c/f"{system}.cfg"
                    if tgt.exists(): continue   # never clobber an existing content-dir override
                    tgt.write_text(
                        f'# wire-bezels generic (T3)\ninput_overlay = "{gpath}"\n'
                        'input_overlay_enable = "true"\ninput_overlay_opacity = "1.000000"\n')
                wrote_generic=True
    print(f"{system:<12}{len(roms):>5}{have:>6}{t1:>5}{t2:>5}{t3:>6}  {cores[:3]}{'+gen' if gpath else ' NOGEN'}")
print("\n"+("APPLIED" if APPLY else "DRY RUN — add --apply"))
