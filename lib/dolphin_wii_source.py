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
_HANDHELD_DEFAULT = dolphin_wii_profiles.HANDHELD_DEFAULT
_SINDEN_P1 = "Sinden Lightgun P1"
_SINDEN_P2 = "Sinden Lightgun P2"
# Any EMULATED-profile body in the gun slots (the _apply_sinden rebuild check). Extended
# beyond Classic when non-Classic profiles became pickable (2026-08-04). IMPORTANT: this can
# match a LEGITIMATE resting body too (profiles are authored in Dolphin's own UI and may be
# left active), not just crash debris — which is why the rebuild snapshots first and is
# TRANSIENT (see _apply_sinden). A bare-Wiimote body (plain Buttons/ lines) stays undetected.
_CLASSIC_MARK = re.compile(r'(?mi)^(Extension[ \t]*=[ \t]*(Classic|Nunchuk)\b|Classic/|Nunchuk/)')

# Per-STYLE global default keys in [backends.dolphin_wii] (docked_key, handheld_key). The CC
# style has no entry here: its rails are the existing _apply_cc paths (pads priority /
# undocked_profile), unchanged.
_STYLE_KEYS = {"sideways": ("docked_profile_sideways", "undocked_profile_sideways"),
               "nunchuk": ("docked_profile_nunchuk", "undocked_profile_nunchuk"),
               "other": ("docked_profile_other", "undocked_profile_other")}


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
    rich live mapping intact. Last resort: if the gun slots STILL look like an emulated-profile
    body (Classic OR Nunchuk), rebuild [Wiimote1/2] from the on-disk gun profiles -- incomplete,
    but far better than dead guns. The rebuild SNAPSHOTS the pre-rebuild file first (2026-08-04
    review fix): now that profiles are authored in Dolphin's own UI, an emulated resting body can
    be a LEGITIMATE hand-authored setup, not just crash debris -- the snapshot makes the rebuild
    transient (game-end restore puts the resting body back) instead of destroying user data."""
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
        if changed and _snap_backup(logger):           # never overwrite without a restorable copy
            try:
                _atomic_write(text)
                if logger:
                    logger.warning("dolphin_wii: gun slots held emulated-profile bindings; "
                                   "rebuilt from gun profiles (transient)")
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


# --------------------------------------------------------------------------- profile ladder
def _explicit_pergame(gid: str | None, docked: bool) -> str | None:
    """The per-game profile pick for this context ([backends.dolphin_wii.pergame.<GameID>]
    docked_profile / handheld_profile), or None. Beats style detection entirely."""
    if not gid:
        return None
    pg = (_be_wii().get("pergame") or {}).get(gid)
    if not isinstance(pg, dict):
        return None
    v = pg.get("docked_profile" if docked else "handheld_profile")
    return v.strip() if isinstance(v, str) and v.strip() else None


def _style(rom: str, gid: str | None) -> str:
    """The game's input style: cc | sideways | nunchuk | other. CC first (a pad-drivable game
    keeps the existing rails); the CURATED sideways list beats the derived nunchuk fact
    (GameTDB lists an optional nunchuk on many sideways-primary games, e.g. NSMB Wii)."""
    if _cc_capable(rom) or force_cc(rom):
        return "cc"
    try:
        if gid and dolphin_wii_tdb.is_sideways(gid):
            return "sideways"
        if gid and dolphin_wii_tdb.is_nunchuk(gid):
            return "nunchuk"
    except Exception:
        pass
    return "other"


def _style_global(style: str, docked: bool) -> str | None:
    """The [backends.dolphin_wii] per-style default for this context, or None (rung unset)."""
    keys = _STYLE_KEYS.get(style)
    if not keys:
        return None
    v = _be_wii().get(keys[0] if docked else keys[1])
    return v.strip() if isinstance(v, str) and v.strip() else None


def _snap_backup(logger) -> bool:
    """Take the transient WiimoteNew.ini snapshot WITHOUT writing anything else — used before
    a Sinden PICK's Source flip (and _apply_sinden's rebuild) so the game-end restore reverts
    it. False (with a warning) if the snapshot could not be taken."""
    try:
        if not _BACKUP.is_file():
            _BACKUP.write_bytes(_FILE.read_bytes())
        return True
    except OSError as ex:
        if logger:
            logger.warning(f"dolphin_wii: could not snapshot before Sinden apply: {ex!r}")
        return False


def _resolved_pick(rom: str, logger=None) -> tuple[str | None, str]:
    """The no-bar profile ladder, VALIDATED: (profile, rung). rung is "explicit"|"cc"|
    <style>. A stale pick (file missing) is warned + FALLS THROUGH to the next rung — the
    pickers keep stale picks visible for clearing, so this state is expected. ("cc", None)
    means the existing Classic rails own the launch. Sinden picks are not body-validated
    here (their apply path is the sinden tool, not a body copy)."""
    docked = _is_docked()
    try:
        gid = dolphin_wii_tdb._resolve(rom) or None
    except Exception:
        gid = None
    prof = _explicit_pergame(gid, docked)
    if prof is not None and not dolphin_wii_profiles.is_sinden(prof) \
            and dolphin_wii_profiles.profile_body(prof) is None:
        if logger:
            logger.warning(f"dolphin_wii: per-game profile {prof!r} not found; "
                           "falling through to the style default")
        prof = None
    if prof is not None:
        return prof, "explicit"
    style = _style(rom, gid)
    if style == "cc":
        return None, "cc"
    prof = _style_global(style, docked)
    if prof is not None and not dolphin_wii_profiles.is_sinden(prof) \
            and dolphin_wii_profiles.profile_body(prof) is None:
        if logger:
            logger.warning(f"dolphin_wii: style profile {prof!r} not found; "
                           "falling through to the legacy behavior")
        prof = None
    return prof, style


def sinden_pick(rom: str) -> bool:
    """True iff a no-bar launch of this WII rom resolves to a Sinden profile PICK — PUBLIC:
    controller-router's `lightgun-rom` mode consults it so the game-start sinden.sh hook
    starts the gun DRIVER for a picked (non-collection) lightgun game; without this the pick
    would flip the config but the gun would never track. Fail-safe False; only fires for a
    rom under a wii ROM dir with no DolphinBar (a bar launch never reaches the ladder)."""
    try:
        path = str(rom).replace("\\", "/").lower()
        if "/wii/" not in path:
            return False
        if devices.dolphinbar_present():
            return False
        prof, _rung = _resolved_pick(rom)
        return bool(prof and dolphin_wii_profiles.is_sinden(prof))
    except Exception:
        return False


def _apply_single(profile: str, docked: bool, logger) -> bool:
    """Load a NON-Sinden profile body into [Wiimote1] (Source=1), slots 2-4 off, via the
    existing transient rail. False (with a warning) on any miss — the caller falls through to
    the legacy behavior, so a stale pick can never brick a launch. Sinden picks are routed by
    the CALLER through _apply_sinden (full gun mode), never here."""
    text = _read()
    if text is None:
        if logger:
            logger.warning("dolphin_wii: WiimoteNew.ini missing; skipping profile")
        return False
    body = dolphin_wii_profiles.profile_body(profile)
    if body is None:
        if logger:
            logger.warning(f"dolphin_wii: profile {profile!r} not found; falling back")
        return False
    nt = dolphin_wii_profiles.apply_cc_body(text, "Wiimote1", body)
    if nt is None:
        if logger:
            logger.warning("dolphin_wii: [Wiimote1] absent; skipping profile")
        return False
    for slot in (2, 3, 4):
        nt = dolphin_wii_profiles.disable_slot(nt, f"Wiimote{slot}")
    _snap_write(nt, logger, f"{'docked' if docked else 'handheld'} profile -> {profile!r} "
                            "on [Wiimote1] (transient)")
    return True


def profile_override(rom: str) -> bool:
    """True iff a no-bar launch of this ROM will receive an emulated profile (a VALIDATED
    per-game pick or style default — a stale pick does not count, so the warning still fires
    exactly when the launch would actually fall through to `real`) — PUBLIC: dolphin_cfg.route
    consults it so a covered game shows no spurious "no DolphinBar" warning (the force_cc
    invariant). The CC rung returns False here (route's own CC check already covers it).
    Fail-safe False."""
    try:
        prof, _rung = _resolved_pick(rom)
        return prof is not None
    except Exception:
        return False


def plan() -> dict:
    """Rom-less preview of the no-bar profile ladder for the CURRENT context (mirrors
    dolphin_gc_dock.plan's contract: Preview renders what the launch decider acts on, never a
    re-derivation). Per-game picks and the Sinden collection can differ per title — the
    pickers' notes carry that caveat.

    {"mode": "docked"|"handheld", "styles": {sideways|nunchuk|other: profile|None},
     "cc": <the Classic default for this context>}"""
    docked = _is_docked()
    styles = {s: _style_global(s, docked) for s in ("sideways", "nunchuk", "other")}
    cc = ("pads-to-players order" if docked
          else str(_be_wii().get("undocked_profile", _HANDHELD_DEFAULT) or _HANDHELD_DEFAULT))
    return {"mode": "docked" if docked else "handheld", "styles": styles, "cc": cc}


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
    # No bar, not a Sinden-collection game: the PROFILE LADDER (2026-08-04) —
    # validated explicit per-game pick -> detected style (cc -> the unchanged CC rails;
    # sideways/nunchuk/other -> that style's validated global default) -> legacy fallback.
    # A stale pick warns + falls through inside _resolved_pick, so it can never brick a
    # launch OR mask a still-valid lower rung. Byte-identical to the old behavior when
    # nothing new is configured. A Sinden pick reroutes through the FULL sinden mode (both
    # gun slots + scanning flag; the DRIVER start is covered by sinden_pick() via the
    # lightgun-rom gate) and is made TRANSIENT by snapshotting first — the game-end restore
    # then reverts the Source flips. (The Dolphin.ini WiimoteContinuousScanning=False the
    # sinden tool writes is not snapshotted — same residue the collection path has always
    # had; the next real-mode launch re-enables it.)
    prof, rung = _resolved_pick(rom, logger)
    if rung == "cc":                                   # GameTDB CC-capable / force_cc: old rail
        _apply_cc(logger)
        return "classic"
    if prof is not None:
        if dolphin_wii_profiles.is_sinden(prof):
            _snap_backup(logger)                       # transient: game-end reverts the flip
            _apply_sinden(logger)
            return "sinden"
        if _apply_single(prof, _is_docked(), logger):
            return "classic"
    if _cc_capable(rom) or force_cc(rom):              # legacy fallback
        _apply_cc(logger)
        return "classic"
    _run_tool("real", logger)                          # no bar, nothing configured -> today's behavior
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
