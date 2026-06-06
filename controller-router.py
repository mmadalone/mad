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
import tomllib
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from lib.classify import classify, GameContext, _strip_escapes  # noqa: E402
from lib.device_binds import binds_for                          # noqa: E402
from lib.devices import (                                       # noqa: E402
    Device, by_substring, detect_sinden_mouse_indices,
    enumerate_devices, pin_id, pin_kind, port_of, sinden_present,
)
from lib.retroarch_cfg import (                                 # noqa: E402
    clear_override, core_dirs_for_system, write_override,
)
from lib.cemu_cfg import assign as cemu_assign                  # noqa: E402
from lib.dolphin_cfg import route as dolphin_route              # noqa: E402
from lib.pcsx2_cfg import assign as pcsx2_assign                # noqa: E402
from lib.xemu_cfg import assign as xemu_assign                  # noqa: E402
from lib.eden_cfg import assign as eden_assign                  # noqa: E402
from lib.rpcs3_cfg import assign as rpcs3_assign                # noqa: E402

POLICY_FILE = HERE / "controller-policy.toml"
# Machine-written overrides from the config GUI. Deep-merged over the (commented,
# human-edited) defaults so the GUI never has to mangle the documented file.
LOCAL_POLICY_FILE = HERE / "controller-policy.local.toml"
LOG_DIR = Path.home() / "Emulation/storage/controller-router"
LOG_FILE = LOG_DIR / "router.log"


# ---------------------------------------------------------------------------
# logging
# ---------------------------------------------------------------------------

def _setup_logging() -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("controller-router")
    logger.setLevel(logging.INFO)
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
    sh.setLevel(logging.INFO)
    logger.addHandler(sh)
    return logger


# ---------------------------------------------------------------------------
# policy
# ---------------------------------------------------------------------------

def _deep_merge(base: dict, over: dict) -> dict:
    """Recursively merge `over` into `base` (override wins); dict values merge,
    everything else (lists, scalars) is replaced wholesale. Returns base."""
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def _load_policy() -> dict:
    base: dict = {"systems": {}}
    if POLICY_FILE.is_file():
        with POLICY_FILE.open("rb") as f:
            base = tomllib.load(f)
    if LOCAL_POLICY_FILE.is_file():
        try:
            with LOCAL_POLICY_FILE.open("rb") as f:
                _deep_merge(base, tomllib.load(f))
        except (tomllib.TOMLDecodeError, OSError):
            pass   # a broken local file must never break routing
    return base


def _resolve_system(policy: dict, key: str) -> Optional[dict]:
    """Look up a system entry, resolving its full `inherits` chain (parent-most
    first, each child overriding its parent). Guards against cycles (A→B→A) and
    undefined/non-string parents — both are surfaced on stderr (never stdout,
    which the sdl-ignore callers capture) and the chain is truncated at the bad
    link so a misconfigured policy degrades gracefully instead of looping."""
    systems = policy.get("systems", {})
    if key not in systems:
        return None
    chain: list[str] = []          # child-most -> parent-most
    seen: set[str] = set()
    cur = key
    referrer = None                # the entry whose `inherits` pointed at `cur`
    while cur is not None:
        if not isinstance(cur, str):
            print(f"controller-router: [systems.{referrer}] inherits a non-string "
                  f"value {cur!r} — ignoring inherit.", file=sys.stderr)
            break
        if cur in seen:
            print(f"controller-router: circular inherits detected — "
                  f"{' -> '.join(chain)} -> {cur} — breaking the cycle.",
                  file=sys.stderr)
            break
        entry = systems.get(cur)
        if entry is None:
            print(f"controller-router: [systems.{referrer}] inherits "
                  f"'{cur}', which is not defined — ignoring inherit.",
                  file=sys.stderr)
            break
        seen.add(cur)
        chain.append(cur)
        referrer = cur
        cur = entry.get("inherits")
    merged: dict = {}
    for name in reversed(chain):                       # parent-most first; child wins
        merged.update({k: v for k, v in systems[name].items() if k != "inherits"})
    return merged


def _resolve_policy(policy: dict, ctx: GameContext) -> Optional[dict]:
    """The policy entry that governs this launch. A `[collections.<name>]` rule
    for the matched enabled collection WINS over the launched system's policy
    (e.g. a Duck Hunt launch from NES routes by the lightgun collection). If the
    collection has no rule, fall through to the system's `[systems.<name>]`."""
    if ctx.collection:
        ent = policy.get("collections", {}).get(ctx.collection)
        if ent is not None:
            # A collection rule may `inherits` a system's config — resolve it
            # (parity with systems, which resolve their full chain in
            # _resolve_system); the collection's own keys override the parent.
            if "inherits" in ent:
                parent = _resolve_system(policy, ent["inherits"]) or {}
                merged = dict(parent)
                merged.update({k: v for k, v in ent.items() if k != "inherits"})
                return merged
            return ent
    return _resolve_system(policy, ctx.system)


# ---------------------------------------------------------------------------
# warning helpers
# ---------------------------------------------------------------------------

def _show_warning_blocking(title: str, body: str, logger) -> int:
    """Spawn lib.warning_dialog as a subprocess so the tkinter mainloop
    doesn't pollute our process. Returns exit code (0=Proceed, 1=Cancel)."""
    import subprocess
    env = os.environ.copy()
    env.setdefault("DISPLAY", ":0")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "lib.warning_dialog", title, body],
            cwd=str(HERE),
            env=env,
        )
        return result.returncode
    except Exception as e:
        logger.error(f"warning dialog failed to show: {e!r}; defaulting to Cancel")
        return 1


# ---------------------------------------------------------------------------
# resolution
# ---------------------------------------------------------------------------

def _pin_items(pins: dict):
    """Yield (int player, str tagged-pin_id) from the global [pins] table,
    skipping malformed keys/values and out-of-range player numbers."""
    for k, v in (pins or {}).items():
        try:
            p = int(k)
        except (ValueError, TypeError):
            continue
        if 1 <= p <= 16:                     # valid player slot (RA supports up to 8/16)
            yield p, str(v)


def _resolve_pins(pins: dict, devs: list[Device]) -> tuple[dict[int, Device], set[str]]:
    """Resolve the GLOBAL [pins] table (player -> tagged pin_id, e.g.
    'uniq:054c:0ce6:<mac>') against connected pads. Returns ({port: Device},
    claimed_paths). The first unclaimed pad whose `pin_id` matches the key wins.
    Claiming: a `uniq:`/`port:` key claims every node sharing that key (the whole
    physical unit / that one interface); a `vidpid:` key (ambiguous model-only)
    claims ONLY the matched node, so a second model-only pin can still resolve to
    a different device. A pin whose pad isn't connected is skipped silently.
    Sinden guns are never matched here (their PID udev pinning is separate)."""
    assigned: dict[int, Device] = {}
    claimed: set[str] = set()
    for player, key in sorted(_pin_items(pins)):
        match = next((d for d in devs
                      if d.is_joypad and not d.is_sinden
                      and d.path not in claimed and pin_id(d) == key), None)
        if match is None:
            continue
        assigned[player] = match
        if pin_kind(key) == "vidpid":
            claimed.add(match.path)          # ambiguous model — claim only this node
        else:
            for d in devs:                   # uniq → whole unit; port → that interface
                if pin_id(d) == key:
                    claimed.add(d.path)
    return assigned, claimed


def _resolve_ports(ports: list[list[str]], devs: list[Device],
                   with_fallback: bool = True,
                   preassigned: Optional[dict[int, Device]] = None,
                   preclaimed: Optional[set[str]] = None) -> dict[int, Device]:
    """For each port (1-indexed), walk its priority-list substrings, find the
    FIRST present joypad whose name contains one (and isn't yet claimed by an
    earlier port), and assign THAT device to the port. Policy tokens are only
    used to *pick* the device here. Returns {port: Device}; the caller derives
    the RetroArch reserve-value (`_reserve_value`) and any device-specific
    binds (`device_binds.binds_for`) from the chosen Device.

    Claim tracking matters when the user has e.g. one 8BitDo + one DualSense
    launching a NES game with policy ["8BitDo", "DualSense"]: P1 takes the
    8BitDo, then P2 must fall through to "DualSense" because the only 8BitDo
    is gone. Without claim-tracking, both ports would reserve "8BitDo" and
    P2 would end up unassigned (RetroArch's first-match-not-yet-assigned
    logic only fires when MULTIPLE devices match the same reserved name).

    When TWO 8BitDo pads are present, claim-tracking picks both distinct
    devices; each port reserves its own `_reserve_value` (identical vid:pid for
    same-model pads) and RetroArch's sequential cascade pairs them to P1/P2.
    """
    claimed: set[str] = set(preclaimed or ())   # paths already taken (incl. global pins)
    # Only honor pins for ports this system actually has — a global P3 pin is a
    # no-op on a 2-player game (don't write input_playerN beyond the port count).
    out: dict[int, Device] = {p: d for p, d in (preassigned or {}).items()
                              if 1 <= p <= len(ports)}
    for i, priority in enumerate(ports, start=1):
        if i in out:                            # pinned → keep it, skip token resolution
            continue
        for substr in priority:
            hits = by_substring(devs, substr, kind="joypad")
            # First not-yet-claimed match wins this port
            for d in hits:
                if d.path not in claimed:
                    out[i] = d
                    claimed.add(d.path)
                    break
            if i in out:
                break

    # ── fallback ──
    # Standalone backends (Cemu) want STRICT matching — only pads named in the
    # policy tokens, never a catch-all — so the empty-port falls through to the
    # backend's own handheld/none handling. RetroArch keeps the rescue below.
    if not with_fallback:
        return out
    # If a port matched no policy token but a real (non-Sinden) gamepad is
    # connected, bind the next unclaimed one so the port never lands on
    # RetroArch's "N/A" (an unassigned reserved/empty port). This rescues the
    # case observed post-reboot: only the X-Arcade was connected, but it had
    # not yet enumerated as "Xbox 360 Wireless Receiver" (it appeared only as a
    # Steam virtual "Microsoft X-Box 360 pad"), so no token matched and the
    # router wrote nothing → P1 = N/A. Writing a best-guess token is never
    # worse than N/A: if RetroArch's SDL2 name doesn't contain it, the port is
    # left unassigned exactly as it would have been without us.
    real_pads = [d for d in devs if d.is_joypad and not d.is_sinden and not d.is_steam_virtual]
    for i in range(1, len(ports) + 1):
        if i in out:
            continue
        for d in real_pads:
            if d.path in claimed:
                continue
            if _fallback_token(d) is None:
                continue
            out[i] = d
            claimed.add(d.path)
            break
    return out


def _reserve_value(d: Device) -> str:
    """The exact string RetroArch's reservation matcher will accept for `d`.

    RetroArch (PR libretro/RetroArch#16647, in the user's 1.22.2) does NOT
    substring-match `input_playerN_reserved_device`. Its matcher first tries
    `sscanf("%04x:%04x ", ...)`; on success it compares vid:pid, otherwise it
    falls back to `string_is_equal()` (EXACT full device name). A bare token
    like "Xbox" is neither a vid:pid nor the exact name, so it NEVER matches —
    the reserved port stays empty (RetroArch "N/A") while the real pad spills
    into a later port. Verified live 2026-05-29 in the udev-driver verbose log.

    We emit the canonical form RetroArch itself writes — "<vid>:<pid> <name>" —
    which matches by vid:pid (robust to name variants) and dual-assigns two
    physically-identical pads (same vid:pid) to two same-reserved ports via
    RetroArch's sequential cascade. The trailing name is informational; sscanf
    stops after the two hex fields."""
    return f"{d.vid:04x}:{d.pid:04x} {d.name}"


# The X-Arcade Tankstick in Xbox mode enumerates as "Xbox 360 Wireless Receiver"
# (USB vid 045e, two interfaces) — BYTE-IDENTICAL to a real Xbox 360 pad, so it
# can't be told apart by vid:pid or name. It's identified ONLY by the user-set USB
# port ([hardware].xarcade_port) — see _is_xarcade below.


_XARCADE_PORT_CACHE: Optional[str] = None   # process-lifetime cache (policy is static per launch)


def _xarcade_port() -> str:
    """Configured USB port of the X-Arcade ([hardware].xarcade_port), or "" if unset.
    Lazily loaded once — the policy doesn't change within a launch."""
    global _XARCADE_PORT_CACHE
    if _XARCADE_PORT_CACHE is None:
        try:
            _XARCADE_PORT_CACHE = str(_load_policy().get("hardware", {}).get("xarcade_port", "") or "")
        except Exception:
            _XARCADE_PORT_CACHE = ""
    return _XARCADE_PORT_CACHE


def _is_xarcade(d: Device) -> bool:
    # The X-Arcade in Xbox mode is 045e:02a1 — IDENTICAL to a real Xbox 360 pad.
    # A pad is the X-Arcade ONLY when it's a 045e device at the user-IDENTIFIED USB
    # port ([hardware].xarcade_port). Not identified → it's just an Xbox-looking pad
    # (safe default: never assume an 045e is the stick). Set via MAD's Preview
    # "Identify X-Arcade"; re-cabling the stick → re-identify.
    port = _xarcade_port()
    return bool(port) and d.vid == 0x045e and port_of(d.phys) == port


# Steam Input's virtual gamepad (vid 28de, pid 11ff) — appears as
# "Microsoft X-Box 360 pad N". It's never a real "plug in a better controller"
# alternative: it's either the X-Arcade wrapped by Steam Input, or the Deck's
# built-in pad surfaced through Steam Input. For the only-X-Arcade warning it
# must NOT count as a present real gamepad (it masked the warning in test 2,
# 2026-05-29 — the dialog never showed because the phantom looked like a pad).
def _is_steam_virtual_pad(d: Device) -> bool:
    return d.vid == 0x28de and d.pid == 0x11ff


def _fallback_token(d: Device) -> Optional[str]:
    """Gate: is `d` a pad we can confidently reserve as a fallback? Returns a
    non-None marker for pads we recognize (8BitDo / DualSense / X-Arcade /
    Steam-virtual), or None for ones we can't map — in which case we leave the
    port to RetroArch's own autoconfig instead of pinning it. The actual value
    written is `_reserve_value(d)`, not this marker."""
    n = d.name.lower()
    if "8bitdo" in n:
        return "8BitDo"
    if d.vid == 0x054c or "dualsense" in n or "dualshock" in n:
        return "DualSense"
    # X-Arcade (raw "Xbox 360 Wireless Receiver") or a Steam-virtual
    # "Microsoft X-Box 360 pad" standing in for it: RetroArch shows the
    # X-Arcade as "X-Arcade Xbox 360 wireless controller", so "Xbox" matches.
    if d.vid == 0x045e or "xbox" in n or "x-box" in n:
        return "Xbox"
    return None


def _only_xarcade_present(devs: list[Device]) -> bool:
    # Real gamepads only — the Sinden guns classify as joypads (they expose
    # gamepad-style buttons + an absolute axis) but are not controllers anyone
    # plays a console game with, so they must not defeat the all() test.
    pads = [d for d in devs if d.is_joypad and not d.is_sinden]
    if not pads:
        return False
    # A Steam-virtual pad is the X-Arcade/Deck wrapped by Steam Input, not a
    # real alternative controller — treat it as "X-Arcade" so it doesn't defeat
    # the warning when only the stick (+ its Steam-virtual shadow) is present.
    return all(_is_xarcade(d) or _is_steam_virtual_pad(d) for d in pads)


def _xarcade_present(devs: list[Device]) -> bool:
    """True if ANY connected real gamepad is the X-Arcade — the inverse signal
    used by the arcade 'no X-Arcade detected' warning."""
    return any(_is_xarcade(d) for d in devs if d.is_joypad and not d.is_sinden)


def _xarcade_warn(sys_entry: dict, devs: list[Device], logger) -> int:
    """X-Arcade presence warning, defaulted BY CATEGORY (override per-system in
    policy via warn_when_only_xarcade / warn_when_no_xarcade):
      • console → warn when ONLY the X-Arcade is present (you likely want a gamepad)
      • arcade  → warn when the X-Arcade is NOT present (arcade wants the stick)
    A system is console XOR arcade, so at most one fires. Returns the dialog exit
    code (0 = Proceed / no warn, 1 = Cancel)."""
    cat = sys_entry.get("category")
    warn_only = sys_entry.get("warn_when_only_xarcade", cat == "console")
    warn_no = sys_entry.get("warn_when_no_xarcade", cat == "arcade")
    if warn_only and _only_xarcade_present(devs):
        logger.warning("only X-Arcade present for a console game; prompting")
        return _show_warning_blocking(
            title="Plug in a gamepad?",
            body=("This is a console game; the X-Arcade is detected but a\n"
                  "regular gamepad gives a better experience.\n"
                  "Press Proceed to play with the X-Arcade, or Cancel to\n"
                  "plug in a controller first."),
            logger=logger,
        )
    if warn_no and not _xarcade_present(devs):
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
# ---------------------------------------------------------------------------

def _setup(ctx: GameContext, logger) -> int:
    policy = _load_policy()
    sys_entry = _resolve_policy(policy, ctx)
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
            default_ports = ["DualSense", "8BitDo", "Xbox", "X-Arcade"]
            sys_entry = {"category": "console",
                         "ports": [list(default_ports), list(default_ports)]}
            logger.info(f"no [systems.{ctx.policy_key}] stanza; defaulting active "
                        f"RetroArch system to console ports {default_ports}")
        else:
            logger.info(f"no policy for system={ctx.policy_key!r} "
                        f"(non-RetroArch/unknown); skipping")
            return 0

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
    if _xarcade_warn(sys_entry, devs, logger) != 0:
        logger.info("user cancelled at X-Arcade presence warning")
        return 1
    # Re-enumerate in case the user plugged something in during the warning
    devs = enumerate_devices()

    # ── per-port resolution (device PINS first, then family priority) ──
    # Hybrid pins: the global [pins] table is the baseline; a per-system
    # [systems.<name>.pins] overrides it per player.
    ports = sys_entry.get("ports", [])
    eff_pins = {**policy.get("pins", {}), **sys_entry.get("pins", {})}
    pinned, pin_claimed = _resolve_pins(eff_pins, devs)
    if pinned:
        logger.info("device pins: "
                    + ", ".join(f"P{p}={d.name}" for p, d in sorted(pinned.items())))
    port_devs = _resolve_ports(ports, devs,
                               preassigned=pinned, preclaimed=pin_claimed)
    # ALL ports bind via RetroArch's class-level reserved_device (vid:pid + name).
    # NOTE (2026-06-05): input_playerN_joypad_index was tried (#37) to exact-pin two
    # IDENTICAL pads, but the router's `js_index` is NOT RetroArch's joypad-enumeration
    # index — a different number space — so it mapped P1/P2 onto absent/wrong pads and
    # left BOTH DualSenses dead in-game and in the RA menu. Reverted to reserved_device:
    # distinct-model pads pin exactly; two IDENTICAL pads both work but may swap P-order
    # across reconnects (RA can't distinguish same vid:pid). Exact identical-pad pinning
    # in RA stays an open problem (would need RA's real enumeration order, not js_index).
    joypad_indices: dict[int, int] = {}
    port_names = {p: _reserve_value(d) for p, d in port_devs.items()}
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

    # ── nothing to write? skip cleanly ──
    if not port_names and not mouse_indices and not joypad_indices:
        logger.info("no port reservations or mouse indices to write; done")
        return 0
    if not core_dirs_for_system(ctx.system):
        logger.info(f"system={ctx.system} has no configured RetroArch core "
                    f"dirs; skipping override write (likely standalone emu)")
        return 0

    written = write_override(
        ctx.system, ctx.rom_basename, port_names, mouse_indices or None,
        port_binds or None, joypad_indices or None,
    )
    logger.info(f"wrote per-game override in {len(written)} core dir(s): "
                + ", ".join(str(p) for p in written))
    return 0


def _cleanup(ctx: GameContext, logger) -> int:
    touched = clear_override(ctx.system, ctx.rom_basename)
    logger.info(f"cleanup touched {len(touched)} files")
    return 0


def _standalone(ctx: GameContext, logger) -> int:
    """Route controllers for a standalone emulator (Cemu / Dolphin), selected
    by the resolved system's `backend` key and configured entirely from the
    matching `[backends.<name>]` table. Invoked at ES-DE game-start (emulator
    closed). Always returns 0 — launch continues regardless (Wii is warn-only
    per the user's choice; Wii U falls back to handheld)."""
    from lib import es_systems          # local import (matches the _setup path at ~L460) —
    #                                     without it the es_systems.* call below raised
    #                                     NameError, silently aborting ALL standalone routing.
    policy = _load_policy()
    sys_entry = _resolve_policy(policy, ctx)
    if sys_entry is None:
        logger.info(f"no policy for system={ctx.policy_key!r}; skipping standalone")
        return 0
    # ── X-Arcade presence warning for STANDALONE systems (daphne/mugen/openbor/
    # model3 + standalone consoles wii/xbox/switch/ps3/wiiu). Gated on the launch
    # command being standalone so RA systems — which also fire this hook — don't
    # double-warn (they warn in _setup). Runs BEFORE the router_skip return so
    # daphne/openbor are covered. The 05 hook is fire-and-forget, so this blocks +
    # lets the user plug the stick in, but can't hard-abort a standalone launch.
    cmd = es_systems.default_command(ctx.system, es_systems.load_systems())
    if es_systems.is_standalone(cmd):
        _xarcade_warn(sys_entry, enumerate_devices(), logger)
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
    pinned, pin_claimed = _resolve_pins(eff_pins, devs)
    if pinned:
        logger.info("device pins: "
                    + ", ".join(f"P{p}={d.name}" for p, d in sorted(pinned.items())))

    if backend == "cemu":
        ports = sys_entry.get("ports", [])
        port_devs = _resolve_ports(ports, devs, with_fallback=False,
                                   preassigned=pinned, preclaimed=pin_claimed)
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

    logger.warning(f"unknown backend {backend!r}; skipping")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("mode",
                   choices=("setup", "cleanup", "standalone", "sdl-ignore",
                            "sdl-ignore-list", "quit-systems", "quit-cmd",
                            "collection-of", "view-collection", "track-view",
                            "splash-collection", "lightgun-rom"))
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
        pol = _load_policy()
        entry = _resolve_system(pol, system) or {}
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
            print(keep_first_present(be.get("pad_classes", []),
                                     be.get("handheld_class", "")))
        else:
            # expose all listed player pads (Supermodel JOY1/JOY2)
            print(keep_except_list(be.get("pad_classes", []),
                                   be.get("handheld_class", ""),
                                   be.get("keep_extra", [])))
        return 0

    # sdl-ignore-list: print an SDL_GAMECONTROLLER_IGNORE_DEVICES *blocklist* (the
    # connected pads to HIDE = everything except the chosen top family) to STDOUT.
    # For Proton/Wine games (OpenBOR) whose winebus ignores the _EXCEPT whitelist
    # but honors the IGNORE blocklist — so the Steam Deck pad + extras are hidden
    # and only the chosen family (e.g. X-Arcade) reaches the game.
    if args.mode == "sdl-ignore-list":
        from lib.sdl_filter import ignore_nonplayers
        system = args.system or args.rom_path or args.name
        pol = _load_policy()
        entry = _resolve_system(pol, system) or {}
        be = pol.get("backends", {}).get(entry.get("backend", ""), {})
        print(ignore_nonplayers(be.get("pad_classes", []),
                                be.get("handheld_class", "")))
        return 0

    # quit-systems  -> the standalone systems (with games) eligible for a hold-to-
    #                  quit combo, one per line (consumed by the config GUI).
    # quit-cmd <sys> -> the shell command that quits that system's emulator, or
    #                  empty if it's a RetroArch/HID/unknown system (consumed by
    #                  the quit-combo-watcher.sh game-start hook). STDOUT only.
    if args.mode in ("quit-systems", "quit-cmd"):
        from lib import es_systems
        pol = _load_policy()
        if args.mode == "quit-systems":
            print("\n".join(es_systems.quit_combo_systems(pol)))
        else:
            system = args.system or args.rom_path or args.name
            print(es_systems.quit_cmd(system, pol))
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
        ent = _load_policy().get("collections", {}).get(name, {})
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
