"""Wii Remote SOURCE decider + Classic-Controller launch rail -- the SOLE writer of WiimoteNew.ini.

Called at Wii game-start by hooks/game-start/dolphin-wii-mode.sh
    python3 -m lib.dolphin_wii_source apply "<rom>"
and reverted at game-end by hooks/game-end/dolphin-wii-cc-restore.sh
    python3 -m lib.dolphin_wii_source restore

One decision per launch, made AFTER sweeping any crashed-CC leftover:

    DolphinBar present            -> real / real2 by connected-remote count (lightgun AND non-lightgun)
    no bar, lightgun collection   -> Sinden (Source flip; the sweep + a contamination guard heal the body)
    no bar, CC-capable or forced  -> Classic Controller (docked: pads->players | handheld: the Deck)
    no bar, otherwise             -> real  (today's behavior; the router shows the "no remote" warning)

"Forced" = a per-game override, `[backends.dolphin_wii.pergame.<GameID>].force_cc = true`, for a
data-gap game GameTDB has no CC record of (e.g. WiiWare like Retro City Rampage). It is consulted only
in the no-bar branch, so it applies to BOTH docked-no-bar and handheld. (It replaces the old global
`[backends.dolphin].cc_overrides` allowlist, which is retired.)

Only the CC branch is TRANSIENT: it snapshots WiimoteNew.ini.cc-backup and the game-end hook reverts
it. Because the snapshot is written BEFORE the CC bodies, a CC body in the gun slots ALWAYS implies a
consumable backup -- so the crash-orphan sweep (run first, every launch) restores the FULL resting gun
body (the on-disk Sinden profiles are only partial subsets, so we never rebuild from them except as a
last resort when contamination is detected without a backup).

Byte-safe: only targeted [WiimoteN] bodies are touched (block copy, lib.dolphin_wii_profiles); the
snapshot is a whole-file copy; atomic writes; Dolphin is closed at game-start. Everything degrades to
"leave the resting config" on any error -- the launch always continues.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from lib import deck_state, devices, dolphin_profiles, dolphin_wii_profiles, dolphin_wii_tdb
from lib.policy import load_merged

_DIR = Path.home() / ".var/app/org.DolphinEmu.dolphin-emu/config/dolphin-emu"
_FILE = _DIR / "WiimoteNew.ini"
_BACKUP = _DIR / "WiimoteNew.ini.cc-backup"       # transient snapshot (CC branch only)
_TOOL = Path(__file__).resolve().parent.parent / "dolphin-wii-mode.sh"   # the Source-only writer
_HANDHELD_DEFAULT = "Steamdeck = classic controller"
_SINDEN_P1 = "Sinden Lightgun P1"
_SINDEN_P2 = "Sinden Lightgun P2"
_CLASSIC_MARK = re.compile(r'(?mi)^(Extension[ \t]*=[ \t]*Classic\b|Classic/)')


def _be() -> dict:
    """[backends.dolphin] -- real2 threshold + the DolphinBar tool config (shared with route())."""
    be = (load_merged().get("backends") or {}).get("dolphin")
    return be if isinstance(be, dict) else {}


def _be_wii() -> dict:
    """[backends.dolphin_wii] -- the Classic Controller prefs (handheld undocked_profile, pads
    priority). SAME table the editor page (dolphin_wii_pads_cmds) reads/writes, so the handheld
    profile the decider loads matches what the user set."""
    be = (load_merged().get("backends") or {}).get("dolphin_wii")
    return be if isinstance(be, dict) else {}


def _read() -> str | None:
    try:
        return _FILE.read_text(encoding="utf-8", errors="replace") if _FILE.is_file() else None
    except OSError:
        return None


def _atomic_write(text: str) -> None:
    tmp = _FILE.with_suffix(_FILE.suffix + ".cc-tmp")
    tmp.write_text(text, encoding="utf-8", newline="")     # verbatim (preserve line endings)
    tmp.replace(_FILE)


def _is_docked() -> bool:
    """Physical dock/display state (deck_state), honoring the [handheld] force override. Fail-safe:
    docked on any error (-> the pads->players branch, which no-ops unless a priority is set)."""
    try:
        hh = load_merged().get("handheld")
        return deck_state.is_docked(deck_state.resolve_force(hh if isinstance(hh, dict) else None))
    except Exception:
        return True


def _is_lightgun(rom: str) -> bool:
    """True iff the ROM belongs to a require_sinden (lightgun) collection -- the same check the
    router's `lightgun-rom` mode uses. Fail-safe False (a gun game is never GameTDB-CC-capable, so it
    then lands on `real`, matching today's lightgun-rom failure fallback)."""
    try:
        from lib import es_collections as colls
        name = colls.collection_for_rom(str(rom))
        if not name:
            return False
        ent = (load_merged().get("collections") or {}).get(name) or {}
        return bool(ent.get("require_sinden"))
    except Exception:
        return False


# --------------------------------------------------------------------------- transient restore (CC)
def restore(logger=None) -> bool:
    """Revert a transient CC swap: copy the snapshot back over WiimoteNew.ini and drop it. No-op
    (False) when no snapshot exists. Idempotent -- safe at game-end AND as the crash-orphan sweep."""
    if not _BACKUP.is_file():
        return False
    try:
        tmp = _FILE.with_suffix(_FILE.suffix + ".cc-tmp")
        tmp.write_bytes(_BACKUP.read_bytes())
        tmp.replace(_FILE)
        _BACKUP.unlink()
        if logger:
            logger.info("dolphin_wii: restored resting WiimoteNew.ini after Classic Controller game")
        return True
    except OSError as ex:
        if logger:
            logger.warning(f"dolphin_wii: CC restore failed: {ex!r}")
        return False


def _snap_write(new_text: str, logger, msg: str) -> None:
    """Snapshot the resting WiimoteNew.ini (only when no backup survives -- apply() guarantees that)
    then write the transient CC swap. Never truncates (whole-file snapshot; temp+replace)."""
    try:
        if not _BACKUP.is_file():
            _BACKUP.write_bytes(_FILE.read_bytes())
        _atomic_write(new_text)
    except OSError as ex:
        if logger:
            logger.warning(f"dolphin_wii: could not apply Classic Controller: {ex!r}")
        return
    if logger:
        logger.info(f"dolphin_wii: {msg}")


# --------------------------------------------------------------------------- source modes
def _run_tool(mode: str, logger) -> None:
    """Delegate a real/real2/sinden Source flip to the existing single-writer tool. Source-only is
    correct for real (Source=2 makes Dolphin use the physical remote and ignore the emulated body)
    and for sinden here (the crash sweep + contamination guard keep the gun BODY correct).

    CRITICAL: the tool prints status lines to STDOUT, so we CAPTURE its output (never let it inherit
    our stdout). The game-start hook reads this process's stdout as the chosen mode -- a leaked tool
    banner would corrupt `$mode` and stop the real-Wiimote quit-watcher from starting. The captured
    output is forwarded to the logger (stderr -> the hook's log)."""
    if not _TOOL.is_file():
        if logger:
            logger.warning(f"dolphin_wii: {_TOOL} not found; leaving WiimoteNew.ini untouched")
        return
    try:
        r = subprocess.run([str(_TOOL), mode], check=False,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        if logger:
            out = (r.stdout or "").strip().replace("\n", " | ")
            logger.info(f"dolphin_wii: source mode {mode!r}" + (f" ({out})" if out else ""))
    except OSError as ex:
        if logger:
            logger.warning(f"dolphin_wii: failed to run {_TOOL} {mode}: {ex!r}")


def _cc_contaminated(text: str) -> bool:
    """True iff [Wiimote1] or [Wiimote2] still holds Classic-Controller lines -- i.e. a CC body
    survived into a gun launch WITHOUT a consumable backup (the invariant was broken)."""
    for slot in ("Wiimote1", "Wiimote2"):
        body = dolphin_profiles._section_body(text, slot) or ""
        if _CLASSIC_MARK.search(body):
            return True
    return False


def _apply_sinden(logger) -> None:
    """Lightgun launch: normally just the Source flip -- the crash-orphan sweep (run first) has
    already reverted any CC leftover to the FULL resting gun body, so a Source-only flip keeps the
    rich live mapping intact. Last resort: if the gun slots STILL look like Classic Controller (the
    backup invariant was somehow broken), rebuild [Wiimote1/2] from the on-disk gun profiles --
    incomplete, but far better than dead guns."""
    text = _read()
    if text is not None and _cc_contaminated(text):
        changed = False
        for slot, prof in ((1, _SINDEN_P1), (2, _SINDEN_P2)):
            body = dolphin_wii_profiles.profile_body(prof)
            if body is None:
                continue
            nt = dolphin_wii_profiles.apply_cc_body(text, f"Wiimote{slot}", body)
            if nt is not None:
                text, changed = nt, True
        if changed:
            try:
                _atomic_write(text)
                if logger:
                    logger.warning("dolphin_wii: gun slots held Classic bindings; rebuilt from gun profiles")
            except OSError as ex:
                if logger:
                    logger.warning(f"dolphin_wii: gun-slot rebuild failed: {ex!r}")
    _run_tool("sinden", logger)


def _apply_cc(logger) -> None:
    """No-bar Classic Controller (transient). Docked -> the pads->players profile priority across
    [Wiimote1..4]; handheld -> the single Deck profile on [Wiimote1] (2..4 off)."""
    from lib import dolphin_wii_pads
    text = _read()
    if text is None:
        if logger:
            logger.warning("dolphin_wii: WiimoteNew.ini missing; skipping Classic Controller")
        return
    if _is_docked():
        new_text, applied = dolphin_wii_pads.assign_text(text)
        if not applied:
            if logger:
                logger.info("dolphin_wii: docked, no Classic Controller pad matched; leaving resting")
            return
        _snap_write(new_text, logger,
                    "docked Classic Controller -> "
                    + ", ".join(f"P{s}={n!r}" for s, n in applied) + " (transient)")
        return
    profile = str(_be_wii().get("undocked_profile", _HANDHELD_DEFAULT) or _HANDHELD_DEFAULT)
    body = dolphin_wii_profiles.profile_body(profile)
    if body is None:
        if logger:
            logger.warning(f"dolphin_wii: handheld CC profile {profile!r} not found; leaving resting")
        return
    nt = dolphin_wii_profiles.apply_cc_body(text, "Wiimote1", body)
    if nt is None:
        if logger:
            logger.warning("dolphin_wii: [Wiimote1] absent; skipping Classic Controller")
        return
    for slot in (2, 3, 4):
        nt = dolphin_wii_profiles.disable_slot(nt, f"Wiimote{slot}")
    _snap_write(nt, logger, f"handheld Classic Controller -> {profile!r} on [Wiimote1] (transient)")


# --------------------------------------------------------------------------- the decision
def _wiimote_count() -> int:
    try:
        return int(devices.dolphinbar_wiimotes())
    except Exception:
        return 0


def _cc_capable(rom: str) -> bool:
    try:
        return dolphin_wii_tdb.is_cc_capable(rom)
    except Exception:
        return False                                   # fail-closed


def force_cc(rom: str) -> bool:
    """A per-game override: `[backends.dolphin_wii.pergame.<GameID>].force_cc = true` forces the
    Classic Controller for a data-gap game GameTDB has no CC record of (e.g. WiiWare). Consulted only
    in the no-bar branch, so it covers docked-no-bar AND handheld. Fail-safe False. PUBLIC: the
    router's warning (dolphin_cfg.route) also consults it so a forced game shows no spurious "no
    DolphinBar" dialog. Resolves the id exactly as is_cc_capable does, so the stored GameID matches."""
    try:
        gid = dolphin_wii_tdb._resolve(rom)
        if not gid:
            return False
        pg = (_be_wii().get("pergame") or {}).get(gid)
        return bool(pg.get("force_cc")) if isinstance(pg, dict) else False
    except Exception:
        return False


def apply(rom: str, logger=None) -> str:
    """Guarded entry point. The game-start hook launches the game regardless, so ANY unexpected error
    degrades to "skip" (leave the resting config) rather than aborting the launch."""
    try:
        return _run_decision(rom, logger)
    except Exception as ex:                            # never let a probe/IO error break a launch
        if logger:
            logger.warning(f"dolphin_wii: apply aborted ({ex!r}); leaving resting config")
        return "skip"


def _run_decision(rom: str, logger=None) -> str:
    """Decide + apply the Wii Remote source for this launch; return the chosen mode
    (real|real2|sinden|classic|skip). Sweeps any crashed-CC leftover FIRST."""
    restore(logger)                                    # crash-orphan sweep (no-op without a leftover)
    if _BACKUP.is_file():                              # restore() failed to consume a surviving backup
        if logger:
            logger.warning("dolphin_wii: leftover CC backup survived; leaving config untouched")
        return "skip"
    if devices.dolphinbar_present():                   # USB-level presence (NOT the awake-remote count)
        real2_min = int(_be().get("real2_min_wiimotes", 2))
        mode = "real2" if _wiimote_count() >= real2_min else "real"
        _run_tool(mode, logger)
        return mode
    if _is_lightgun(rom):
        _apply_sinden(logger)
        return "sinden"
    if _cc_capable(rom) or force_cc(rom):              # GameTDB CC-capable, or a per-game force flag
        _apply_cc(logger)
        return "classic"
    _run_tool("real", logger)                          # no bar, not lightgun, not CC -> today's behavior
    return "real"


# --------------------------------------------------------------------------- CLI (the hooks)
def _main(argv: list[str]) -> int:
    import logging
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    log = logging.getLogger("dolphin_wii")
    mode = argv[1] if len(argv) > 1 else ""
    rom = argv[2] if len(argv) > 2 else ""
    if mode == "apply":
        print(apply(rom, log))                         # the hook reads this to start the quit-watcher on real*
        return 0
    if mode == "restore":
        restore(log)
        return 0
    print("usage: python3 -m lib.dolphin_wii_source {apply <rom>|restore}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
