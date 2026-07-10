#!/usr/bin/env bash
# game-start: on-the-go internal-resolution downshift for GameCube/Wii (standalone Dolphin).
# Sweeps any crash orphan back to resting, then drops GFX.ini InternalResolution to native when
# handheld (no-op docked / feature-off / non-participating). GC + Wii share one GFX.ini.
# $1=ROM $2=name $3=system $4=fullname
case "$3" in gc|wii) ;; *) exit 0 ;; esac
RT="$HOME/Emulation/tools/launchers"
python3 -c "import sys; sys.path.insert(0,'$RT'); from lib import dolphin_res; dolphin_res.sweep_all(); dolphin_res.apply(sys.argv[1], sys.argv[2])" "$3" "$1" 2>/dev/null
exit 0
