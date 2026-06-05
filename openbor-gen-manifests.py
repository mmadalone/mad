#!/usr/bin/env python3
"""Generate ES-DE .openbor launcher manifests for the OpenBOR ROM folder.

For each game folder under the openbor ROM dir, write a <Folder>.openbor file
that openbor.sh reads (DIR / EXE / PREFIX). The exe is taken from the Steam
shortcut when the game is in Steam (authoritative), else inferred. PREFIX
reuses the game's existing Steam Proton prefix (compatdata/<appid>) when that
prefix actually exists on disk; otherwise it points at the shared OpenBOR
prefix that Proton creates on first launch.

Idempotent: safe to re-run after adding/removing games.
"""
import os
import struct
import sys

HOME = os.path.expanduser("~")
ROM_DIR = "/run/media/deck/1tbDeck/ROMs/openbor"
SHORTCUTS = f"{HOME}/.local/share/Steam/userdata/109754127/config/shortcuts.vdf"
COMPATDATA = f"{HOME}/.local/share/Steam/steamapps/compatdata"
SHARED_PREFIX = f"{HOME}/Emulation/storage/openbor/prefix"

# Some folders ship more than one exe and the renamed/headline one is actually
# an OLD engine build that fails under Proton. Force a specific exe here.
# TMNT_RP ships TMNT_Rescue_Palooza.exe (2014 SDL1.2 build — crashes at video
# init under Proton) alongside the modern OpenBOR.exe (2021 SDL2 build) which
# runs fine, so use the latter.
EXE_OVERRIDE = {
    "TMNT_RP_1_1_5": "OpenBOR.exe",
}


def parse_vdf(path):
    data = open(path, "rb").read()

    def rs(i):
        j = data.index(b"\x00", i)
        return data[i:j].decode("utf-8", "replace"), j + 1

    def parse(i):
        out = {}
        while i < len(data):
            t = data[i]; i += 1
            if t == 0x08:
                return out, i
            k, i = rs(i)
            if t == 0x00:
                v, i = parse(i); out[k] = v
            elif t == 0x01:
                v, i = rs(i); out[k] = v
            elif t == 0x02:
                v = struct.unpack("<i", data[i:i + 4])[0]; i += 4; out[k] = v
            else:
                return out, i
        return out, i

    return parse(0)[0]


def steam_map():
    """folder -> (exe_basename, unsigned_appid)

    Steam stores shortcut keys with inconsistent casing (Exe/exe,
    StartDir/startdir, appid/AppId) depending on the tool that wrote them,
    so look keys up case-insensitively.
    """
    m = {}
    try:
        root = parse_vdf(SHORTCUTS)
    except Exception as e:
        print(f"WARN: could not parse shortcuts.vdf: {e}", file=sys.stderr)
        return m

    def ci(d, *names):
        low = {k.lower(): v for k, v in d.items()}
        for n in names:
            if n.lower() in low:
                return low[n.lower()]
        return None

    for e in root.get("shortcuts", {}).values():
        sd = ci(e, "StartDir") or ""
        if "/openbor" not in sd.lower():
            continue
        folder = sd.rstrip("/").split("/")[-1]
        exe = os.path.basename((ci(e, "Exe") or "").strip().strip('"'))
        appid = ci(e, "appid")
        if appid is not None:
            appid &= 0xFFFFFFFF
        m[folder] = (exe, appid)
    return m


def choose_exe(folder, steam_exe):
    fdir = os.path.join(ROM_DIR, folder)
    exes = sorted(x for x in os.listdir(fdir) if x.lower().endswith(".exe"))
    if folder in EXE_OVERRIDE and os.path.isfile(os.path.join(fdir, EXE_OVERRIDE[folder])):
        return EXE_OVERRIDE[folder]
    if steam_exe and steam_exe in exes:
        return steam_exe
    if steam_exe and os.path.isfile(os.path.join(fdir, steam_exe)):
        return steam_exe
    # not in Steam (or stale): prefer a renamed launcher over generic OpenBOR.exe
    non_generic = [x for x in exes if x.lower() != "openbor.exe"]
    if non_generic:
        return non_generic[0]
    return exes[0] if exes else None


def main():
    sm = steam_map()
    folders = sorted(
        d for d in os.listdir(ROM_DIR)
        if os.path.isdir(os.path.join(ROM_DIR, d))
    )
    print(f"{'FOLDER':<42} {'EXE':<26} PREFIX")
    print("-" * 100)
    for folder in folders:
        steam_exe, appid = sm.get(folder, (None, None))
        exe = choose_exe(folder, steam_exe)
        if not exe:
            print(f"{folder:<42} {'!! NO EXE — skipped':<26}")
            continue
        # prefix: reuse the Steam compatdata prefix only if it really exists
        prefix = SHARED_PREFIX
        kind = "shared"
        if appid is not None:
            cand = os.path.join(COMPATDATA, str(appid))
            if os.path.isdir(cand):
                prefix = cand
                kind = f"reused({appid})"
        manifest = os.path.join(ROM_DIR, f"{folder}.openbor")
        with open(manifest, "w") as f:
            f.write("# ES-DE OpenBOR launcher manifest (read by openbor.sh)\n")
            f.write(f"DIR={folder}\n")
            f.write(f"EXE={exe}\n")
            f.write(f"PREFIX={prefix}\n")
        print(f"{folder:<42} {exe:<26} {kind}")
    os.makedirs(SHARED_PREFIX, exist_ok=True)
    print(f"\nWrote {len(folders)} manifests to {ROM_DIR}")
    print(f"Shared prefix: {SHARED_PREFIX}")


if __name__ == "__main__":
    main()
