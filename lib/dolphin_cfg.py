"""
Dolphin (Wii) DolphinBar reporting for the controller-router.

Real Wii Remotes reach Dolphin through a Mayflash DolphinBar. The Wii Remote SOURCE decision (real /
real2 / Sinden / Classic Controller) now lives in lib.dolphin_wii_source -- the single writer of
WiimoteNew.ini, invoked from the game-start `dolphin-wii-mode.sh` hook, which runs AFTER this and fires
for every Wii launch (including collection games the router skips). So this module no longer writes the
file; it only REPORTS whether a "no DolphinBar" warning is warranted, so the router can surface it.

The warning is SUPPRESSED for a Classic-Controller-capable game with no bar, because that game does not
need a DolphinBar (it falls back to a gamepad). Nothing here is hardcoded -- the real-vs-real2 threshold
comes from `[backends.dolphin]` in controller-policy.toml.
"""
from __future__ import annotations

from .devices import dolphinbar_wiimotes, dolphinbar_present


def route(cfg: dict, require_dolphinbar: bool, logger, rom: str | None = None) -> dict:
    """Report the DolphinBar situation for the caller's warning dialog. It NO LONGER writes
    WiimoteNew.ini -- lib.dolphin_wii_source does, from the game-start hook that runs after this.

    `cfg` is the `[backends.dolphin]` table. Returns a summary whose `warn` is True only when a
    DolphinBar is required but ABSENT and the game is NOT a Classic-Controller fallback (a CC-capable
    game needs no bar). `warn` is never set merely because the remotes are asleep -- they wake on a
    button press at game start; the bar always exposes its fixed slots, the awake-count is probe-based."""
    present = dolphinbar_present()
    n = dolphinbar_wiimotes()
    real2_min = int(cfg.get("real2_min_wiimotes", 2))
    mode = "real2" if n >= real2_min else "real"      # informational only (the hook decides + applies)

    cc = False
    if not present and rom:
        try:
            from lib.dolphin_wii_tdb import is_cc_capable
            cc = is_cc_capable(rom)
        except Exception:
            cc = False

    summary = {"wiimotes": n, "mode": mode,
               "warn": bool(require_dolphinbar and not present and not cc)}
    if summary["warn"]:
        logger.warning("dolphin: require_dolphinbar set but NO Wiimote connected via DolphinBar")
    elif not present and cc:
        logger.info("dolphin: no DolphinBar; Classic-Controller-capable game -> gamepad fallback")
    return summary
