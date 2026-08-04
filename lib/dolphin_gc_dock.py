"""Launch-time controller layout for GameCube (standalone Dolphin) — the dolphin_gc coordinator.

Called by controller-router.py at gc game-start (the `dolphin_gc` backend) and reverted by
hooks/game-end/dolphin-gc-restore.sh. It reverts any crash-orphaned swap to the resting config, then:
  HANDHELD (only the Deck built-in pad; `[backends.dolphin_gc].dock_autodetect` on): load the chosen
    `undocked_profile` into `[GCPad1]`.
  DOCKED (deck_state, honoring the [handheld] force override): apply the "pads -> players" profile
    priority (lib/dolphin_gc_pads) across the ports -- the top profiles whose pad is connected fill `[GCPad1..4]`.
Both are a TRANSIENT swap: snapshot GCPadNew.ini once, apply, and the game-end hook restores it.

Byte-safe: only the targeted `[GCPadN]` bodies are replaced (block copy, lib.dolphin_profiles), the
snapshot is a whole-file copy, atomic writes. Dolphin is closed at game-start, so there is no rewrite
race. Everything degrades to "do nothing" on any error (the launch always continues).
"""
from __future__ import annotations

from pathlib import Path

from lib import deck_state, dolphin_profiles
from lib.policy import load_merged

_DIR = Path.home() / ".var/app/org.DolphinEmu.dolphin-emu/config/dolphin-emu"
_FILE = _DIR / "GCPadNew.ini"
_BACKUP = _DIR / "GCPadNew.ini.dock-backup"     # transient snapshot of the resting config


def _be() -> dict:
    be = (load_merged().get("backends") or {}).get("dolphin_gc")
    return be if isinstance(be, dict) else {}


def _is_docked() -> bool:
    """Physical dock/display state (deck_state), honoring the [handheld] force override -- the same
    signal handheld_res / handheld_input / switch_bind use. Fail-safe: on any error assume docked
    (-> the docked path, which no-ops unless a "pads -> players" priority is set). Replaces the old
    pad-presence heuristic, which misread a Bluetooth pad connected while UNDOCKED as "docked"."""
    try:
        hh = load_merged().get("handheld")
        return deck_state.is_docked(deck_state.resolve_force(hh if isinstance(hh, dict) else None))
    except Exception:
        return True


def _read() -> str | None:
    try:
        return _FILE.read_text(encoding="utf-8", errors="replace") if _FILE.is_file() else None
    except OSError:
        return None


def _atomic_write(text: str) -> None:
    tmp = _FILE.with_suffix(_FILE.suffix + ".dock-tmp")
    tmp.write_text(text, encoding="utf-8", newline="")   # verbatim (preserve line endings)
    tmp.replace(_FILE)


def restore(logger=None) -> bool:
    """Revert a transient undocked swap: copy the snapshot back over GCPadNew.ini and drop it.
    No-op (returns False) when no snapshot exists (docked play never created one)."""
    if not _BACKUP.is_file():
        return False
    try:
        tmp = _FILE.with_suffix(_FILE.suffix + ".dock-tmp")   # atomic: temp + replace, never truncate
        tmp.write_bytes(_BACKUP.read_bytes())
        tmp.replace(_FILE)
        _BACKUP.unlink()
        if logger:
            logger.info("dolphin_gc: restored resting GCPadNew.ini after the game")
        return True
    except OSError as ex:
        if logger:
            logger.warning(f"dolphin_gc: restore failed: {ex!r}")
        return False


def _pergame_profile(gid: str | None, docked: bool) -> str | None:
    """The per-game profile pick for this context ([backends.dolphin_gc.pergame.<GameID>]
    docked_profile / handheld_profile, written by the MAD pickers), or None. An explicit
    per-game pick applies even with `dock_autodetect` off — the toggle governs only the
    GLOBAL auto-swap."""
    if not gid:
        return None
    pg = (_be().get("pergame") or {}).get(gid)
    if not isinstance(pg, dict):
        return None
    v = pg.get("docked_profile" if docked else "handheld_profile")
    return v.strip() if isinstance(v, str) and v.strip() else None


def _gameid(rom) -> str | None:
    """ROM -> 6-char GameID via dolphin-tool (path+mtime cached); None-safe + fail-safe."""
    if not rom:
        return None
    try:
        from lib import dolphin_gameids
        return dolphin_gameids.gameid(str(rom))
    except Exception:
        return None


def plan(rom=None) -> dict:
    """The dock-aware gc controller decision, WITHOUT writing anything.

    {"mode": "docked"|"handheld", "assign": [(port, profile), ...], "note": str}

    `assign` empty means Dolphin's own normal mapping applies, and `note` says why.
    apply() ACTS on exactly this and MAD's Preview RENDERS exactly this, so the page cannot
    drift from the launch. It drifted badly before: Preview had no gc case at all (the
    dispatch tests `backend == "dolphin"`, an exact match gc's `dolphin_gc` misses), so gc
    fell through to a generic branch that reads `pad_classes` -- a key dolphin_gc does not
    have, because its routing is profile-based -- and every gc row rendered "(no player pad)".
    Preview re-deriving what the router already knows is what made that possible; do not add
    a second copy of this decision anywhere.

    `rom` (2026-08-04): the launching ROM enables the PER-GAME profile override (Port 1 only,
    other ports resting). Preview calls plan() rom-less -> the GLOBAL decision, with the
    per-game caveat living in the pickers' notes.
    """
    docked = _is_docked()
    pg = _pergame_profile(_gameid(rom), docked)
    if pg and dolphin_profiles.profile_body(pg) is None:
        # Stale pick (profile renamed/deleted in Dolphin's UI — pickers keep it visible for
        # clearing): FALL THROUGH to the next rung instead of masking a still-valid global
        # profile / pads-priority assignment (2026-08-04 review fix).
        pg = None
    if docked:
        if pg:
            return {"mode": "docked", "assign": [(1, pg)], "note": "per-game profile"}
        from lib import dolphin_gc_pads
        assign = dolphin_gc_pads.plan_assignment()
        return {"mode": "docked", "assign": assign,
                "note": "" if assign else "normal mapping (no profile assignment)"}
    if pg:
        return {"mode": "handheld", "assign": [(1, pg)], "note": "per-game profile"}
    be = _be()
    if not be.get("dock_autodetect", True):
        return {"mode": "handheld", "assign": [],
                "note": "dock auto-detect off; normal mapping"}
    profile = str(be.get("undocked_profile", "") or "")
    if not profile:
        return {"mode": "handheld", "assign": [],
                "note": "no undocked profile set; normal mapping"}
    return {"mode": "handheld", "assign": [(1, profile)], "note": ""}


def apply(logger, rom=None) -> None:
    """At gc game-start: revert any crash-orphaned swap to the resting config, then apply this
    session's transient controller layout — HANDHELD -> the per-game/undocked profile on Port 1;
    DOCKED -> the per-game profile on Port 1, else the "pads -> players" profile priority across
    the ports. The game-end hook (dolphin_gc_dock.restore) reverts whatever we write. The
    decision itself is plan(rom)'s, computed ONCE here and threaded through, so the pad
    resolution (a ~1s cold SDL walk) runs once."""
    restore(logger)                               # -> resting config (no-op if no leftover backup)
    if _BACKUP.is_file():                         # restore() FAILED to consume a surviving snapshot:
        logger.warning("dolphin_gc: could not consume the leftover backup; leaving config untouched")
        return                                    #   never clobber a good resting snapshot with a swap
    p = plan(rom)
    if p["mode"] == "docked":
        if p["note"] == "per-game profile":       # explicit pick: Port 1 only, other ports resting
            _apply_handheld(logger, p)
        else:
            _apply_docked(logger, p["assign"])
    else:
        _apply_handheld(logger, p)


def _apply_handheld(logger, p: dict) -> None:
    if not p["assign"]:
        logger.info(f"dolphin_gc: {p['note']}")
        return
    profile = p["assign"][0][1]
    body = dolphin_profiles.profile_body(profile)
    if body is None:
        logger.warning(f"dolphin_gc: undocked profile {profile!r} not found; skipping")
        return
    text = _read()
    if text is None:
        logger.warning("dolphin_gc: GCPadNew.ini missing; skipping (launch a game once)")
        return
    new_text = dolphin_profiles.apply_profile_body(text, "GCPad1", body)
    if new_text is None:
        logger.warning("dolphin_gc: [GCPad1] absent; skipping")
        return
    tag = " [per-game]" if p.get("note") == "per-game profile" else ""
    _snap_write(new_text, logger,
                f"{p['mode']} -> profile {profile!r} into GCPad1 (transient){tag}")


def _apply_docked(logger, assign) -> None:
    from lib import dolphin_gc_pads
    text = _read()
    if text is None:
        logger.warning("dolphin_gc: GCPadNew.ini missing; skipping")
        return
    new_text, applied = dolphin_gc_pads.assign_text(text, assign=assign)
    if not applied:                               # no priority / hands-off / nothing matched
        logger.info("dolphin_gc: docked -> normal mapping (no profile assignment)")
        return
    _snap_write(new_text, logger,
                "docked -> " + ", ".join(f"P{p}={n!r}" for p, n in applied) + " (transient)")


def _snap_write(new_text: str, logger, msg: str) -> None:
    """Snapshot the resting GCPadNew.ini then write the transient swap. Only snapshots when no backup
    survives (apply() already guaranteed that) so a good resting snapshot is never clobbered. Never
    truncates: the snapshot is a whole-file copy and _atomic_write is temp+replace."""
    try:
        if not _BACKUP.is_file():
            _BACKUP.write_bytes(_FILE.read_bytes())
        _atomic_write(new_text)
    except OSError as ex:
        logger.warning(f"dolphin_gc: could not apply: {ex!r}")
        return
    logger.info(f"dolphin_gc: {msg}")
