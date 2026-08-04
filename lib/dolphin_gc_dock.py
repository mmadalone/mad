"""Launch-time controller layout for GameCube (standalone Dolphin) — the dolphin_gc coordinator.

Called by controller-router.py at gc game-start (the `dolphin_gc` backend) and reverted by
hooks/game-end/dolphin-gc-restore.sh. It reverts any crash-orphaned swap to the resting config,
then seats PORTS 1-4 (multi-seat 2026-08-04): for each port, the per-game pick
(`[backends.dolphin_gc.pergame.<GameID>]` docked_profile[_pN] / handheld_profile[_pN]) wins,
else the context's base — DOCKED: the "pads -> players" profile priority (lib/dolphin_gc_pads);
HANDHELD: the global `undocked_profile[_pN]` seats. A port with no pick is LEFT UNTOUCHED
(docked: the pads priority may fill it; handheld: the resting config) — GC ports are never
force-disabled, unlike Wii slots. The old `dock_autodetect` auto-swap gate and its toggle are
retired (a set row seats, an unset row leaves the port alone); a stored value stays inert.

TRANSIENT swap: snapshot GCPadNew.ini once, apply, and the game-end hook restores it.
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


_SEATS = (1, 2, 3, 4)


def _seat_key(base: str, seat: int) -> str:
    """Port 1 keeps the legacy key name (live user data stays valid); 2-4 suffix _p<n>."""
    return base if seat == 1 else f"{base}_p{seat}"


def _clean(v) -> str | None:
    return v.strip() if isinstance(v, str) and v.strip() else None


def _pergame_profile(be: dict, gid: str | None, docked: bool, seat: int = 1) -> str | None:
    """The per-game profile pick for this context+port ([backends.dolphin_gc.pergame.<GameID>]
    docked_profile[_pN] / handheld_profile[_pN], written by the MAD pickers), or None."""
    if not gid:
        return None
    pg = (be.get("pergame") or {}).get(gid)
    if not isinstance(pg, dict):
        return None
    return _clean(pg.get(_seat_key("docked_profile" if docked else "handheld_profile", seat)))


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
    """The dock-aware gc controller decision for PORTS 1-4, WITHOUT writing anything.

    {"mode": "docked"|"handheld", "assign": [(port, profile), ...], "note": str}

    Per port, first match wins: the per-game pick for that port -> the context base (docked:
    the pads-priority assignment; handheld: the global undocked_profile[_pN] seat). A stale
    pick (profile renamed/deleted in Dolphin's UI — pickers keep it visible for clearing)
    falls through to the next rung PER PORT; a profile already seated on a lower port is
    skipped the same way (one authored profile drives one pad — copied twice it would
    mirror, not split). Ports with no match are simply absent from `assign` and stay
    untouched at apply time.

    `assign` empty means Dolphin's own normal mapping applies, and `note` says why.
    apply() ACTS on exactly this and MAD's Preview RENDERS exactly this, so the page cannot
    drift from the launch. It drifted badly before: Preview had no gc case at all (the
    dispatch tests `backend == "dolphin"`, an exact match gc's `dolphin_gc` misses), so gc
    fell through to a generic branch that reads `pad_classes` -- a key dolphin_gc does not
    have, because its routing is profile-based -- and every gc row rendered "(no player pad)".
    Preview re-deriving what the router already knows is what made that possible; do not add
    a second copy of this decision anywhere. Preview calls plan() rom-less -> the GLOBAL
    decision, with the per-game caveat living in the pickers' notes.
    """
    docked = _is_docked()
    be = _be()
    gid = _gameid(rom)
    base: dict[int, str] = {}
    if docked:
        from lib import dolphin_gc_pads
        base = dict(dolphin_gc_pads.plan_assignment())
    else:
        for n in _SEATS:
            v = _clean(be.get(_seat_key("undocked_profile", n)))
            if v is not None:
                base[n] = v
    assign: list[tuple[int, str]] = []
    used: set[str] = set()
    pergame_hit = False
    for n in _SEATS:
        cands: list[tuple[str, bool]] = []
        pg = _pergame_profile(be, gid, docked, n)
        if pg is not None:
            cands.append((pg, True))
        if n in base:
            cands.append((base[n], False))
        for prof, from_pg in cands:
            if prof in used or dolphin_profiles.profile_body(prof) is None:
                continue                          # duplicate or stale -> next rung for this port
            assign.append((n, prof))
            used.add(prof)
            pergame_hit = pergame_hit or from_pg
            break
    if assign:
        note = "per-game profile" if pergame_hit else ""
    else:
        note = ("normal mapping (no profile assignment)" if docked
                else "no handheld profiles set; normal mapping")
    return {"mode": "docked" if docked else "handheld", "assign": assign, "note": note}


def apply(logger, rom=None) -> None:
    """At gc game-start: revert any crash-orphaned swap to the resting config, then apply this
    session's transient port layout — plan(rom)'s per-port decision, computed ONCE here and
    threaded through (the pad resolution is a ~1s cold SDL walk). Unassigned ports are left
    untouched. The game-end hook (dolphin_gc_dock.restore) reverts whatever we write."""
    restore(logger)                               # -> resting config (no-op if no leftover backup)
    if _BACKUP.is_file():                         # restore() FAILED to consume a surviving snapshot:
        logger.warning("dolphin_gc: could not consume the leftover backup; leaving config untouched")
        return                                    #   never clobber a good resting snapshot with a swap
    p = plan(rom)
    if not p["assign"]:
        logger.info(f"dolphin_gc: {p['mode']} -> {p['note']}")
        return
    text = _read()
    if text is None:
        logger.warning("dolphin_gc: GCPadNew.ini missing; skipping (launch a game once)")
        return
    from lib import dolphin_gc_pads
    new_text, applied = dolphin_gc_pads.assign_text(text, assign=p["assign"])
    if not applied:                               # every [GCPadN] header absent -> nothing to do
        logger.info(f"dolphin_gc: {p['mode']} -> normal mapping (no port header matched)")
        return
    tag = " [per-game]" if p["note"] == "per-game profile" else ""
    _snap_write(new_text, logger,
                f"{p['mode']} -> " + ", ".join(f"P{pt}={n!r}" for pt, n in applied)
                + f" (transient){tag}")


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
