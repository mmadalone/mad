#!/usr/bin/env bash
# Revert the transient Wii Classic-Controller swap after a game exits.
# No-op unless lib.dolphin_wii_source applied a Classic Controller layout this session (a no-bar Wii
# game that GameTDB says supports a Classic Controller). Safe for every game and every system: restore()
# checks for the WiimoteNew.ini.cc-backup snapshot and does nothing when it is absent (real / real2 /
# Sinden launches never create one). Runs after Dolphin exits (Dolphin rewrites its config on exit).
_L="$HOME/Emulation/tools/launchers"
python3 -c 'import sys; sys.path.insert(0, "'"$_L"'"); from lib import dolphin_wii_source as d; d.restore()' 2>/dev/null
exit 0
