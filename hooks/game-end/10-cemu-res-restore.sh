#!/usr/bin/env bash
# game-end: revert the Wii U (Cemu) handheld resolution downshift (marker-gated no-op; also
# self-heals a crashed handheld session on any exit). Reverts the resolution graphic pack to its
# resting preset ONLY if it still holds the one we applied. Cemu is closed at game-end.
# $1=ROM $2=name $3=system $4=fullname
case "$3" in wiiu) ;; *) exit 0 ;; esac
RT="$HOME/Emulation/tools/launchers"
python3 -c "import sys; sys.path.insert(0,'$RT'); from lib import cemu_res; cemu_res.sweep_all()" 2>/dev/null
exit 0
