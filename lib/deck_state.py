"""Physical docked/handheld state for the Deck, with a manual override.

Single source of truth for "am I docked or handheld", used by the on-the-go
auto-profile feature (resolution downshift + TDP watt cap + built-in-pad routing).

Detection is pure sysfs (DRM connectors), so it works in every launch context
(Game Mode gamescope, ES-DE handheld, Steam UI handheld) where compositor tools
(xrandr / gamescopectl) are absent or broken from a launch shell. The Deck's dock
is DisplayPort; an external HDMI adapter shows as card*-HDMI*. The internal panel
(eDP, always "connected" because it is soldered) is excluded.

`resolve_force(handheld_cfg)` turns the merged policy's [handheld] table into an
override token so callers stay dependency-free: this module never imports the
policy loader itself. Override precedence in is_docked() (first that applies wins):
  1. env MAD_FORCE_CONTEXT = "handheld" | "docked"   (test hook / scripts)
  2. explicit `force` argument
  3. physical DRM connector state
Callers pass resolve_force([handheld] cfg) as `force` to fold policy in.
"""
from __future__ import annotations

import glob
import os


def _read(path: str) -> str:
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return ""


def external_display_connected() -> bool:
    """True if an external HDMI/DP display is connected AND lit (the dock, or an
    HDMI adapter). Hardened past a bare 'connected': a plugged-but-dark TV keeps
    HPD asserted, so we also require the connector to be 'enabled' where that
    attribute exists. The always-'connected' internal eDP panel is excluded by the
    glob (it is card*-eDP*, not HDMI/DP)."""
    for st in (glob.glob("/sys/class/drm/card*-HDMI*/status")
               + glob.glob("/sys/class/drm/card*-DP*/status")):
        if _read(st) != "connected":
            continue
        en = _read(st.rsplit("/", 1)[0] + "/enabled")   # 'enabled' / 'disabled' / ''
        if en in ("enabled", ""):        # '' = node lacks the file -> trust status
            return True
    # Secondary guard: if the internal panel is explicitly dark we are docked, even
    # if the loop above somehow missed the external connector.
    for edp in glob.glob("/sys/class/drm/card*-eDP*/enabled"):
        if _read(edp) == "disabled":
            return True
    return False


def resolve_force(handheld_cfg: dict | None) -> str | None:
    """Map a merged-policy [handheld] table to an override token for is_docked():
      force = "handheld"|"docked"  -> that token (explicit user override)
      detect = "manual" (no force) -> "docked" (manual mode = no auto profile)
      otherwise (detect "display") -> None (let the DRM check decide)
    Pure dict logic; no imports, so deck_state stays dependency-free. A non-dict
    (from a malformed hand-edited TOML scalar) degrades to no override."""
    cfg = handheld_cfg if isinstance(handheld_cfg, dict) else {}
    force = str(cfg.get("force", "")).strip().lower()
    if force in ("handheld", "docked"):
        return force
    if str(cfg.get("detect", "display")).strip().lower() == "manual":
        return "docked"
    return None


def is_docked(force: str | None = None) -> bool:
    """True = docked (external display), False = handheld. See module docstring
    for override precedence."""
    env = os.environ.get("MAD_FORCE_CONTEXT", "").strip().lower()
    if env in ("handheld", "docked"):
        return env == "docked"
    if force in ("handheld", "docked"):
        return force == "docked"
    return external_display_connected()


def is_handheld(force: str | None = None) -> bool:
    return not is_docked(force)


if __name__ == "__main__":                # tiny CLI for headless testing / hooks
    import sys
    arg = sys.argv[1] if len(sys.argv) > 1 else "state"
    if arg in ("state", "docked", "handheld"):
        print("docked" if is_docked() else "handheld")
    elif arg == "external":
        print("connected" if external_display_connected() else "none")
    else:
        print("usage: deck_state.py [state|external]", file=sys.stderr)
        sys.exit(2)
