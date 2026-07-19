"""mugen_cfg - write the canonical Ikemen GO joystick config for the MAD merger.

When the merger is active the game sees N identical CANONICAL twins (recognised SDL
GameControllers, one per player in OUR seat order), so every seat takes the SAME
standard binding, seated by index (Joystick = 0..3). This OVERWRITES any per-game
hand-tuning of the [Joystick_Pn] blocks -- e.g. a game de-rotated for the RAW
X-Arcade (up = DP_L ...), which is WRONG for the twin -- with the standard layout
that is correct for the twin (and for the Steam Deck pad on a handheld launch, which
is itself a standard GameController). Directions bind to the d-pad (DP_*), which is
the twin's hat: the merger feeds it from BOTH the real d-pad AND the digitised analog
stick, so a gamepad stick and the X-Arcade both drive movement through one binding.

Byte-preserving: only the [Joystick_Pn] value tokens change (comments, other sections,
[Keys_*] keyboard fallback, alignment all untouched). The engine rewrites config.ini
on exit, so this launch-time write is authoritative for the session; no restore.

CLI (called by mugen.sh on a canonical launch):
    python3 -m lib.mugen_cfg apply <path-to-save/config.ini>
"""
from __future__ import annotations

import sys
from pathlib import Path

try:
    from .madsrv import cfgutil
except ImportError:                       # run as a plain script
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from lib.madsrv import cfgutil

MAX_PLAYERS = 4

# The standard Ikemen GO gamepad binding (its own defaultConfig.ini [Joystick_Pn]
# layout): directions on the d-pad, 6 fighter buttons + shoulders/triggers, menu.
CANON = [
    ("up", "DP_U"), ("down", "DP_D"), ("left", "DP_L"), ("right", "DP_R"),
    ("a", "A"), ("b", "B"), ("c", "RT"), ("x", "X"), ("y", "Y"), ("z", "RB"),
    ("start", "START"), ("d", "LB"), ("w", "LT"), ("menu", "BACK"),
]


def apply(config_ini: str | Path) -> str:
    """Write the canonical [Joystick_P1..P4] blocks. Returns a launcher-log status:
    applied | unchanged | skip-no-config. Never raises for a missing section/key."""
    config_ini = Path(config_ini)
    text = cfgutil.read_text(config_ini)
    if text is None:
        return "skip-no-config"           # not launched yet; the engine makes it first
    orig = text
    for n in range(1, MAX_PLAYERS + 1):
        sec = f"Joystick_P{n}"
        nt = cfgutil.ini_set_or_insert(text, sec, "Joystick", str(n - 1))
        if nt is None:
            continue                      # this build has no [Joystick_Pn] section
        text = nt
        for key, tok in CANON:
            nt = cfgutil.ini_set_or_insert(text, sec, key, tok)
            if nt is not None:
                text = nt
    if text == orig:
        return "unchanged"
    cfgutil.ensure_bak(config_ini)
    cfgutil.atomic_write(config_ini, text)
    return "applied"


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[0] == "apply":
        p = Path(argv[1])
        print(f"mugen_cfg apply {p.parent.parent.name}: {apply(p)}")
        return 0                          # a skip is expected (first run), not an error
    print("usage: mugen_cfg apply <path-to-save/config.ini>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
