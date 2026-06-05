"""
xemu (Original Xbox) controller-assignment backend for the controller-router.

xemu binds each console port to a controller by its **SDL GUID**, in the
`[input.bindings]` table of its TOML config:

    [input.bindings]
    port1 = '03000000...'   # SDL GUID of the Player 1 pad
    port2 = '4c050000...'   # Player 2, etc. (absent key = port unbound)

GUID is the device CLASS (vendor/product/crc), so two identical pads share one
GUID — xemu then fills the ports in order from the matching devices (so two PS4
controllers → port1 + port2 works; ordering between identical pads falls to
power-on order, the usual caveat). Distinct models pin cleanly.

This backend picks the connected PlayStation pads (PS4 treated like DualSense,
by vid:pid in `pad_classes`) via `devices.sdl_devices()` and writes port1..N to
their GUIDs; no pad → bind port1 to `handheld_class` (Steam Deck) or leave the
file untouched. xemu rewrites xemu.toml on exit, so we edit while it's closed
(guaranteed at ES-DE game-start) and keep a one-time backup. Everything comes
from `[backends.xemu]` in controller-policy.toml.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from . import inifile
from .devices import sdl_devices

_PORT_RE = re.compile(r"\s*port\d+\s*=")


def _expand(p: str) -> Path:
    return Path(p).expanduser()


def assign(cfg: dict, logger, devs=None, pins=None) -> int:
    """Apply the Xbox pad assignment. Returns 0 (launch always continues).

    `pins` ({player: evdev Device}) + `devs` let a GLOBAL pin set a port's pad.
    NOTE: xemu binds by SDL GUID (device CLASS), so a pin selects the port's pad
    MODEL — two identical-model pads can't be told apart here (xemu fills them in
    SDL order). Cross-model pins (e.g. P1=8BitDo, P2=DualSense) ARE honored."""
    path = _expand(cfg.get(
        "config_file", "~/.var/app/app.xemu.xemu/data/xemu/xemu/xemu.toml"))
    manage = int(cfg.get("manage_ports", 4))
    pad_classes: list[str] = list(cfg.get("pad_classes", []))
    handheld = cfg.get("handheld_class", "")

    if not path.is_file():
        logger.warning(f"xemu: config file {path} not found; skipping")
        return 0

    sdl = sdl_devices()
    if not sdl:
        logger.warning("xemu: SDL enumerated no joysticks; leaving xemu.toml")
        return 0

    prio = {c: i for i, c in enumerate(pad_classes)}
    ps = sorted((d for d in sdl if d.vidpid in prio),
                key=lambda d: (prio[d.vidpid], d.index))

    # Global device pins -> the pinned pad's class GUID (override the fill below).
    pinned_guid: dict[int, str] = {}
    if pins and devs:
        from .devices import vidpid as _vp
        for port, dev in pins.items():
            if port > manage:
                continue
            g = next((s.guid for s in sdl if s.vidpid == _vp(dev)), None)
            if g:
                pinned_guid[port] = g

    assigned: dict[int, str] = {}   # port (1-based) -> SDL GUID
    if ps:
        for k in range(1, manage + 1):
            if k - 1 < len(ps):
                assigned[k] = ps[k - 1].guid
        logger.info("xemu: ports -> "
                    + ", ".join(f"port{k}={g[:12]}…" for k, g in assigned.items()))
    elif not pinned_guid:
        deck = next((d for d in sdl if d.vidpid == handheld), None)
        if not handheld or deck is None:
            logger.info("xemu: no PlayStation pad and no handheld device; "
                        "leaving xemu.toml untouched")
            return 0
        assigned[1] = deck.guid
        logger.info(f"xemu: no PlayStation pad -> port1={deck.guid[:12]}… (handheld)")

    for port, g in sorted(pinned_guid.items()):    # pins win (class-level)
        assigned[port] = g
    if pinned_guid:
        logger.info("xemu: pins -> "
                    + ", ".join(f"port{k}={g[:12]}…" for k, g in sorted(pinned_guid.items())))

    text = path.read_text(encoding="utf-8")
    # Preserve any non-port keys already in [input.bindings]; replace the portN set.
    body = inifile.section_body(text, "input.bindings") or ""
    keep = [ln for ln in body.splitlines() if ln.strip() and not _PORT_RE.match(ln)]
    new_lines = keep + [f"port{k} = '{g}'" for k, g in sorted(assigned.items())]

    backup = path.with_name(path.name + ".router-backup")
    if not backup.exists():
        shutil.copy2(path, backup)
        logger.info(f"xemu: one-time backup -> {backup.name}")

    text = inifile.set_section(text, "input.bindings", "\n".join(new_lines))
    path.write_text(text, encoding="utf-8")
    logger.info(f"xemu: wrote {path}")
    return 0
