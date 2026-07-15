#!/usr/bin/env python3
"""steam-collection-sync.py — keep the ES-DE 'steam' collection installed-only.

Prunes launchers for Steam games that are NO LONGER installed, moving each .sh
plus its <game> block to a recoverable _TMP — preserving scraped metadata for
everything that stays (unlike re-running steam-collection-gen.py, which rewrites
the whole list and clobbers metadata).

Also reports non-Steam shortcuts whose target exe is missing (dead links) — it does
NOT remove those; that's your call.

  steam-collection-sync.py            # apply (ES-DE must be closed)
  steam-collection-sync.py --check    # dry run, change nothing

To ADD newly-installed curated games, run steam-collection-gen.py (re-fetches metadata).
Installed-set is read authoritatively from libraryfolders.vdf across EVERY library/mount.
"""
import re, sys, glob, time, shutil, subprocess
from pathlib import Path
from xml.sax.saxutils import escape
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.proc_guard import abort_if_esde_running  # noqa: E402
from lib import fsutil, esde_settings  # noqa: E402

HOME = Path.home()
STEAM_ROMS = Path("/run/media/deck/1tbDeck/ROMs/steam")
# esde_settings.APPDATA honors $ESDE_APPDATA_DIR (default ~/ES-DE) so a relocated
# ES-DE install trims the gamelist ES-DE actually reads (else synced games vanish).
GL = esde_settings.APPDATA / "gamelists" / "steam" / "gamelist.xml"
TMP_BASE = Path("/run/media/deck/1tbDeck")
CHECK = "--check" in sys.argv or "-n" in sys.argv


def installed_appids():
    """Authoritative installed Steam appids: libraryfolders.vdf -> every library's appmanifests."""
    paths = set()
    for lf in (HOME / ".steam/steam/steamapps/libraryfolders.vdf",
               HOME / ".local/share/Steam/steamapps/libraryfolders.vdf"):
        if lf.exists():
            paths.update(re.findall(r'"path"\s+"([^"]+)"', lf.read_text(errors="replace")))
    paths.update([str(HOME / ".local/share/Steam"), str(HOME / ".steam/steam")])
    apps = set()
    for p in paths:
        sa = Path(p) / "steamapps"
        if sa.exists():
            for acf in sa.glob("appmanifest_*.acf"):
                m = re.search(r'"appid"\s+"(\d+)"', acf.read_text(errors="replace"))
                if m:
                    apps.add(int(m.group(1)))
    return apps


def shortcut_exes():
    """normalized AppName -> resolved exe path, from the non-Steam shortcuts.vdf."""
    out = {}
    for sc in glob.glob(str(HOME / ".steam/steam/userdata/*/config/shortcuts.vdf")) \
            + glob.glob(str(HOME / ".local/share/Steam/userdata/*/config/shortcuts.vdf")):
        data = Path(sc).read_bytes()
        names = re.findall(rb"\x01[Aa]pp[Nn]ame\x00([^\x00]*)\x00", data)
        exes = re.findall(rb"\x01[Ee]xe\x00([^\x00]*)\x00", data)
        for n, e in zip(names, exes):
            ex = e.decode("utf-8", "replace").strip()
            ex = ex[1:].split('"', 1)[0] if ex.startswith('"') else ex.split(" ", 1)[0]  # drop launch opts
            out[re.sub(r"[^a-z0-9]", "", n.decode("utf-8", "replace").lower())] = ex
    return out


def remove_block(text, shname):
    idx = text.find(f"<path>./{escape(shname)}</path>")
    if idx < 0:
        return text, None
    start = text.rfind("<game>", 0, idx)
    end = text.find("</game>", idx)
    if start < 0 or end < 0:
        return text, None
    end += len("</game>")
    ls = text.rfind("\n", 0, start) + 1
    le = text.find("\n", end)
    le = le + 1 if le >= 0 else end
    return text[:ls] + text[le:], text[ls:le]


def main():
    if not STEAM_ROMS.exists():
        print(f"steam roms dir not found: {STEAM_ROMS}")
        return 1
    if not CHECK and abort_if_esde_running("prune the Steam collection"):
        return 1

    installed = installed_appids()
    prune, keep, nonsteam = [], [], []
    for sh in sorted(STEAM_ROMS.glob("*.sh")):
        m = re.search(r"rungameid/(\d+)", sh.read_text(errors="replace"))
        if not m:
            continue
        rg = int(m.group(1))
        if rg >= 2**32:
            nonsteam.append(sh)
        elif rg in installed:
            keep.append(sh)
        else:
            prune.append((sh, rg))

    print(f"installed Steam games: {len(installed)}")
    print(f"collection: {len(keep)} installed | {len(nonsteam)} non-Steam | {len(prune)} uninstalled (to prune)")

    exes = shortcut_exes()
    norm = lambda s: re.sub(r"[^a-z0-9]", "", s.lower())
    dead = []
    for sh in nonsteam:
        s = norm(sh.stem)
        ex = exes.get(s) or next((v for k, v in exes.items() if s and (s in k or k in s)), None)
        if ex and not Path(ex).exists():
            dead.append((sh.stem, ex))
    if dead:
        print("\n⚠ non-Steam shortcuts with a MISSING target (dead links — NOT touched, your call):")
        for nm, ex in dead:
            print(f"   - {nm}  ->  {ex}")

    if not prune:
        print("\nnothing to prune — collection is already installed-only.")
        return 0
    print(f"\n{'[CHECK] would prune' if CHECK else 'pruning'}:")
    for sh, _ in prune:
        print(f"   - {sh.stem}")
    if CHECK:
        print("\n(dry run — re-run without --check to apply)")
        return 0

    text = GL.read_text(encoding="utf-8")
    removed = []
    for sh, _ in prune:
        text, blk = remove_block(text, sh.name)
        if blk:
            removed.append(blk)
    try:
        ET.fromstring(text)
    except Exception as ex:
        print(f"ABORT — edited gamelist invalid: {ex}. Nothing changed.")
        return 1

    ts = time.strftime("%Y%m%d-%H%M%S")
    tmp = TMP_BASE / f"_TMP_steam-sync-{ts}"
    tmp.mkdir(parents=True, exist_ok=True)
    GL.with_name(f"gamelist.xml.bak-sync-{ts}").write_text(GL.read_text(encoding="utf-8"), encoding="utf-8")
    fsutil.atomic_write(GL, text)
    for sh, _ in prune:
        shutil.move(str(sh), str(tmp / sh.name))
    (tmp / "removed-gamelist-entries.xml").write_text("\n".join(removed), encoding="utf-8")
    (tmp / "manifest.txt").write_text("\n".join(f"{sh.name}\t{rg}" for sh, rg in prune), encoding="utf-8")
    (tmp / "RECOVERY.txt").write_text(
        f"steam-collection-sync pruned {len(prune)} uninstalled Steam launchers ({ts}).\n"
        "Restore one: move its .sh back to the steam roms dir and re-add its <game> block\n"
        "(removed-gamelist-entries.xml) to the steam gamelist with ES-DE closed. Media left in place.\n",
        encoding="utf-8")
    print(f"\npruned {len(prune)} -> {tmp}")
    print(f"gamelist backup: gamelist.xml.bak-sync-{ts} (metadata preserved for everything kept)")
    print("restart ES-DE to see the trimmed list.")
    return 0


sys.exit(main())
