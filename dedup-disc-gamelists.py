#!/usr/bin/env python3
"""
De-duplicate disc-based ES-DE gamelists where a game shows twice (its disc file
.cue/.chd/… AND a .m3u playlist) or N+1 times (multi-disc: each disc + the m3u).

Rule (per game, based on what its .m3u references):
  • single-disc (m3u → 1 file): SHOW the disc file, HIDE the redundant .m3u.
  • multi-disc  (m3u → 2+ files): SHOW the .m3u (handles disc swapping), HIDE the disc files.
Uses <hidden>true</hidden> (files stay on disk; needs ES-DE ShowHiddenGames = off,
which is the default). Backs up each gamelist (.bak-<ts>). Idempotent.

Usage: dedup-disc-gamelists.py [system ...]   (default: psx segacd pcenginecd dreamcast)
"""
import sys, re, glob, os, html, time, collections
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.proc_guard import abort_if_esde_running
from lib import fsutil

HOME = os.path.expanduser("~")
ROMROOT = os.path.realpath(os.path.join(HOME, "ROMs"))
TS = time.strftime("%Y%m%d-%H%M%S")


def _setvis(block, hidden):
    has = re.search(r'\n\s*<hidden>\s*true\s*</hidden>', block)
    if hidden and not has:
        return block.replace('</game>', '\t<hidden>true</hidden>\n\t</game>', 1)
    if not hidden and has:
        return re.sub(r'\n\s*<hidden>\s*true\s*</hidden>', '', block)
    return block


def dedup(sysname):
    romd = os.path.join(ROMROOT, sysname)
    gl = os.path.join(HOME, "ES-DE", "gamelists", sysname, "gamelist.xml")
    if not os.path.isfile(gl):
        return f"{sysname}: no gamelist — skip"
    single, multi, single_refs, multi_refs = set(), set(), set(), set()
    for m in glob.glob(os.path.join(romd, "*.m3u")):
        refs = [l.strip() for l in open(m, encoding='utf-8', errors='replace') if l.strip()]
        b = os.path.basename(m)
        if len(refs) <= 1:
            single.add(b); single_refs |= set(refs)
        else:
            multi.add(b); multi_refs |= set(refs)
    if not (single or multi):
        return f"{sysname}: no .m3u files — nothing to dedup"

    t = open(gl, encoding='utf-8').read()
    open(gl + f".bak-{TS}", "w", encoding='utf-8').write(t)
    hid = shown = 0

    def proc(mm):
        nonlocal hid, shown
        block = mm.group(0)
        pm = re.search(r'<path>\./([^<]+)</path>', block)
        if not pm:
            return block
        base = html.unescape(pm.group(1))
        if base.endswith('.m3u'):
            h = base in single
            block = _setvis(block, h); hid += h; shown += (not h)
        elif base in multi_refs:
            block = _setvis(block, True); hid += 1
        elif base in single_refs:
            block = _setvis(block, False); shown += 1
        return block

    new = re.sub(r'\t<game>.*?</game>', proc, t, flags=re.S)
    fsutil.atomic_write(gl, new)
    try:
        ET.parse(gl)
    except ET.ParseError as e:
        return f"{sysname}: ⚠ XML broke ({e}) — restore {gl}.bak-{TS}"

    vis = collections.Counter()
    for m in re.finditer(r'<game>(.*?)</game>', new, re.S):
        if re.search(r'<hidden>\s*true', m.group(1)):
            continue
        nm = re.search(r'<name>([^<]*)</name>', m.group(1))
        if nm:
            vis[html.unescape(nm.group(1))] += 1
    dups = {n: c for n, c in vis.items() if c > 1}
    msg = (f"{sysname}: {len(single)} single-disc (m3u hidden) + {len(multi)} multi-disc "
           f"(m3u kept); hid {hid}, showed {shown}; now {sum(vis.values())} visible, "
           f"{len(dups)} dup-name(s)")
    if dups:
        msg += "\n   ⚠ same-name visible entries (likely DIFFERENT games sharing a name — "
        msg += "rename manually if wanted): " + "; ".join(f"{n} ×{c}" for n, c in dups.items())
    return msg


def main():
    if abort_if_esde_running("rewrite the disc gamelists"):
        sys.exit(1)
    systems = sys.argv[1:] or ["psx", "segacd", "pcenginecd", "dreamcast"]
    for s in systems:
        print(dedup(s))


if __name__ == "__main__":
    main()
