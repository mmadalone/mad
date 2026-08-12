#!/usr/bin/env python3
"""ES-DE launch wrapper for Switch games — a forwarding shim.

mad-standalone-launch.py is the single launch binder now (it was always a
strict superset of this file: the only functional delta, the pcsx2/rpcs3
device-visibility blacklist, is gated on the emu argument and a no-op for
eden/citron/ryujinx; everything else, including the ryujinx bundled-libSDL2
probe and --restore-all, is identical). This file STAYS, as a shim, because
live es_systems.xml commands, lib/mad_launch_wrap.py's W constant and its
idempotence lookahead, and lib/es_systems_standalone.py's seeder all name it —
repointing those instead would arm the double-wrap trap the 2026-08-12 audit
flagged (a changed W stops matching already-wrapped commands and the next
post-update wrap pass wraps them AGAIN).

os.execv keeps the process tree flat: the shim becomes the binder, the binder
becomes the emulator, so ES-DE waits on the game itself and the quit-combo's
pkill patterns (keyed on the emu names) behave exactly as before. Cost: one
extra ~20 ms interpreter start per Switch launch.
"""
import os
import sys
from pathlib import Path

_TARGET = Path(__file__).resolve().parent / "mad-standalone-launch.py"
try:
    os.execv(str(_TARGET), [str(_TARGET)] + sys.argv[1:])
except OSError as e:
    # stderr is lost in Game Mode (same rationale as the binder's own exec
    # guard) — leave a trace in router.log, best-effort, without importing the
    # possibly-broken tree beyond mad_paths.
    sys.stderr.write(f"mad-switch-launch: exec of {_TARGET} FAILED ({e!r})\n")
    try:
        sys.path.insert(0, str(_TARGET.parent))
        from lib import mad_paths
        log = mad_paths.storage("controller-router") / "router.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, "a", encoding="utf-8") as f:
            f.write(f"mad-switch-launch: exec of {_TARGET} FAILED ({e!r})\n")
    except Exception:
        pass
    sys.exit(127)
