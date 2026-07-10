"""Launch-time handheld controller swap for Cemu (Wii U) — the on-the-go input rail.

Cemu ("wiiu") is router_skip, so the router never touches its input; nothing loads the
user's saved handheld GamePad profile when undocked, nor restores the docked one on exit.
This module does that, gated on the physical display like every other on-the-go consumer:
  HANDHELD (on-the-go enabled + wiiu participating + a handheld_profile set): snapshot the
    active GamePad profile (controller0.xml) once, then write the handheld profile into it so
    the Deck's built-in pad drives the game. The game-end hook restores it.
  DOCKED / disabled / non-participating / no profile: no swap (any crash-orphaned swap is
    swept back to the resting config first).

A TRANSIENT whole-file swap mirroring lib/dolphin_gc_dock.py: snapshot once to a co-located
controller0.xml.dock-backup, atomic writes (temp + replace, never truncate), restore-first so
a crash cannot strand the swap. Cemu is closed at game-start, so there is no rewrite race.
Every error degrades to "do nothing" (the launch always continues).

Called by hooks/game-start/07-cemu-input.sh (apply) + hooks/game-end/09-cemu-input-restore.sh
(restore). See memory onthego-cemu-input-pending / onthego-handheld-profiles.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Cemu's GamePad is always port 0 = controller0.xml (manage_ports leaves slot 0 to the
# GamePad, the screen). BOTW etc. read the GamePad, so that is the slot the Deck pad occupies.
_GAMEPAD_PORT0 = 0
_BACKUP_SUFFIX = ".dock-backup"     # transient snapshot of the resting GamePad profile


# ── policy / config helpers ──────────────────────────────────────────────────
def _load_policy() -> dict:
    try:
        from . import policy                     # package context (hooks use `from lib import`)
        return policy.load_merged()
    except Exception:
        return {}


def _dget(d, key, default=None):
    """dict.get that tolerates a non-dict (a malformed hand-edited TOML scalar)."""
    return d.get(key, default) if isinstance(d, dict) else default


def _cemu_cfg() -> dict:
    be = _dget(_load_policy(), "backends", {})
    cemu = be.get("cemu") if isinstance(be, dict) else None
    return cemu if isinstance(cemu, dict) else {}


def _config_dir(cfg: dict) -> Path:
    return Path(str(cfg.get("config_dir", "~/.config/Cemu/controllerProfiles"))).expanduser()


def _gamepad_file(cfg_dir: Path) -> Path:
    return cfg_dir / f"controller{_GAMEPAD_PORT0}.xml"


def _backup_file(cfg_dir: Path) -> Path:
    return _gamepad_file(cfg_dir).with_name(f"controller{_GAMEPAD_PORT0}.xml{_BACKUP_SUFFIX}")


def _atomic_write(path: Path, data: bytes) -> None:
    """temp + replace so a crash mid-write leaves the complete old or new file, never a partial."""
    tmp = path.with_suffix(path.suffix + ".dock-tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


# ── restore ──────────────────────────────────────────────────────────────────
def restore(logger=None, cfg=None) -> bool:
    """Revert a transient handheld swap: copy the snapshot back over controller0.xml and drop it.
    No-op (returns False) when no snapshot exists (docked play never created one)."""
    cfg = cfg if isinstance(cfg, dict) else _cemu_cfg()
    cfg_dir = _config_dir(cfg)
    backup = _backup_file(cfg_dir)
    if not backup.is_file():
        return False
    try:
        _atomic_write(_gamepad_file(cfg_dir), backup.read_bytes())
        backup.unlink()
        if logger:
            logger.info("cemu: restored resting controller0.xml (GamePad) after the game")
        return True
    except OSError as ex:
        if logger:
            logger.warning(f"cemu: restore failed: {ex!r}")
        return False


# ── apply ────────────────────────────────────────────────────────────────────
def _gate(cfg: dict) -> tuple[Path | None, str]:
    """Decide whether to swap. Returns (handheld_profile_path, reason). Path is None (no swap)
    unless on-the-go is enabled, wiiu participates, we are handheld, and the profile file exists."""
    pol = _load_policy()
    hh = _dget(pol, "handheld", {})
    if not _dget(hh, "enabled", False):
        return None, "on-the-go disabled"
    try:
        from . import deck_state
    except ImportError:                          # pragma: no cover
        import deck_state                         # type: ignore
    if not deck_state.is_handheld(deck_state.resolve_force(hh if isinstance(hh, dict) else {})):
        return None, "docked -> no swap"
    sys_hh = _dget(_dget(_dget(pol, "systems", {}), "wiiu", {}), "handheld", {})
    if not _dget(sys_hh, "enabled", False):
        return None, "wiiu not participating -> no swap"
    name = str(cfg.get("handheld_profile", "") or "").strip()
    if not name:
        return None, "no handheld_profile set -> no swap"
    prof = _config_dir(cfg) / f"{name}.xml"
    if not prof.is_file():
        return None, f"handheld_profile {name!r} not found -> no swap"
    return prof, f"handheld -> {name}"


def apply(logger=None, cfg=None) -> str:
    """At wiiu game-start: sweep any crash-orphaned swap back to the resting profile, then
    (on-the-go + wiiu participating + handheld + a handheld_profile set) snapshot the active
    GamePad profile and swap in the handheld one. The game-end hook restores it. Returns a
    human-readable status string (the hook prints it to the launch log)."""
    cfg = cfg if isinstance(cfg, dict) else _cemu_cfg()
    cfg_dir = _config_dir(cfg)
    gamepad = _gamepad_file(cfg_dir)
    backup = _backup_file(cfg_dir)

    restore(logger, cfg)                          # -> resting profile (no-op if no leftover backup)
    if backup.is_file():                          # restore() couldn't consume a surviving snapshot:
        msg = "leftover backup survived; leaving input untouched"
        if logger:
            logger.warning(f"cemu: {msg}")
        return msg                                #   never clobber a good resting snapshot

    prof, why = _gate(cfg)
    if prof is None:
        return why                                # docked / disabled / non-participating / no profile

    try:
        new = prof.read_bytes()
    except OSError as ex:
        return f"cannot read handheld profile: {ex!r}"
    if not gamepad.is_file():
        return "controller0.xml missing (launch Cemu once) -> no swap"
    try:
        cur = gamepad.read_bytes()               # guarded like prof above: never throw at launch
    except OSError as ex:
        return f"cannot read controller0.xml: {ex!r}"
    if cur == new:
        # Already the handheld profile (user set it as their GamePad, or an odd state) — do NOT
        # snapshot it as "docked", or restore would strand it. Nothing to do.
        return f"{why} (already active)"
    try:
        if not backup.is_file():
            _atomic_write(backup, cur)            # snapshot the resting GamePad profile once (atomic)
        _atomic_write(gamepad, new)
    except OSError as ex:
        return f"could not apply: {ex!r}"
    if logger:
        logger.info(f"cemu: {why} into controller0.xml (transient)")
    return why


# ── CLI (manual testing + parity with deck_power) ────────────────────────────
def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv[0] if argv else "apply"
    if cmd == "apply":
        print(apply())
        return 0
    if cmd in ("restore", "sweep"):
        print("restored" if restore() else "no swap to restore")
        return 0
    print("usage: cemu_input_dock.py [apply|restore|sweep]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
