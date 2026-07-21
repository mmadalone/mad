"""Launch-time family x context controller seating for Cemu (Wii U).

Cemu ("wiiu") is ``router_skip``, so the router never touches its input. This binder does,
gated on the physical display like every on-the-go consumer, and keyed by controller
FAMILY (not player slot): at game-start it writes each seated pad's assigned profile
(from ``[backends.cemu.profile_map.<context>]``) into its Cemu ``controllerN.xml``,
re-pinned to the pad's live SDL uuid, and reverts every managed file on exit. It subsumes
the older single-slot handheld swap (lib/cemu_input_dock): Controller 1 (the GamePad) is
the "Steam Deck" family; external pads fill Controller 2..5 by connection order.

DESIGN
  * ``seating_enabled = false`` (default) -> no-op (today's behaviour, byte-for-byte).
  * Controller 1 (``gamepad_port``) = the (Steam Deck, context) profile if assigned, else
    the resting file is left untouched (docked usually = your hand-config).
  * Each external pad -> its family's (context) profile into its managed slot, re-pinned.
    An unassigned family, a missing profile file, or a slot with no pad is left untouched
    (never cleared).
  * TRANSIENT: each written slot's resting ``controllerN.xml`` is snapshotted once to
    ``controllerN.xml.mad-seat-backup``, restored on exit (a restore-first sweep heals a
    crash; an empty snapshot marks "the resting file was absent" so restore removes ours).
    Cemu is closed at game-start and rewrites config on exit, so there is no race.

Called by hooks/game-start/07-cemu-input.sh (apply) + hooks/game-end/09-cemu-input-restore.sh
(restore). Every error degrades to "leave that slot alone"; the launch always continues.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_BACKUP_SUFFIX = ".mad-seat-backup"
_GAMEPAD_FAMILY = "Steam Deck"


def _seatlog(lines: list[str]) -> None:
    """Append a compact seat record to the shared router.log (Game Mode has no console; the last
    debug was only possible because a game was left running). Never breaks seating on a log error."""
    try:
        import datetime
        from . import mad_paths
        log = mad_paths.storage("controller-router", "router.log")
        log.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with log.open("a", encoding="utf-8") as f:
            for ln in lines:
                f.write(f"cemu-seat [{ts}]: {ln}\n")
    except Exception:
        pass


def _uuids_of(text: str) -> list[str]:
    return re.findall(r"<uuid>(.*?)</uuid>", text)


def _type_of(text: str) -> str:
    m = re.search(r"<type>(.*?)</type>", text)
    return m.group(1).strip() if m else "?"


def _load_policy() -> dict:
    try:
        from . import policy
        return policy.load_merged()
    except Exception:
        return {}


def _dget(d, key, default=None):
    """dict.get that tolerates a non-dict (a malformed hand-edited TOML scalar)."""
    return d.get(key, default) if isinstance(d, dict) else default


def _cemu_cfg(pol) -> dict:
    be = _dget(pol, "backends", {})
    cemu = be.get("cemu") if isinstance(be, dict) else None
    return cemu if isinstance(cemu, dict) else {}


def _config_dir(cfg) -> Path:
    return Path(str(_dget(cfg, "config_dir", "~/.config/Cemu/controllerProfiles"))).expanduser()


def _port_path(cfg_dir: Path, slot0: int) -> Path:
    return cfg_dir / f"controller{slot0}.xml"


def _profile_path(cfg_dir: Path, stem: str) -> Path:
    return cfg_dir / f"{stem}.xml"


def _backup_path(cfg_dir: Path, slot0: int) -> Path:
    return _port_path(cfg_dir, slot0).with_name(f"controller{slot0}.xml{_BACKUP_SUFFIX}")


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """temp + replace so a crash mid-write leaves the complete old or new file, never a partial."""
    tmp = path.with_suffix(path.suffix + ".seat-tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def _managed_slots(cfg) -> list[int]:
    mp = _dget(cfg, "manage_ports", [1, 2, 3, 4])
    try:
        return [int(s) for s in mp]
    except (TypeError, ValueError):
        return [1, 2, 3, 4]


def _gamepad_slot(cfg) -> int:
    try:
        return int(_dget(cfg, "gamepad_port", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _all_slots(cfg) -> list[int]:
    """Every slot the binder may touch: the GamePad slot + the managed external slots."""
    gp = _gamepad_slot(cfg)
    return [gp] + [s for s in _managed_slots(cfg) if s != gp]


# ── restore ──────────────────────────────────────────────────────────────────
def _revert_seat(logger=None, cfg=None) -> int:
    """Revert every NEW-STYLE transient seat: copy each slot's snapshot back over its
    ``controllerN.xml`` (or remove ours if the snapshot is the empty "was absent" marker) and
    drop the snapshot. Returns the count reverted. No-op-safe when nothing was seated."""
    cfg = cfg if isinstance(cfg, dict) else _cemu_cfg(_load_policy())
    cfg_dir = _config_dir(cfg)
    n = 0
    for slot0 in _all_slots(cfg):
        backup = _backup_path(cfg_dir, slot0)
        if not backup.is_file():
            continue
        try:
            data = backup.read_bytes()
            target = _port_path(cfg_dir, slot0)
            if data == b"":
                target.unlink(missing_ok=True)          # resting file was absent -> remove ours
            else:
                _atomic_write_bytes(target, data)
            backup.unlink()
            n += 1
        except OSError as ex:
            if logger:
                logger.warning(f"cemu-seat: restore of Controller {slot0 + 1} failed: {ex!r}")
    if n and logger:
        logger.info(f"cemu-seat: reverted {n} controller file(s) to resting after the game")
    return n


def restore(logger=None, cfg=None) -> int:
    """Game-end entry: revert BOTH the new-style family seats and the legacy single-slot
    handheld swap (lib/cemu_input_dock), so a game-end heals whichever apply() ran. Returns
    the count of new-style slots reverted."""
    cfg = cfg if isinstance(cfg, dict) else _cemu_cfg(_load_policy())
    n = _revert_seat(logger, cfg)
    try:
        from . import cemu_input_dock
        cemu_input_dock.restore(logger, cfg)
    except Exception:
        pass
    return n


# ── apply ─────────────────────────────────────────────────────────────────────
def _seat_plan(pol, cfg, context, devs) -> list[tuple[int, str, object]]:
    """[(slot0, profile_stem, dev_or_None)] for each slot with a profile assigned in this
    context. dev is the seated external pad; None for the GamePad slot (the Deck itself)."""
    from . import cemu_profiles, routing
    plan: list[tuple[int, str, object]] = []
    gp = _gamepad_slot(cfg)
    gp_name = cemu_profiles.profile_for(cfg, _GAMEPAD_FAMILY, context)
    if gp_name:
        plan.append((gp, gp_name, None))

    sys_wiiu = _dget(_dget(pol, "systems", {}), "wiiu", {})
    ports = _dget(sys_wiiu, "ports", []) or []
    eff_pins = {**_dget(pol, "pins", {}), **_dget(sys_wiiu, "pins", {})}
    xport = routing.xarcade_port(pol)
    pinned, pin_claimed = routing.resolve_pins(eff_pins, devs)
    port_devs = routing.resolve_ports(ports, devs, with_fallback=False,
                                      preassigned=pinned, preclaimed=pin_claimed, xport=xport)
    for player, slot0 in enumerate(_managed_slots(cfg), start=1):
        dev = port_devs.get(player)
        if dev is None:
            continue
        name = cemu_profiles.profile_for(cfg, routing.family_of(dev), context)
        if name:
            plan.append((slot0, name, dev))
    return plan


def apply(logger=None) -> str:
    """At wiiu game-start: heal any orphaned seat back to resting first, then either replay
    the legacy handheld swap (seating disabled = today's behaviour, byte-for-byte) or seat
    each slot by family x context (seating enabled). Returns a status string for the log."""
    pol = _load_policy()
    cfg = _cemu_cfg(pol)
    cfg_dir = _config_dir(cfg)
    if not cfg_dir.is_dir():
        return f"config dir {cfg_dir} not found -> no seat"

    restore(logger, cfg)                              # heal orphaned seats (new + legacy) -> resting
    if not _dget(cfg, "seating_enabled", False):
        from . import cemu_input_dock
        return cemu_input_dock.apply(logger, cfg)     # legacy single-slot handheld swap = today's behaviour

    survivors = [s for s in _all_slots(cfg) if _backup_path(cfg_dir, s).is_file()]
    if survivors:                                     # a snapshot restore() couldn't consume
        msg = f"leftover seat backup for slot(s) {survivors}; leaving input untouched"
        if logger:
            logger.warning(f"cemu-seat: {msg}")
        return msg                                    # never clobber a good resting snapshot

    from . import handheld_input, cemu_cfg
    from .devices import enumerate_devices, sdl_devices
    context = handheld_input.context()
    devs = enumerate_devices()
    plan = _seat_plan(pol, cfg, context, devs)
    if not plan:
        _seatlog([f"context={context}: nothing assigned -> all slots left resting"])
        return f"{context}: nothing assigned -> untouched"

    sdl_devs = sdl_devices()                          # one SDL init; live index + GUID per pad
    log_lines = [f"context={context}, {len(devs)} evdev pad(s), {len(sdl_devs)} SDL pad(s)"]
    log_lines += [f"  SDL[{s.index}] {s.guid} {s.name!r}" for s in sdl_devs]
    log_lines += [f"  plan: C{slot0 + 1} <- {stem!r} "
                  f"pad={(d.name if d is not None else 'Steam Deck (GamePad)')!r}"
                  for slot0, stem, d in plan]
    seated: list[str] = []
    for slot0, stem, dev in plan:
        prof = _profile_path(cfg_dir, stem)
        if not prof.is_file():
            log_lines.append(f"  SKIP C{slot0 + 1}: profile {stem!r} missing; slot left resting")
            if logger:
                logger.warning(f"cemu-seat: profile {stem!r} for Controller {slot0 + 1} "
                               "missing; leaving that slot untouched")
            continue
        try:
            body = prof.read_text(encoding="utf-8")
        except OSError as ex:
            log_lines.append(f"  SKIP C{slot0 + 1}: cannot read {stem!r}: {ex!r}")
            if logger:
                logger.warning(f"cemu-seat: cannot read profile {stem!r}: {ex!r}")
            continue
        # The GamePad slot (dev is None) is the Deck itself (Controller 1): keep its profile verbatim
        # (its own Deck block keeps its baked uuid, one Deck -> GUID-only bind). External slots
        # (dev not None) are re-pinned AND cleaned for an external player -- Deck co-source dropped,
        # <type> forced to Wii U Pro Controller -- via external_slot=True.
        if dev is not None:
            body = cemu_cfg.repin_profile(body, dev, devs, sdl_devs, external_slot=True)
        new = body.encode("utf-8")

        target = _port_path(cfg_dir, slot0)
        try:
            cur = target.read_bytes() if target.is_file() else None
        except OSError as ex:
            log_lines.append(f"  SKIP C{slot0 + 1}: cannot read current slot: {ex!r}")
            if logger:
                logger.warning(f"cemu-seat: cannot read Controller {slot0 + 1}: {ex!r}")
            continue
        if cur == new:
            log_lines.append(f"  C{slot0 + 1} {stem!r}: already seated (unchanged)")
            continue                                  # already the seated profile; nothing to do
        try:
            backup = _backup_path(cfg_dir, slot0)
            if not backup.is_file():
                _atomic_write_bytes(backup, cur if cur is not None else b"")
            _atomic_write_bytes(target, new)
        except OSError as ex:
            log_lines.append(f"  FAIL C{slot0 + 1}: could not write: {ex!r}")
            if logger:
                logger.warning(f"cemu-seat: could not seat Controller {slot0 + 1}: {ex!r}")
            continue
        who = dev.name if dev is not None else "Steam Deck"
        log_lines.append(f"  SEATED C{slot0 + 1} {stem!r} type={_type_of(body)} "
                         f"uuids={_uuids_of(body)} pad={who!r}")
        seated.append(f"C{slot0 + 1}={stem!r}({who})")
    log_lines.append(f"result: seated {len(seated)} slot(s)")
    _seatlog(log_lines)
    if logger and seated:
        logger.info(f"cemu-seat [{context}]: " + ", ".join(seated))
    return f"{context}: seated {len(seated)} slot(s)"


# ── CLI (manual testing + hook entrypoint parity with cemu_input_dock) ─────────
def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv[0] if argv else "apply"
    if cmd == "apply":
        print(apply())
        return 0
    if cmd in ("restore", "sweep"):
        n = restore()
        print(f"reverted {n} slot(s)" if n else "no seat to revert")
        return 0
    print("usage: cemu_seat.py [apply|restore|sweep]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
