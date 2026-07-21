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
    (never cleared) -- EXCEPT in takeover mode (Deck hidden), where the router owns every slot and
    CLEARS (transiently, restored on exit) any it does NOT seat, so a stale profile from a richer
    prior config cannot drive a phantom/duplicate player.
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
    """Every slot the binder may touch: the GamePad slot + the managed external slots.
    De-duplicated (order-preserving) so a hand-edited manage_ports with a repeat can never list a slot
    twice -- important now that takeover CLEARS owned-but-unseated slots (a dup could otherwise clear a
    just-seated slot)."""
    gp = _gamepad_slot(cfg)
    return list(dict.fromkeys([gp] + [s for s in _managed_slots(cfg) if s != gp]))


def _hide_deck_when_external() -> bool:
    """The ES-DE 'no deckpad if external' toggle (context-aware via sdl_filter): True = hide the Deck
    so the external pads are the players from Controller 1; False = keep the Deck as Controller 1. On
    any error, keep the Deck (today's behaviour). Reused, not reimplemented, so Cemu and the SDL-order
    emulators honour the exact same switch."""
    try:
        from . import sdl_filter
        return bool(sdl_filter._hide_deck_when_external())
    except Exception:
        return False


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
def _seat_plan(pol, cfg, context, devs) -> tuple[list[tuple[int, str, object, bool]], list[int]]:
    """Returns (plan, owned_slots). plan = [(slot0, profile_stem, dev_or_None, gamepad_type)] for each
    slot to seat; dev is the seated external pad (None only for the Deck GamePad slot); gamepad_type flags
    the one slot that must be the Wii U GamePad (an external pad taking over Controller 1). owned_slots =
    the slots the router OWNS this launch (takeover = every slot; keep-Deck = none); apply() clears every
    owned slot it does NOT actually seat (transiently), so a stale profile cannot become a phantom player.
    Two layouts, chosen by the 'no deckpad if external' toggle when an external pad is present:

      * KEEP the Deck (toggle off / no external): the Deck -> the GamePad slot; externals -> the
        managed slots (Controller 2..5), Pro-type. Today's behaviour.
      * HIDE the Deck (toggle on + external present): the Deck is NOT seated; the external pads are
        the players from Controller 1 -- the FIRST present external pad -> slot 0 (forced GamePad), the
        next present pads -> the next slots (Pro-type). Resolved pads are COMPACTED from Controller 1 by
        connection order, so a hole at port 1 (a pin to Player 2+) still puts a pad on the GamePad."""
    from . import cemu_profiles, routing
    plan: list[tuple[int, str, object, bool]] = []

    sys_wiiu = _dget(_dget(pol, "systems", {}), "wiiu", {})
    ports = _dget(sys_wiiu, "ports", []) or []
    eff_pins = {**_dget(pol, "pins", {}), **_dget(sys_wiiu, "pins", {})}
    xport = routing.xarcade_port(pol)
    pinned, pin_claimed = routing.resolve_pins(eff_pins, devs)
    port_devs = routing.resolve_ports(ports, devs, with_fallback=False,
                                      preassigned=pinned, preclaimed=pin_claimed, xport=xport)

    if port_devs and _hide_deck_when_external():
        # TAKEOVER: external pads are the players from Controller 1; the Deck is not seated at all.
        slots = _all_slots(cfg)                        # [gamepad_slot, managed...] == [0,1,2,3,4]
        cfg_dir = _config_dir(cfg)
        fam_ord: dict = {}                             # per-family running ordinal -> distinct profiles
        # COMPACT by connection order onto SEATABLE slots: the first pad that resolves to a profile is
        # Controller 1 = the GamePad, the next seatable pad Controller 2, and so on. seat_idx advances
        # ONLY when a pad is actually seated, so neither a hole at port 1 (a pin to a later player) NOR a
        # present-but-unassigned first pad leaves the GamePad slot unseated. (Indexing by the raw port
        # number, or by resolved order, could strand Controller 1 with no GamePad.)
        seat_idx = 0
        for player in sorted(port_devs):
            dev = port_devs[player]
            fam = routing.family_of(dev)
            k = fam_ord.get(fam, 0); fam_ord[fam] = k + 1   # 2nd DualSense -> "DualSense 2", etc.
            name = cemu_profiles.profile_for_nth(cfg, fam, context, k, cfg_dir)
            if not name:
                continue                               # unassigned family: leave it, do NOT consume a slot
            if seat_idx >= len(slots):
                break                                  # more seatable pads than Cemu slots
            plan.append((slots[seat_idx], name, dev, seat_idx == 0))   # first seatable -> Controller 1 (GamePad)
            seat_idx += 1
        # In takeover the router OWNS every slot (_all_slots). Return them ALL as "owned"; apply() clears
        # every owned slot it does not actually SEAT -- including a planned-but-skipped one (missing /
        # unreadable profile) -- so a stale profile in an unused slot (a 2nd DualSense no longer connected,
        # or a DUPLICATE of a seated pad) cannot become a phantom/duplicate player in-game.
        return plan, slots

    # KEEP the Deck: the Deck -> the GamePad slot; externals -> the managed slots (Pro-type).
    gp = _gamepad_slot(cfg)
    gp_name = cemu_profiles.profile_for(cfg, _GAMEPAD_FAMILY, context)
    if gp_name:
        plan.append((gp, gp_name, None, False))
    cfg_dir = _config_dir(cfg)
    fam_ord: dict = {}
    for player, slot0 in enumerate(_managed_slots(cfg), start=1):
        dev = port_devs.get(player)
        if dev is None:
            continue
        fam = routing.family_of(dev)
        k = fam_ord.get(fam, 0); fam_ord[fam] = k + 1
        name = cemu_profiles.profile_for_nth(cfg, fam, context, k, cfg_dir)
        if name:
            plan.append((slot0, name, dev, False))
    return plan, []   # keep-Deck: leave unassigned/hand-config slots untouched (no clearing)


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
    plan, owned_slots = _seat_plan(pol, cfg, context, devs)
    if not plan:
        _seatlog([f"context={context}: nothing assigned -> all slots left resting"])
        return f"{context}: nothing assigned -> untouched"

    sdl_devs = sdl_devices()                          # one SDL init; live index + GUID per pad
    log_lines = [f"context={context}, {len(devs)} evdev pad(s), {len(sdl_devs)} SDL pad(s)"]
    log_lines += [f"  SDL[{s.index}] {s.guid} {s.name!r}" for s in sdl_devs]
    log_lines += [f"  plan: C{slot0 + 1} <- {stem!r} "
                  f"pad={(d.name if d is not None else 'Steam Deck (GamePad)')!r}"
                  f"{' [GamePad]' if gp else ''}"
                  for slot0, stem, d, gp in plan]
    seated: list[str] = []
    written: set[int] = set()                         # slots actually seated (or already-correct); every
    #                                                   OTHER owned slot is cleared (takeover) below.
    for slot0, stem, dev, gamepad_type in plan:
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
        # dev is None only for the Deck GamePad slot (keep-Deck mode, Controller 1): keep the profile
        # verbatim (its own Deck block keeps its baked uuid, one Deck -> GUID-only bind). Every seated
        # pad (dev not None) is re-pinned + cleaned -- Deck co-source dropped, <type> forced to Wii U
        # Pro Controller, or Wii U GamePad for the takeover Controller 1 (gamepad_type) -- all via
        # external_slot=True.
        if dev is not None:
            body = cemu_cfg.repin_profile(body, dev, devs, sdl_devs,
                                          external_slot=True, gamepad_type=gamepad_type)
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
            written.add(slot0)                        # already correct -> counts as seated, do NOT clear
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
        written.add(slot0)
        log_lines.append(f"  SEATED C{slot0 + 1} {stem!r} type={_type_of(body)} "
                         f"uuids={_uuids_of(body)} pad={who!r}")
        seated.append(f"C{slot0 + 1}={stem!r}({who})")
    # Clear every slot the router OWNS but did NOT actually WRITE this launch (takeover only; keep-Deck's
    # owned_slots is empty). Derived from `written`, NOT the plan: a planned slot that was skipped (missing
    # / unreadable profile, write-fail) is owned-but-unwritten and MUST be cleared too, or its stale
    # resting file drives a phantom/duplicate player. Guard on `written`: if nothing seated at all, own
    # nothing (don't wipe a whole resting config on a total-config failure). Snapshot then REMOVE the file:
    # an absent controllerN.xml = no controller on that Cemu port (the one DualSense can't sit on C2 & C3).
    clear_slots = [s for s in owned_slots if s not in written] if written else []
    cleared = 0
    for slot0 in clear_slots:
        target = _port_path(cfg_dir, slot0)
        if not target.is_file():
            continue                                  # already absent -> nothing to clear
        try:
            cur = target.read_bytes()
            backup = _backup_path(cfg_dir, slot0)
            if not backup.is_file():
                _atomic_write_bytes(backup, cur)      # non-empty snapshot -> game-end restore rewrites it
            target.unlink()
        except OSError as ex:
            log_lines.append(f"  FAIL clear C{slot0 + 1}: {ex!r}")
            if logger:
                logger.warning(f"cemu-seat: could not clear Controller {slot0 + 1}: {ex!r}")
            continue
        cleared += 1
        log_lines.append(f"  CLEARED C{slot0 + 1} (removed stale uuids={_uuids_of(cur.decode('utf-8', 'replace'))})")
    log_lines.append(f"result: seated {len(seated)} slot(s)" + (f", cleared {cleared}" if cleared else ""))
    _seatlog(log_lines)
    if logger and (seated or cleared):
        logger.info(f"cemu-seat [{context}]: " + ", ".join(seated)
                    + (f" | cleared {cleared} stale slot(s)" if cleared else ""))
    return f"{context}: seated {len(seated)} slot(s)" + (f", cleared {cleared}" if cleared else "")


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
