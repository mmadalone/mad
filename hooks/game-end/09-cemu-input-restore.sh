#!/usr/bin/env bash
# Revert the transient Wii U (Cemu) handheld controller swap after the game exits. No-op unless a
# swap was applied this session (restore() checks the co-located snapshot and no-ops when absent).
# The game-start apply() also sweeps orphans, so a missed game-end self-heals on the next launch.
# $1=ROM $2=name $3=system $4=fullname
case "$3" in wiiu) ;; *) exit 0 ;; esac
RT="$HOME/Emulation/tools/launchers"
python3 -c "import sys; sys.path.insert(0,'$RT'); from lib import cemu_input_dock; cemu_input_dock.restore()" 2>/dev/null
exit 0
