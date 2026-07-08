#!/usr/bin/env bash
# Revert the transient GameCube (standalone Dolphin) controller swap after a game exits.
# No-op unless controller-router applied a swap this session (a gc game launched handheld with the
# undocked profile, OR docked with a "pads -> players" order). Safe for every game (restore() checks
# the snapshot and no-ops when absent).
_L="$HOME/Emulation/tools/launchers"
python3 -c 'import sys; sys.path.insert(0, "'"$_L"'"); from lib import dolphin_gc_dock as d; d.restore()' 2>/dev/null
exit 0
