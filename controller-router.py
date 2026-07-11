#!/usr/bin/env python3
"""
ES-DE controller-router orchestrator.

Invoked by `controller-router-wrap.sh` before each emulator launch (and again
by the ES-DE game-end hook after exit).

  setup mode:
    1. Classify the game (system + Pew-Pew-Pew collection check).
    2. Load policy from `controller-policy.toml`.
    3. Enumerate connected input devices.
    4. If policy requires a Sinden and none present  → blocking warning;
       Cancel = exit 1 (wrapper aborts emulator launch).
    5. If policy warns on "only X-Arcade" for console games and only X-Arcade
       is present → blocking warning; Cancel = exit 1.
    6. Resolve each port's priority list against present devices, first hit
       wins. Build {port: device_name}.
    7. For Pew-Pew launches: also compute mouse_index for P1/P2 from the
       Sinden smoothed or raw devices.
    8. Write a per-game override file under every configured core dir for
       the system, with a router-managed sentinel block.

  cleanup mode:
    Strip the sentinel block from every per-game override under the system's
    core dirs (the next launch builds it fresh).

Logs to ~/Emulation/storage/controller-router/router.log.
"""
from __future__ import annotations

import argparse
import datetime
import logging
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from lib.classify import classify, GameContext, _strip_escapes  # noqa: E402
from lib.device_binds import binds_for                          # noqa: E402
from lib.devices import (                                       # noqa: E402
    Device, detect_sinden_mouse_indices, enumerate_devices, ra_mouse_index,
    sinden_present, XARCADE_TRACKBALL,
)
# Resolution logic lives in lib/routing.py (moved verbatim, native-panel phase 0
# R1) so the mad-backend daemon can run the SAME pipeline read-only for Preview.
# This script stays the game-launch entry point.
from lib.routing import (                                       # noqa: E402
    load_policy, only_xarcade_present, resolve_pins, resolve_policy,
    resolve_ports, resolve_system, reserve_value, xarcade_port,
    xarcade_present,
)
from lib.retroarch_cfg import (                                 # noqa: E402
    clear_override, core_dirs_for_system, ra_mouse_hotkey_bound, write_override,
)
from lib.cemu_cfg import assign as cemu_assign                  # noqa: E402
from lib.dolphin_cfg import route as dolphin_route              # noqa: E402
from lib.pcsx2_cfg import assign as pcsx2_assign                # noqa: E402
from lib.xemu_cfg import assign as xemu_assign                  # noqa: E402
from lib.eden_cfg import assign as eden_assign                  # noqa: E402
from lib.rpcs3_cfg import assign as rpcs3_assign                # noqa: E402
from lib import mad_paths                                       # noqa: E402

LOG_DIR = mad_paths.storage("controller-router")
LOG_FILE = LOG_DIR / "router.log"


# ---------------------------------------------------------------------------
# logging
# ---------------------------------------------------------------------------

def _setup_logging() -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    # MAD_DEBUG=1 raises verbosity on demand (surfaces logger.debug lines) without a
    # code edit; default INFO = unchanged per-launch output.
    level = logging.DEBUG if os.environ.get("MAD_DEBUG") == "1" else logging.INFO
    logger = logging.getLogger("controller-router")
    logger.setLevel(level)
    if logger.handlers:
        return logger
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    sh.setLevel(level)
    logger.addHandler(sh)
    return logger


# ---------------------------------------------------------------------------
# warning helpers
# ---------------------------------------------------------------------------

def _show_warning_blocking(title: str, body: str, logger) -> int:
    """Spawn lib.warning_dialog as a subprocess so the tkinter mainloop
    doesn't pollute our process. Returns 0=Proceed, 1=user Cancel.

    10.3: a dialog that physically could NOT be shown (exit 3 = tk missing or
    no display, or the subprocess failing to spawn) is treated as PROCEED, not
    Cancel — only a real Cancel click (exit 1) aborts the launch, so a broken
    warning UI can never silently block a game. Callers keep their `!= 0` check."""
    import subprocess
    env = os.environ.copy()
    env.setdefault("DISPLAY", ":0")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "lib.warning_dialog", title, body],
            cwd=str(HERE),
            env=env,
        )
    except Exception as e:
        logger.error(f"warning dialog failed to spawn: {e!r}; proceeding (could not warn)")
        return 0
    rc = result.returncode
    if rc == 1:
        return 1                       # genuine user Cancel → abort the launch
    if rc != 0:
        logger.warning(f"warning dialog could not display (exit {rc}); proceeding")
    return 0                           # 0 = shown+Proceed, or 2/3 = could-not-show → proceed


# ---------------------------------------------------------------------------
# resolution — moved to lib/routing.py (R1); only the dialog-coupled X-Arcade
# warning stays here.
# ---------------------------------------------------------------------------

def _handheld_active(policy: dict) -> bool:
    """True when the on-the-go feature is enabled AND the Deck is physically handheld
    (undocked) -- the SAME gate the rest of the on-the-go rail uses (_ra_handheld_driver's
    joypad flip, ra_handheld_input). Fail-safe: any error -> False (docked behaviour), so a
    detection glitch can only ever KEEP a warning, never wrongly suppress one."""
    try:
        from lib import deck_state
        hh = policy.get("handheld") if isinstance(policy, dict) else None
        if not (isinstance(hh, dict) and hh.get("enabled", False)):
            return False
        return deck_state.is_handheld(deck_state.resolve_force(hh))
    except Exception:
        return False


def _xarcade_warn(sys_entry: dict, devs: list[Device], logger, xport: str,
                  defaults: dict | None = None, handheld: bool = False) -> int:
    """X-Arcade presence warning, defaulted BY CATEGORY (override per-system in
    policy via warn_when_only_xarcade / warn_when_no_xarcade, or globally via the
    [defaults] table = the RetroArch-hub "global" Controllers toggles):
      • console → warn when ONLY the X-Arcade is present (you likely want a gamepad)
      • arcade  → warn when the X-Arcade is NOT present (arcade wants the stick)
    A system is console XOR arcade, so at most one fires. `xport` = the identified
    X-Arcade USB port (routing.xarcade_port(policy)). Returns the dialog exit
    code (0 = Proceed / no warn, 1 = Cancel). Handheld (on-the-go): BOTH warnings
    are skipped -- the X-Arcade is definitionally absent when undocked, so the
    prompts are pure noise (caller passes handheld=_handheld_active(policy))."""
    if handheld:
        logger.info("handheld: skipping X-Arcade presence warning")
        return 0
    cat = sys_entry.get("category")
    defaults = defaults or {}
    # Cascade: per-system stanza > global [defaults] (the hub's global toggles) >
    # category default. resolve_policy does not merge [defaults], so read it here.
    warn_only = sys_entry.get("warn_when_only_xarcade",
                              defaults.get("warn_when_only_xarcade", cat == "console"))
    warn_no = sys_entry.get("warn_when_no_xarcade",
                            defaults.get("warn_when_no_xarcade", cat == "arcade"))
    if warn_only and only_xarcade_present(devs, xport):
        logger.warning("only X-Arcade present for a console game; prompting")
        return _show_warning_blocking(
            title="Plug in a gamepad?",
            body=("This is a console game; the X-Arcade is detected but a\n"
                  "regular gamepad gives a better experience.\n"
                  "Press Proceed to play with the X-Arcade, or Cancel to\n"
                  "plug in a controller first."),
            logger=logger,
        )
    # Gate the "no X-Arcade" nag on an X-Arcade actually being CONFIGURED
    # (xport set via MAD's "Identify X-Arcade"). Without this, a fresh Deck that
    # never had an X-Arcade — xport=="" so xarcade_present() is always False —
    # would show this blocking dialog on EVERY arcade/OpenBOR/MUGEN launch. On a
    # rig with the stick identified, xport is non-empty so the warn (including the
    # configured-but-unplugged case it's meant for) fires exactly as before.
    if warn_no and xport and not xarcade_present(devs, xport):
        logger.warning("no X-Arcade present for an arcade game; prompting")
        return _show_warning_blocking(
            title="No X-Arcade detected",
            body=("This is an arcade game — the X-Arcade Tankstick is the\n"
                  "intended controller but isn't connected.\n"
                  "Plug in the X-Arcade, then press Proceed — or Cancel to\n"
                  "play with another controller."),
            logger=logger,
        )
    return 0


# ---------------------------------------------------------------------------
# main flow
#
# RetroArch mouse-hotkey support: RA polls system hotkeys on port 0 (= player 1)
# only, so a mouse-button hotkey (the X-Arcade red button) reads the device at
# input_player1_mouse_index. For NON-lightgun RA games we pin that to the X-Arcade
# trackball (re-derived each launch from its stable vid:pid; the number shifts on
# replug). Lightgun games keep P1/P2 = the guns (the mouse hotkey can't fire there).
# Shared helpers: devices.XARCADE_TRACKBALL / ra_mouse_index, retroarch_cfg.
# ra_mouse_hotkey_bound (so the Preview page can show the same pin).
# ---------------------------------------------------------------------------

def _ra_handheld_driver(policy: dict, logger) -> None:
    """On-the-go: RetroArch's joypad driver must be sdl2 when HANDHELD (udev is blind to the
    Deck's lizard-mode built-in pad, so on-the-go RA games would otherwise have no gamepad) and
    udev when DOCKED (the X-Arcade dual-emit d-pad fix + Sinden gun path read raw evdev). Strictly
    gated on the physical display, so the docked arcade path is never on sdl2. Self-healing:
    asserted every RA launch; set_global_option is idempotent so an unchanged value is free. Only
    called when the on-the-go feature is enabled (the caller gates it), so when the feature is off
    it is never invoked and RetroArch keeps whatever driver it had -- no legacy override."""
    try:
        from lib import retroarch_cfg
        hh = policy.get("handheld") if isinstance(policy, dict) else None
        handheld = _handheld_active(policy)
        ra = (hh.get("retroarch") if isinstance(hh, dict) else None)
        jdrv = ra.get("joypad_driver") if isinstance(ra, dict) else None
        # Handheld: the Deck's virtual pad (28de:11ff) maps FULLY + stably only on the sdl2 joypad
        # driver (SDL keys by controller GUID); its udev BUTTON indices shift between launches, so a
        # udev profile breaks on relaunch. Docked: udev (X-Arcade dual-emit d-pad fix + Sinden read
        # raw evdev). input_driver stays udev throughout -- the handheld hotkeys are gamepad COMBOS
        # (ra_handheld_input), not synthetic keys, so there is no keyboard-driver flip to conflict.
        driver = (str(jdrv) if jdrv else "sdl2") if handheld else "udev"
        retroarch_cfg.set_global_option("input_joypad_driver", driver)
        # config_save_on_exit MUST stay off while this feature manages the cfg: otherwise RetroArch
        # rewrites the whole retroarch.cfg on exit, re-baking stale/in-session binds and clobbering
        # the transient binds the launch hook writes (the footgun that started this feature). Re-
        # assert it every RA launch (idempotent) so a MAD-GUI toggle or a SteamOS/EmuDeck reset
        # can't silently re-arm it. Trade-off: in-menu setting changes need a manual "Save Current
        # Configuration" (documented in deck-docs/retroarch-sdl2-handheld-input.md).
        retroarch_cfg.set_global_option("config_save_on_exit", "false")
        logger.info(f"on-the-go: input_joypad_driver = {driver}, config_save_on_exit = false")
    except Exception as e:
        logger.warning(f"on-the-go joypad-driver flip failed ({e!r})")


def _ra_on_the_go(ctx: "GameContext", policy: dict, logger) -> None:
    """On a genuine RetroArch launch, keep the global retroarch.cfg matched to the dock state and
    self-heal a crash-orphaned handheld profile. No-op for a standalone (launched_core() is None).
      ENABLED  -> flip the joypad driver (sdl2 handheld / udev docked), apply the handheld pad +
                  hotkey profile (which sweeps a crash orphan first), downshift heavy-core res.
      DISABLED -> STILL heal a crash orphan from a prior handheld session -- a hard crash bypasses
                  the game-end restore, so without this a docked RA game would start on the leftover
                  sdl2 driver + Deck P1 binds, blind to the raw X-Arcade. restore() reverts the
                  orphaned binds/hotkeys (no-op without a sidecar); if it swept one the driver was on
                  sdl2, so put it back to udev. ra_res.sweep_all() reverts a heavy-core res orphan on
                  both paths. Best-effort; caller wraps it so it never blocks the launch."""
    from lib import ra_res, ra_handheld_input, retroarch_cfg as _rc
    core = _rc.launched_core(ctx.system, ctx.rom_basename)
    if core is None:                        # a standalone reached _setup -> not an RA launch
        return
    hh = policy.get("handheld") if isinstance(policy, dict) else None
    if isinstance(hh, dict) and hh.get("enabled", False):
        _ra_handheld_driver(policy, logger)
        ra_handheld_input.apply(logger)     # sweeps a crash orphan first, then applies if handheld
        ra_res.sweep_all()
        ra_res.apply(ctx.system, ctx.rom_basename, core)
    else:
        if ra_handheld_input.restore(logger):
            _rc.set_global_option("input_joypad_driver", "udev")
        ra_res.sweep_all()


def _setup(ctx: GameContext, logger) -> int:
    policy = load_policy()
    xport = xarcade_port(policy)
    sys_entry = resolve_policy(policy, ctx.system, ctx.collection, ctx.rom_basename)
    if sys_entry is None:
        # No explicit [systems.<name>] stanza. If this is an active RetroArch
        # system (it IS being launched via the wrap), don't silently skip — give
        # it a console controller-priority default so a newly-added RA system
        # routes sanely with no policy edit (policy then holds only exceptions;
        # this is the root fix for the "new system → no routing" GBA-class bug).
        # Non-RetroArch / unroutable systems still skip.
        from lib import es_systems
        cmd = es_systems.default_command(ctx.system) if ctx.system else ""
        if cmd and not es_systems.is_standalone(cmd):
            # Global default ports: user-editable via [defaults].ports (the
            # RetroArch-hub "global" tier); falls back to the built-in console
            # order when unset. Only active RA systems with no [systems.<name>]
            # stanza reach here (the is_standalone gate above is preserved).
            dflt = (policy.get("defaults", {}) or {}).get("ports")
            if dflt and isinstance(dflt[0], list):
                default_ports = [list(p) for p in dflt]        # already per-port
            elif dflt:
                default_ports = [list(dflt), list(dflt)]        # flat family list
            else:
                fam = ["DualSense", "8BitDo", "Xbox", "X-Arcade"]
                default_ports = [list(fam), list(fam)]
            sys_entry = {"category": "console", "ports": default_ports}
            logger.info(f"no [systems.{ctx.policy_key}] stanza; defaulting active "
                        f"RetroArch system to console ports {default_ports}")
        else:
            logger.info(f"no policy for system={ctx.policy_key!r} "
                        f"(non-RetroArch/unknown); skipping")
            return 0

    # On-the-go (RetroArch ONLY): match the joypad driver to the dock state (handheld=sdl2 so the
    # built-in pad is visible; docked=udev for the arcade rig) + apply/heal the handheld profile.
    # Runs on EVERY RA launch (even feature-off) so a crash orphan self-heals before a docked game;
    # a standalone reaching _setup is a no-op (launched_core() is None). Best-effort; never blocks.
    try:
        _ra_on_the_go(ctx, policy, logger)
    except Exception as e:
        logger.warning(f"on-the-go RA setup failed ({e!r})")

    logger.info(f"policy resolved: category={sys_entry.get('category', '?')} "
                f"require_sinden={sys_entry.get('require_sinden', False)} "
                f"warn_when_only_xarcade={sys_entry.get('warn_when_only_xarcade', False)}")

    devs = enumerate_devices()
    pad_summary = ", ".join(f"{d.name}" for d in devs if d.is_joypad) or "(none)"
    logger.info(f"joypads present: {pad_summary}")

    # ── lightgun hard requirement ──
    if sys_entry.get("require_sinden"):
        p1_ok, p2_ok = sinden_present()
        if not (p1_ok or p2_ok):
            logger.warning("require_sinden but NO Sinden detected; prompting")
            ec = _show_warning_blocking(
                title="No lightgun detected",
                body=("This game uses the Sinden Lightgun for aiming.\n"
                      "Plug in (and turn on the LED border, if applicable),\n"
                      "then press Proceed. Or press Cancel to back out."),
                logger=logger,
            )
            if ec != 0:
                logger.info("user cancelled at no-gun warning")
                return 1
            # Re-check after the user said Proceed
            p1_ok, p2_ok = sinden_present()
            if not (p1_ok or p2_ok):
                logger.warning("user proceeded with no gun; launch will continue")

    # ── X-Arcade presence warning (console: only-X-Arcade · arcade: no-X-Arcade) ──
    # Skip for UNWRAPPED standalone launches: the 05-standalone hook (_standalone)
    # owns the X-Arcade warn for those (wiiu/wii/xbox/switch/daphne…). A WRAPPED
    # standalone (ps2/ps3/model2/mugen) reaches _setup via controller-router-wrap.sh,
    # whose non-zero exit ABORTS the launch — that abortable warn must stay (the 04
    # hook ignores _setup's exit code, so the abort only ever mattered for wrapped
    # systems), and _standalone suppresses its own warn for wrapped commands. RA
    # systems still warn here. Keyed on the SAME is_standalone(cmd) the _standalone
    # path uses, so exactly one of the two warns per launch.
    from lib import es_systems
    _cmd = es_systems.default_command(ctx.system) if ctx.system else ""
    _unwrapped_standalone = (es_systems.is_standalone(_cmd)
                             and "controller-router-wrap.sh" not in _cmd)
    if not _unwrapped_standalone:
        if _xarcade_warn(sys_entry, devs, logger, xport, policy.get("defaults", {}),
                         handheld=_handheld_active(policy)) != 0:
            logger.info("user cancelled at X-Arcade presence warning")
            return 1
    # Re-enumerate in case the user plugged something in during the warning
    devs = enumerate_devices()

    # ── per-port resolution (device PINS first, then family priority) ──
    # Hybrid pins: the global [pins] table is the baseline; a per-system
    # [systems.<name>.pins] overrides it per player.
    ports = sys_entry.get("ports", [])
    eff_pins = {**policy.get("pins", {}), **sys_entry.get("pins", {})}
    pinned, pin_claimed = resolve_pins(eff_pins, devs)
    if pinned:
        logger.info("device pins: "
                    + ", ".join(f"P{p}={d.name}" for p, d in sorted(pinned.items())))
    port_devs = resolve_ports(ports, devs,
                              preassigned=pinned, preclaimed=pin_claimed,
                              xport=xport)
    # ALL ports bind via RetroArch's class-level reserved_device (vid:pid + name).
    # NOTE (2026-06-05): input_playerN_joypad_index was tried (#37) to exact-pin two
    # IDENTICAL pads, but the router's `js_index` is NOT RetroArch's joypad-enumeration
    # index — a different number space — so it mapped P1/P2 onto absent/wrong pads and
    # left BOTH DualSenses dead in-game and in the RA menu. Reverted to reserved_device:
    # distinct-model pads pin exactly; two IDENTICAL pads both work but may swap P-order
    # across reconnects (RA can't distinguish same vid:pid). Exact identical-pad pinning
    # in RA stays an open problem (would need RA's real enumeration order, not js_index).
    port_names = {p: reserve_value(d) for p, d in port_devs.items()}
    logger.info(f"resolved ports: reserved={port_names}")

    # ── device-specific binds for reserved ports ──
    # RetroArch does not carry a device's autoconfig binds onto a *reserved*
    # port, so for pads with a known non-standard layout (e.g. 8BitDo FC30,
    # whose phantom buttons shift Select/Start to udev idx 10/11) we write the
    # correct physical→RetroPad binds into the same override. Pads without a
    # profile get nothing here — RetroArch's own binds handle them unchanged.
    port_binds: dict[int, dict[str, str]] = {}
    for p, d in port_devs.items():
        b = binds_for(d)
        if b:
            port_binds[p] = b
    if port_binds:
        logger.info("device binds written for ports: "
                    + ", ".join(f"P{p}({port_devs[p].name})"
                                for p in sorted(port_binds)))

    # ── lightgun mouse_index pin (any collection/system marked require_sinden) ──
    mouse_indices: dict[int, int] = {}
    if sys_entry.get("require_sinden"):
        p1_idx, p2_idx, using_smoothed = detect_sinden_mouse_indices(devs)
        if p1_idx is not None:
            mouse_indices[1] = p1_idx
        if p2_idx is not None:
            mouse_indices[2] = p2_idx
        src = "smoothed" if using_smoothed else "raw"
        logger.info(f"lightgun mouse_index from {src}: {mouse_indices}")
    elif ra_mouse_hotkey_bound():
        # Non-lightgun RA game with a mouse-button hotkey bound (the X-Arcade red
        # button): pin player-1's mouse to the X-Arcade trackball so the hotkey fires.
        xa = ra_mouse_index(*XARCADE_TRACKBALL)
        if xa is not None:
            mouse_indices[1] = xa
            logger.info(f"RA mouse-hotkey active: pinned P1 mouse_index={xa} "
                        f"(X-Arcade trackball "
                        f"{XARCADE_TRACKBALL[0]:04x}:{XARCADE_TRACKBALL[1]:04x})")
        else:
            logger.info("RA mouse-hotkey bound but X-Arcade trackball absent; "
                        "P1 mouse_index left to the global cfg")

    # ── nothing to write? skip cleanly ──
    if not port_names and not mouse_indices:
        logger.info("no port reservations or mouse indices to write; done")
        return 0
    if not core_dirs_for_system(ctx.system):
        logger.info(f"system={ctx.system} has no configured RetroArch core "
                    f"dirs; skipping override write (likely standalone emu)")
        return 0

    written = write_override(
        ctx.system, ctx.rom_basename, port_names, mouse_indices or None,
        port_binds or None,
    )
    logger.info(f"wrote per-game override in {len(written)} core dir(s): "
                + ", ".join(str(p) for p in written))
    return 0


def _cleanup(ctx: GameContext, logger) -> int:
    touched = clear_override(ctx.system, ctx.rom_basename)
    logger.info(f"cleanup touched {len(touched)} files")
    # On-the-go: restore RetroArch's udev joypad driver at game-end so the docked arcade path is
    # never left on sdl2 (idempotent; also self-heals a crashed handheld RA session on any exit).
    try:
        from lib import retroarch_cfg
        retroarch_cfg.set_global_option("input_joypad_driver", "udev")
    except Exception as e:
        logger.warning(f"on-the-go joypad-driver restore failed ({e!r})")
    try:
        from lib import ra_handheld_input
        ra_handheld_input.restore(logger)   # restore resting RA hotkeys (no-op without a sidecar)
    except Exception as e:
        logger.warning(f"on-the-go RA hotkeys restore failed ({e!r})")
    try:
        from lib import ra_res
        ra_res.sweep_all()          # revert any handheld heavy-core res downshift to resting
    except Exception:
        pass
    return 0


def _standalone(ctx: GameContext, logger) -> int:
    """Route controllers for a standalone emulator (Cemu / Dolphin), selected
    by the resolved system's `backend` key and configured entirely from the
    matching `[backends.<name>]` table. Invoked at ES-DE game-start (emulator
    closed). Always returns 0 — launch continues regardless (Wii is warn-only
    per the user's choice; Wii U falls back to handheld)."""
    from lib import es_systems          # local import (matches the _setup path) — without
    #                                     it the es_systems.* call below raised NameError,
    #                                     silently aborting ALL standalone routing.
    policy = load_policy()
    xport = xarcade_port(policy)
    sys_entry = resolve_policy(policy, ctx.system, ctx.collection, ctx.rom_basename)
    if sys_entry is None:
        logger.info(f"no policy for system={ctx.policy_key!r}; skipping standalone")
        return 0
    # ── X-Arcade presence warning for STANDALONE systems (daphne/mugen/openbor/
    # model3 + standalone consoles wii/xbox/switch/ps3/wiiu). Gated on the launch
    # command being standalone so RA systems — which also fire this hook — don't
    # double-warn (they warn in _setup). Runs BEFORE the router_skip return so
    # daphne/openbor are covered. The 05 hook is fire-and-forget, so this blocks +
    # lets the user plug the stick in, but can't hard-abort a standalone launch.
    # WRAPPED standalones (ps2/ps3/model2/mugen) are EXCLUDED here: their warn is
    # delivered abortably by _setup via controller-router-wrap.sh, so warning again
    # here would double-prompt. Unwrapped consoles (wiiu/switch/xbox/wii) + daphne
    # are owned here (their _setup warn, via the exit-code-ignoring 04 hook, was
    # never abortable anyway and is now suppressed in _setup).
    cmd = es_systems.default_command(ctx.system, es_systems.load_systems())
    if es_systems.is_standalone(cmd) and "controller-router-wrap.sh" not in cmd:
        _xarcade_warn(sys_entry, enumerate_devices(), logger, xport, policy.get("defaults", {}),
                      handheld=_handheld_active(policy))
    if sys_entry.get("router_skip"):
        # Hands-off systems (e.g. Switch — the user hand-configures every Switch
        # emulator); the router must never touch their input. Data-driven so the
        # game-start hook no longer needs a hardcoded system case.
        logger.info(f"system={ctx.policy_key!r} has router_skip=true; leaving input untouched")
        return 0
    backend = sys_entry.get("backend")
    if not backend:
        logger.info(f"system={ctx.system!r} has no standalone backend; skipping")
        return 0
    logger.debug(f"system={ctx.system!r} -> backend={backend!r} (policy_key={ctx.policy_key!r})")
    backend_cfg = policy.get("backends", {}).get(backend)
    if backend_cfg is None:
        logger.warning(f"backend {backend!r} missing [backends.{backend}] config; skipping")
        return 0

    devs = enumerate_devices()
    pad_summary = ", ".join(d.name for d in devs if d.is_joypad) or "(none)"
    logger.info(f"standalone backend={backend} joypads: {pad_summary}")

    # Device pins (player -> Device), passed to every backend so a pinned pad
    # lands on its player slot regardless of the backend's binding scheme.
    # Hybrid: global [pins] baseline, per-system [systems.<name>.pins] overrides.
    eff_pins = {**policy.get("pins", {}), **sys_entry.get("pins", {})}
    pinned, pin_claimed = resolve_pins(eff_pins, devs)
    if pinned:
        logger.info("device pins: "
                    + ", ".join(f"P{p}={d.name}" for p, d in sorted(pinned.items())))

    if backend == "cemu":
        ports = sys_entry.get("ports", [])
        port_devs = resolve_ports(ports, devs, with_fallback=False,
                                  preassigned=pinned, preclaimed=pin_claimed,
                                  xport=xport)
        resolved = ", ".join(f"P{p}={d.name}" for p, d in sorted(port_devs.items()))
        logger.info(f"cemu resolved ports: {resolved or '(none -> handheld)'}")
        return cemu_assign(port_devs, devs, backend_cfg, logger)

    if backend == "pcsx2":
        # PCSX2 binds by SDL index; the backend matches PlayStation pads by
        # vid:pid and writes their live SDL indices (no port-token resolver).
        # Global pins override per player via the pinned pad's live SDL index.
        return pcsx2_assign(backend_cfg, logger, devs=devs, pins=pinned)

    if backend == "xemu":
        # xemu binds console ports to SDL GUIDs of the PlayStation pads.
        return xemu_assign(backend_cfg, logger, devs=devs, pins=pinned)

    if backend == "eden":
        # Eden (Switch) binds players by no-CRC SDL GUID + port.
        return eden_assign(backend_cfg, logger, devs=devs, pins=pinned)

    if backend == "rpcs3":
        # RPCS3 (PS3) binds players by SDL device name + 1-based index.
        return rpcs3_assign(backend_cfg, logger, devs=devs, pins=pinned)

    if backend == "supermodel":
        # Supermodel can't pin pads in its ini — it's routed at launch via
        # SDL_GAMECONTROLLER_IGNORE_DEVICES_EXCEPT inside supermodel-native.sh
        # (which calls `controller-router.py sdl-ignore <system>`). Nothing to
        # write here.
        logger.info("supermodel: routed via SDL filter in supermodel-native.sh; "
                    "no config to write")
        return 0

    if backend == "dolphin":
        # A lightgun-collection Wii game never reaches here: its collection rule
        # (no backend) makes _resolve_policy return that entry, so we skip above
        # at "no standalone backend". dolphin-wii-mode.sh owns the Sinden source
        # switch for those (it asks the router `lightgun-rom`). So everything
        # reaching this point is a normal real-Wiimote game.
        require = bool(sys_entry.get("require_dolphinbar", False))
        summary = dolphin_route(backend_cfg, require, logger)
        if summary.get("warn"):
            # Informational only — we ignore the dialog's result and continue.
            _show_warning_blocking(
                title="No Wii Remote detected",
                body=("This Wii game uses real Wii Remotes via a Mayflash\n"
                      "DolphinBar — none is connected right now.\n"
                      "Connect the DolphinBar and turn on a Wii Remote, then\n"
                      "press Proceed (the game launches either way)."),
                logger=logger,
            )
        return 0

    if backend == "dolphin_gc":
        # GameCube (standalone Dolphin) launch controller layout, transient (reverted by the game-end
        # hook hooks/game-end/dolphin-gc-restore.sh): HANDHELD (no external pad) -> the undocked
        # profile on Port 1; DOCKED -> the "pads -> players" profile priority across the ports (or the
        # normal mapping when no priority is set / hands-off).
        from lib import dolphin_gc_dock
        dolphin_gc_dock.apply(logger)
        return 0

    logger.warning(f"unknown backend {backend!r}; skipping")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("mode",
                   choices=("setup", "cleanup", "standalone", "sdl-ignore",
                            "sdl-ignore-list", "pin-node", "quit-systems", "quit-cmd",
                            "lightgun-quit-cmd", "collection-of", "view-collection",
                            "track-view", "splash-collection", "lightgun-rom"))
    p.add_argument("rom_path", nargs="?", default="")
    p.add_argument("name", nargs="?", default="")
    p.add_argument("system", nargs="?", default="")
    p.add_argument("fullname", nargs="?", default="")
    args = p.parse_args(argv[1:])

    # sdl-ignore: print an SDL_GAMECONTROLLER_IGNORE_DEVICES_EXCEPT whitelist for
    # the given system's backend to STDOUT (consumed by supermodel-native.sh).
    # Logs go to stderr/file so stdout stays clean for `$(...)` capture.
    if args.mode == "sdl-ignore":
        from lib.sdl_filter import keep_except_list, keep_first_present
        system = args.system or args.rom_path or args.name
        pol = load_policy()
        entry = resolve_system(pol, system) or {}
        be = pol.get("backends", {}).get(entry.get("backend", ""), {})
        if not be.get("pad_classes") and not be.get("handheld_class"):
            # No player pads AND no handheld fallback => the whitelist is empty,
            # which hides EVERY pad and leaves the game with no usable controller.
            # Warn on stderr (stdout stays clean for the shell capture).
            print(f"controller-router: backend '{entry.get('backend', '?')}' has no "
                  f"pad_classes/handheld_class — SDL whitelist is empty, the game will "
                  f"see no controller.", file=sys.stderr)
        if be.get("sdl_priority"):
            # strict priority chain: only the top present family (-> P1)
            wl = keep_first_present(be.get("pad_classes", []), be.get("handheld_class", ""))
        else:
            # expose all listed player pads (Supermodel JOY1/JOY2)
            wl = keep_except_list(be.get("pad_classes", []), be.get("handheld_class", ""),
                                  be.get("keep_extra", []))
        print(wl)   # stdout stays clean for the shell capture
        if os.environ.get("MAD_DEBUG") == "1":   # diagnose X-Arcade/whitelist routing on demand
            print(f"controller-router: sdl-ignore {system!r} backend="
                  f"{entry.get('backend', '?')!r} -> {wl!r}", file=sys.stderr)
        return 0

    # sdl-ignore-list: print an SDL_GAMECONTROLLER_IGNORE_DEVICES *blocklist* (the
    # connected pads to HIDE = everything except the chosen top family) to STDOUT.
    # For Proton/Wine games (OpenBOR) whose winebus ignores the _EXCEPT whitelist
    # but honors the IGNORE blocklist — so the Steam Deck pad + extras are hidden
    # and only the chosen family (e.g. X-Arcade) reaches the game.
    if args.mode == "sdl-ignore-list":
        from lib.sdl_filter import ignore_nonplayers
        system = args.system or args.rom_path or args.name
        pol = load_policy()
        entry = resolve_system(pol, system) or {}
        be = pol.get("backends", {}).get(entry.get("backend", ""), {})
        print(ignore_nonplayers(be.get("pad_classes", []),
                                be.get("handheld_class", "")))
        return 0

    # pin-node <system> <player>: print the evdev node (/dev/input/eventN) of the
    # pad PINNED to <player> in the effective [pins] table (global + per-system
    # override), or nothing if unpinned. STDOUT only (clean for `$(...)`); a
    # standalone launch wrapper feeds it to SDL_JOYSTICK_DEVICE so a chosen device
    # (e.g. one X-Arcade half — distinguished by its USB interface in the pin_id)
    # becomes that emulator's player-1 joystick. Read-only: resolves the SAME pins
    # the RetroArch setup path uses, so global + per-system semantics match for free.
    if args.mode == "pin-node":
        system = args.rom_path or args.system
        try:
            player = int(args.name)
        except (ValueError, TypeError):
            return 0
        pol = load_policy()
        eff_pins = {**pol.get("pins", {}),
                    **((resolve_system(pol, system) or {}).get("pins", {}))}
        d = resolve_pins(eff_pins, enumerate_devices())[0].get(player)
        if d is not None:
            print(d.path)
        if os.environ.get("MAD_DEBUG") == "1":
            print(f"controller-router: pin-node {system!r} P{player} -> "
                  f"{getattr(d, 'path', None)!r}", file=sys.stderr)
        return 0

    # quit-systems  -> the standalone systems (with games) eligible for a hold-to-
    #                  quit combo, one per line (consumed by the config GUI).
    # quit-cmd <sys> -> the shell command that quits that system's emulator, or
    #                  empty if it's a RetroArch/HID/unknown system (consumed by
    #                  the quit-combo-watcher.sh game-start hook). STDOUT only.
    if args.mode in ("quit-systems", "quit-cmd"):
        from lib import es_systems
        pol = load_policy()
        if args.mode == "quit-systems":
            print("\n".join(es_systems.quit_combo_systems(pol)))
        else:
            system = args.system or args.rom_path or args.name
            print(es_systems.quit_cmd(system, pol))
        return 0

    # lightgun-quit-cmd <rom> <system>  -> the RetroArch quit command IFF the ROM is
    #   a RetroArch lightgun game (a require_sinden collection ROM on an RA core),
    #   else empty. The game-start quit-combo hook uses it to cover RA lightgun games
    #   — whose mouse quit hotkey can't fire (P1 mouse = the gun) — with the
    #   red-button watcher. STDOUT only (clean for `$(...)`).
    if args.mode == "lightgun-quit-cmd":
        from lib import es_systems
        rom = _strip_escapes(args.rom_path)
        system = args.name or args.system
        print(es_systems.lightgun_ra_quit_cmd(rom, load_policy(), system))
        return 0

    # collection-of <rom>  -> print the enabled custom collection the ROM belongs
    #                         to (exit 0), or nothing (exit 1).
    # lightgun-rom <rom>   -> exit 0 iff the ROM's matched collection has
    #                         require_sinden in policy (a lightgun collection),
    #                         else exit 1. Consumed by sinden.sh + dolphin-wii-mode.sh
    #                         in place of their old hardcoded collection greps.
    # view-collection <rom> <view>  -> print <view> iff it is an enabled custom
    #   collection that CONTAINS <rom> (exit 0), else nothing (exit 1). <view> is
    #   the collection the user launched FROM (the last `system-select` shortname,
    #   recorded by scripts/system-select/05-record-view.sh). This is what the
    #   launch-screen resolver uses so a game in several collections shows the
    #   screen for the one you actually browsed — not first-by-order. Membership
    #   doubles as a staleness guard: a stale view that doesn't own this ROM is
    #   rejected, falling the caller back to the system screen.
    if args.mode == "view-collection":
        from lib import es_collections as colls
        rom = _strip_escapes(args.rom_path)
        view = args.name   # recorded system-select shortname
        if view and colls.rom_in_collection(rom, view):
            print(view)
            return 0
        return 1

    # track-view <rom> <system>  -> update the recorded view from the HIGHLIGHTED
    #   game (game-select hook), so collection changes via the L/R QuickSystemSelect
    #   jump (which ES-DE does NOT report via system-select) are still tracked.
    #   Rule (current view = $XDG_RUNTIME_DIR/es-current-view):
    #     • if the current view is a COLLECTION:
    #         - rom is in it            -> keep (still consistent; handles supersets)
    #         - rom is in some other    -> switch to the first enabled collection
    #                                      that contains rom
    #         - rom is in none          -> drop to the game's system (left collections)
    #     • else (system view / empty)  -> track the game's system (don't auto-promote
    #                                      to a collection, so plain system-browsing
    #                                      keeps showing the system splash)
    #   The carousel still sets the view exactly via 05-record-view.sh; this only
    #   corrects the L/R-hop staleness. Best-effort, never errors.
    if args.mode == "track-view":
        try:
            from lib import es_collections as colls
            rom = _strip_escapes(args.rom_path)
            sysname = args.name            # the highlighted game's system ($3)
            sf = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "es-current-view"
            cur = sf.read_text().strip() if sf.exists() else ""
            enabled = set(colls.enabled_collections())
            if cur in enabled:             # collection view
                if colls.rom_in_collection(rom, cur):
                    new = cur
                else:
                    owner = colls.collection_for_rom(rom)
                    new = owner if owner else (sysname or cur)
            else:                          # system view / unknown
                new = sysname or cur
            if new and new != cur:
                tmp = sf.with_suffix(".tmp")
                tmp.write_text(new)
                tmp.replace(sf)
        except Exception:
            pass
        return 0

    # splash-collection <rom>  -> print the MOST SPECIFIC enabled collection that
    #   contains <rom> (smallest membership; ties by CollectionSystemsCustom order),
    #   else nothing (exit 1). The launch-screen resolver uses this so the splash is
    #   a deterministic function of the game (no view tracking, no sticky behaviour):
    #   Spider-Man games -> spiderman; Batman/X-Men -> superheroes; etc.
    if args.mode == "splash-collection":
        from lib import es_collections as colls
        rom = _strip_escapes(args.rom_path)
        name = colls.most_specific_collection(rom)
        if name:
            print(name)
            return 0
        return 1

    if args.mode in ("collection-of", "lightgun-rom"):
        from lib import es_collections as colls
        rom = _strip_escapes(args.rom_path)
        name = colls.collection_for_rom(rom)
        if args.mode == "collection-of":
            if name:
                print(name)
                return 0
            return 1
        if not name:
            return 1
        ent = load_policy().get("collections", {}).get(name, {})
        return 0 if ent.get("require_sinden") else 1

    logger = _setup_logging()
    logger.info(f"========== {args.mode} ==========")
    logger.info(f"args: rom={args.rom_path!r} name={args.name!r} "
                f"system={args.system!r} fullname={args.fullname!r}")

    # Build the classify-style argv (script-name + rom + name + system + fullname)
    ctx = classify([sys.argv[0], args.rom_path, args.name, args.system,
                    args.fullname])
    logger.info(f"context: rom_basename={ctx.rom_basename!r} "
                f"collection={ctx.collection!r} "
                f"policy_key={ctx.policy_key!r}")

    if args.mode == "setup":
        return _setup(ctx, logger)
    if args.mode == "standalone":
        return _standalone(ctx, logger)
    return _cleanup(ctx, logger)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
