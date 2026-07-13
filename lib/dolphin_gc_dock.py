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


def apply(logger) -> None:
    """At gc game-start: revert any crash-orphaned swap to the resting config, then apply this
    session's transient controller layout — HANDHELD -> the undocked profile on Port 1 (dock
    setting); DOCKED -> the "pads -> players" profile priority across the ports. The game-end hook
    (dolphin_gc_dock.restore) reverts whatever we write."""
    restore(logger)                               # -> resting config (no-op if no leftover backup)
    if _BACKUP.is_file():                         # restore() FAILED to consume a surviving snapshot:
        logger.warning("dolphin_gc: could not consume the leftover backup; leaving config untouched")
        return                                    #   never clobber a good resting snapshot with a swap
    if _is_docked():
        _apply_docked(logger)
    else:
        _apply_handheld(logger)


def _apply_handheld(logger) -> None:
    be = _be()
    if not be.get("dock_autodetect", True):
        logger.info("dolphin_gc: dock auto-detect off; normal mapping")
        return
    profile = str(be.get("undocked_profile", "") or "")
    if not profile:
        logger.info("dolphin_gc: handheld but no undocked profile set; normal mapping")
        return
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
    _snap_write(new_text, logger,
                f"handheld -> undocked profile {profile!r} into GCPad1 (transient)")


def _apply_docked(logger) -> None:
    from lib import dolphin_gc_pads
    text = _read()
    if text is None:
        logger.warning("dolphin_gc: GCPadNew.ini missing; skipping")
        return
    new_text, applied = dolphin_gc_pads.assign_text(text)
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
