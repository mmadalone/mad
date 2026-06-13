#!/usr/bin/env python3
"""
Detect and pin the Sinden P1/P2 mouse_index values in RetroArch's global
retroarch.cfg. Called from sinden-start.sh after the smoother is up.

The actual device-enumeration logic now lives in lib/devices.py so it can be
shared with controller-router.py. This file is the thin caller that updates
the global retroarch.cfg in place — for per-game overrides, controller-router
writes the same values into the per-game .cfg instead.
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from lib.devices import detect_sinden_mouse_indices  # noqa: E402
from lib import fsutil  # noqa: E402

CFG = Path.home() / ".var/app/org.libretro.RetroArch/config/retroarch/retroarch.cfg"


def update_retroarch_cfg(p1_idx: int, p2_idx: int) -> bool:
    if p1_idx is None or p2_idx is None:
        print(f"[mouse-idx] WARN P1={p1_idx} P2={p2_idx}; not touching cfg",
              file=sys.stderr)
        return False
    if not CFG.exists():
        print(f"[mouse-idx] retroarch.cfg not found at {CFG}; skipping",
              file=sys.stderr)
        return False
    text = CFG.read_text()
    new = re.sub(
        r'^input_player1_mouse_index\s*=.*$',
        f'input_player1_mouse_index = "{p1_idx}"',
        text, count=1, flags=re.MULTILINE,
    )
    new = re.sub(
        r'^input_player2_mouse_index\s*=.*$',
        f'input_player2_mouse_index = "{p2_idx}"',
        new, count=1, flags=re.MULTILINE,
    )
    if new != text:
        fsutil.atomic_write(CFG, new)
    return True


def main():
    p1, p2, using_smoothed = detect_sinden_mouse_indices()
    src = "smoothed virtual" if using_smoothed else "raw Sinden"
    if p1 is not None and p2 is not None:
        update_retroarch_cfg(p1, p2)
        print(f"[mouse-idx] retroarch.cfg: P1={p1} P2={p2} (from {src})",
              file=sys.stderr)
    else:
        print(f"[mouse-idx] could not find {src} devices: P1={p1} P2={p2}",
              file=sys.stderr)


if __name__ == "__main__":
    main()
