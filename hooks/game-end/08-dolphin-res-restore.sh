#!/usr/bin/env bash
# game-end: revert the GameCube/Wii (standalone Dolphin) handheld internal-resolution downshift
# (sidecar-gated no-op; also self-heals a crashed handheld session on any exit).
# $1=ROM $2=name $3=system $4=fullname
case "$3" in gc|wii) ;; *) exit 0 ;; esac
RT="$HOME/Emulation/tools/launchers"
python3 -c "import sys; sys.path.insert(0,'$RT'); from lib import dolphin_res; dolphin_res.sweep_all()" 2>/dev/null
exit 0
