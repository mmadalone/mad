"""
Dolphin (Wii) controller-routing backend for the controller-router.

Real Wii Remotes reach Dolphin through a Mayflash DolphinBar. This backend only
handles NON-lightgun Wii games (lightgun/Pew-Pew Wii games keep the existing
`dolphin-wii-mode.sh sinden` path). It:

  * counts the Wii Remotes currently connected through the DolphinBar,
  * picks "real" (1 remote slot) vs "real2" (>= real2_min_wiimotes) and applies
    it via the existing `dolphin-wii-mode.sh` tool — the single writer of
    WiimoteNew.ini, so no two scripts fight over `Source =`,
  * reports back whether a "no DolphinBar / no Wiimote" warning is warranted
    (the caller shows it; the launch always continues, per the user's choice).

Nothing here is hardcoded — the tool path and the real-vs-real2 threshold come
from `[backends.dolphin]` in controller-policy.toml.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from .devices import dolphinbar_wiimotes, dolphinbar_present


def _expand(p: str) -> Path:
    return Path(p).expanduser()


def route(cfg: dict, require_dolphinbar: bool, logger) -> dict:
    """Apply the Wii Remote source mode from the connected-Wiimote count.

    `cfg` is the [backends.dolphin] table. Returns a summary dict including
    `warn` = True when require_dolphinbar is set but no Wiimote is connected
    (the caller is responsible for surfacing the warning)."""
    present = dolphinbar_present()
    n = dolphinbar_wiimotes()
    real2_min = int(cfg.get("real2_min_wiimotes", 2))
    tool = _expand(cfg.get(
        "wii_mode_tool",
        str(Path(__file__).resolve().parent.parent / "dolphin-wii-mode.sh")))

    mode = "real2" if n >= real2_min else "real"
    # Warn only when the DolphinBar itself is ABSENT — NOT when it's present but
    # the remote(s) are merely asleep (they wake on a button press at game start;
    # the bar always exposes its fixed slots, the awake-count is probe-based).
    summary = {"wiimotes": n, "mode": mode,
               "warn": bool(require_dolphinbar and not present)}

    if not tool.is_file():
        logger.warning(f"dolphin: wii_mode_tool {tool} not found; "
                       "leaving WiimoteNew.ini untouched")
        return summary

    try:
        subprocess.run([str(tool), mode], check=False)
        logger.info(f"dolphin: {n} Wiimote(s) via DolphinBar -> mode {mode!r}")
    except OSError as ex:
        logger.warning(f"dolphin: failed to run {tool} {mode}: {ex!r}")

    if summary["warn"]:
        logger.warning("dolphin: require_dolphinbar set but NO Wiimote "
                       "connected via DolphinBar")
    return summary
