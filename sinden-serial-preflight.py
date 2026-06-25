#!/usr/bin/env python3
"""
Pin LightgunMono's Player 1 / Player 2 assignment to USB Product ID instead
of USB enumeration order.

Why this exists:
   LightgunMono picks the gun for "Player 1" by walking ttyACM devices and
   taking the lowest-numbered one that is a Sinden Lightgun (the comment on
   SerialPortWrite=0 in LightgunMono.exe.config). The lowest ttyACM number
   isn't stable across reboots — depending on USB hub timing, the gun with
   PID 0f39 may enumerate before 0f38, or vice versa.

   The rest of the pipeline — udev symlinks, sinden-smoother.py, Dolphin
   WiimoteNew.ini bindings — is PID-pinned. When ttyACM enumeration order
   contradicts PID order, aim/trigger stay correct (they ride the PID-pinned
   smoother → uinput path) but side-button assignment flips, because those
   come from the gun firmware's keyboard interface and the driver tells
   each gun which keycodes to emit based on its tty-order role.

What this does:
   1. Reads /dev/sinden-tty-p{1,2} (udev symlinks pinned to PIDs 0f38 / 0f39).
   2. Enumerates all Sinden ttyACMs in the same order LightgunMono does
      (sorted ascending by /dev/ttyACMN number).
   3. Writes SerialPortWrite (P1's index in that list) and SerialPortWriteP2
      (P2's index) into LightgunMono.exe.config so the driver assigns the
      correct gun to each player regardless of which one enumerated first.

Idempotent: re-running with the right values already in place is a no-op.
Safe-by-default: if either symlink is missing (gun unplugged, udev not
loaded), the script leaves the config alone and exits 0 so the rest of
sinden-start.sh proceeds.
"""
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib import fsutil  # noqa: E402

CONFIG = Path.home() / "Lightgun/LightgunMono.exe.config"
SYM_P1 = Path("/dev/sinden-tty-p1")  # PID 0f38
SYM_P2 = Path("/dev/sinden-tty-p2")  # PID 0f39
TTY_RE = re.compile(r"^/dev/ttyACM(\d+)$")


def log(msg):
    print(f"[serial-preflight] {msg}", file=sys.stderr)


def resolve_symlink(p):
    """Resolve /dev/sinden-tty-pN to /dev/ttyACMN. Returns None if missing."""
    try:
        return p.resolve(strict=True)
    except (FileNotFoundError, OSError):
        return None


def acm_num(path):
    """Extract the integer N from /dev/ttyACMN. Returns None on mismatch."""
    m = TTY_RE.match(str(path))
    return int(m.group(1)) if m else None


def patch_value(text, key, new_value):
    """Replace <add key="KEY" value="..."/> with the new value. Preserve
    surrounding whitespace and any trailing comment on the same line."""
    pattern = re.compile(
        r'(<add key="' + re.escape(key) + r'"\s+value=")([^"]*)(")'
    )
    new_text, count = pattern.subn(rf'\g<1>{new_value}\g<3>', text, count=1)
    if count == 0:
        raise RuntimeError(f"key not found in config: {key}")
    return new_text


def main():
    p1_tty = resolve_symlink(SYM_P1)
    p2_tty = resolve_symlink(SYM_P2)

    if not p1_tty and not p2_tty:
        log("neither /dev/sinden-tty-p1 nor /dev/sinden-tty-p2 present — guns unplugged? skipping")
        return 0
    if not p1_tty or not p2_tty:
        log(f"only one gun present (p1={p1_tty}, p2={p2_tty}) — leaving config alone")
        return 0

    # Sort all Sinden ttyACMs the way LightgunMono does: ascending by /dev/ttyACM<N>.
    sinden_ttys = sorted([p1_tty, p2_tty], key=lambda p: acm_num(p) or 0)
    log(f"Sinden ttyACMs in driver-order: {[str(t) for t in sinden_ttys]}")

    try:
        p1_idx = sinden_ttys.index(p1_tty)
        p2_idx = sinden_ttys.index(p2_tty)
    except ValueError as e:
        log(f"unexpected: gun tty not in sorted list — skipping ({e})")
        return 0

    log(f"P1 (PID 0f38) -> {p1_tty} -> driver index {p1_idx}")
    log(f"P2 (PID 0f39) -> {p2_tty} -> driver index {p2_idx}")

    if not CONFIG.exists():
        log(f"FATAL: {CONFIG} missing")
        return 1

    text = CONFIG.read_text()
    new_text = patch_value(text, "SerialPortWrite", p1_idx)
    new_text = patch_value(new_text, "SerialPortWriteP2", p2_idx)

    if new_text == text:
        log("config already correct — no write needed")
        return 0

    # Atomic write + one-time backup (recoverable prior known-good), crash-safe: the backup
    # copy precedes the atomic swap, so a mid-write crash never leaves a half-written config.
    fsutil.atomic_write_text(CONFIG, new_text, backup_once_suffix=".pre-preflight")
    log(f"patched: SerialPortWrite={p1_idx}, SerialPortWriteP2={p2_idx}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
