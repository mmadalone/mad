"""Emulator-binary fingerprint: the cheap half of the config-format canary.

WHY THIS EXISTS. MAD has twelve config writers (lib/{bezel,cemu,dolphin,eden,mugen,openbor,
pcsx2,retroarch,rpcs3,sinden,xemu}_cfg.py + lib/madsrv/ryujinx_cfg.py) and every one of them
hardcodes an emulator's on-disk format: Ryujinx's Config.json shape, Cemu's controllerN.xml
`<uuid>` prefix semantics and `<mapping>` id ranges, the Yuzu-fork qt-config.ini [Controls]
block. NOT ONE of them reads or validates a schema version (audit 2026-08-12 confirmed this by
exhaustive grep), and this class of break has already bitten twice for real: Ryujinx changed
what an SDL id MEANS between SDL2 and SDL3, and Cemu changed its per-GUID ordinal. Neither was
detected by anything. The first symptom both times was a silently wrong input config at game
launch, on a device whose owner cannot debug it.

WHAT THIS DOES, AND DELIBERATELY DOES NOT DO. It answers exactly one question: "did an
emulator binary change since we last looked?" It makes NO claim that the format actually
moved. That is the point. Reading each emulator's real schema would mean authoring a
per-emulator registry and keeping it in lockstep with those twelve writers forever, which is
the maintenance trap the audit flagged; a whole-directory fingerprint needs no table at all
and covers emulators nobody has integrated yet.

COST. Measured on this Deck: 0.19 ms for all AppImages plus every flatpak, with ZERO
subprocesses (os.stat and os.readlink only). For comparison `flatpak info --show-commit` costs
21 ms PER APP and returns the very commit hash this reads straight out of the ostree symlink,
and `flatpak list` costs 24 ms. This runs at ES-DE start, so that difference matters.

AppImages are fingerprinted by (size, mtime) rather than a hash: hashing 14 files totalling
~800 MB at every ES-DE start is not affordable, and (size, mtime) is the same signal
deck-post-update.sh:203 already uses for the ES-DE AppImage itself. It false-positives on a
`touch` or a re-download of an identical build; that is an acknowledged, harmless direction to
err in for a canary whose only action is to ask the owner to re-check.

Stdlib only apart from lib.mad_paths, so it imports in every context MAD runs in.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from . import mad_paths

# Where an emulator binary can live on this class of machine. AppImages are what EmuDeck drops
# in ~/Applications (Ryujinx, Cemu, Eden, Citron, RPCS3, PCSX2, ...); the flatpak roots are
# where Discover updates Dolphin, RetroArch, xemu, Flycast, MAME, PPSSPP.
APPIMAGE_DIR = "Applications"
FLATPAK_ROOTS = (".local/share/flatpak/app", "/var/lib/flatpak/app")

# MAD's OWN delivery artefacts live in the same directory as the emulators and must be excluded, or
# the canary fires on the most frequent update event on this machine: MAD updating itself.
# deck-fetch-esde.sh writes ES-DE-MAD.AppImage on every in-app update, and ES-DE.AppImage is the
# generated launch wrapper that deck-post-update.sh regenerates. Either one would open a dialog
# headed "Emulators were updated" telling the owner to re-check a controller setup that did not
# change, which is exactly the "trains the owner to dismiss it" failure this canary exists to avoid.
SELF_PREFIXES = ("ES-DE", "EmuDeck")

# Flatpaks that are not emulators. This list is a convenience, not a contract: a name missing from
# it costs one acknowledged dialog after that app updates, never a wrong answer, so it does NOT need
# to be kept exhaustive (the whole point of the whole-directory sweep is that it needs no registry).
NON_EMULATOR_FLATPAKS = frozenset({
    "com.spotify.Client", "org.mozilla.firefox", "tv.kodi.Kodi", "tv.plex.PlexHTPC",
    "net.lutris.Lutris", "net.davidotek.pupgui2", "io.github.peazip.PeaZip",
    "org.gnome.baobab", "io.github.flattool.Warehouse", "com.github.tchx84.Flatseal",
    "com.usebottles.bottles", "org.prismlauncher.PrismLauncher", "io.github.dvlv.boxbuddyrs",
})


def state_path() -> Path:
    """The recorded fingerprint. Lives under storage/control-panel (NOT in the launchers git
    clone: a new dotfile there has to be added to install.sh's dirty-clone filter, .gitignore
    and its test in lockstep or a dirty clone blocks the installer's git pull). A .json here is
    also covered by the system backup for free, via lib/system_map.py's control-panel glob."""
    return mad_paths.storage("control-panel") / "emu-fingerprint.json"


def _appimages(home: Path) -> dict:
    out: dict = {}
    d = home / APPIMAGE_DIR
    try:
        entries = sorted(d.iterdir())
    except OSError:
        return out
    for p in entries:
        if p.suffix != ".AppImage" or p.name.startswith(SELF_PREFIXES):
            continue
        try:
            st = p.stat()                       # follows the ES-DE.AppImage wrapper symlink, fine
        except OSError:
            continue
        out[f"appimage:{p.name}"] = f"{st.st_size}:{int(st.st_mtime)}"
    return out


def _flatpaks(home: Path, roots=FLATPAK_ROOTS) -> dict:
    """{flatpak:<app id>: <ostree commit>} read straight from <root>/<id>/current/active, which
    is a symlink whose target basename IS the deployed commit. This is what
    `flatpak info --show-commit` prints, without paying 21 ms per app for it.

    `roots` is a parameter and not a module constant read directly because ONE of the defaults is
    an ABSOLUTE path (/var/lib/flatpak/app, the system installation): redirecting $HOME alone does
    NOT isolate this function, so a test that only faked a home would silently fingerprint the real
    machine and pass for the wrong reason. Callers that want isolation pass their own roots."""
    out: dict = {}
    for root in roots:
        base = Path(root) if root.startswith("/") else home / root
        try:
            ids = sorted(base.iterdir())
        except OSError:
            continue
        for app in ids:
            if app.name in NON_EMULATOR_FLATPAKS:
                continue
            try:
                target = os.readlink(app / "current" / "active")
            except OSError:
                continue
            out[f"flatpak:{app.name}"] = os.path.basename(target)
    return out


def fingerprint(home: Path | None = None, roots=FLATPAK_ROOTS) -> dict:
    """{key: opaque version token} for every emulator-capable binary we can see cheaply."""
    home = Path(home) if home is not None else Path.home()
    fp = _appimages(home)
    fp.update(_flatpaks(home, roots))
    return fp


def load(path: Path | None = None) -> dict | None:
    """The recorded fingerprint, or None when there is none yet / it is unreadable. A corrupt
    file reads as "no record", which makes the next check re-record silently rather than nag
    about every emulator on the machine."""
    p = path or state_path()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def save(fp: dict, path: Path | None = None) -> bool:
    """Record `fp`. Best-effort: a failure here must never break an ES-DE start, it only means
    the same change is reported again next time."""
    p = path or state_path()
    try:
        from . import fsutil
        p.parent.mkdir(parents=True, exist_ok=True)
        fsutil.atomic_write_json(p, fp)
        return True
    except Exception:
        return False


def changed(old: dict, new: dict) -> list:
    """Human-readable names whose version token moved, appeared or vanished, sorted. The
    "<id>" prefix is stripped for display: the owner should read "Ryujinx.AppImage", not
    "appimage:Ryujinx.AppImage"."""
    names = []
    for key in sorted(set(old) | set(new)):
        if old.get(key) != new.get(key):
            names.append(key.split(":", 1)[1] if ":" in key else key)
    return names


def check(path: Path | None = None, home: Path | None = None, roots=FLATPAK_ROOTS) -> list:
    """The canary. Returns the list of changed emulator names, and records the new fingerprint
    when there was nothing to compare against.

    FIRST RUN RECORDS SILENTLY. Without that, a fresh install would open a dialog listing every
    emulator on the machine, which trains the owner to dismiss the one warning that matters.
    A real change is NOT recorded here: recording is the acknowledgement, and the caller does it
    only after the owner has actually seen the dialog. That is the same rule the fork's
    script-skew snooze uses (MadScripts.h): the stored key IS the state, never a bare
    "dismissed" boolean, so the warning re-arms by itself and cannot be silenced into the bug it
    exists to catch."""
    now = fingerprint(home, roots)
    old = load(path)
    if old is None:
        save(now, path)
        return []
    return changed(old, now)


def main(argv=None) -> int:
    """CLI for mad-drift-check.sh. Always exits 0: a canary must never fail a launch.

        check    print one changed emulator name per line (nothing = nothing changed)
        record   record the current fingerprint (the owner acknowledged the dialog)
    """
    args = list(sys.argv[1:] if argv is None else argv)
    cmd = args[0] if args else "check"
    if cmd == "record":
        save(fingerprint())
        return 0
    if cmd == "check":
        for name in check():
            print(name)
        return 0
    print(f"usage: {Path(sys.argv[0]).name} check|record", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
