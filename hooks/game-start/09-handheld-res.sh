#!/usr/bin/env bash
# game-start: backend-aware on-the-go internal-resolution downshift.
# Sweeps any crash orphan back to resting, then downshifts the launching game's internal/upscale
# resolution on WHICHEVER emulator it actually runs with -- a specific RetroArch core OR a standalone
# -- when handheld. No-op docked / feature-off / non-participating system / unsupported backend.
# Supersedes the old per-emulator res rails (RA in-process, PS2/PS3 in switch_bind, Dolphin hook 06).
# $1=ROM $2=name $3=system $4=fullname
case "$3" in psx|n64|saturn|dreamcast|naomi|atomiswave|gc|wii|ps2|ps3) ;; *) exit 0 ;; esac
RT="$HOME/Emulation/tools/launchers"
python3 -c "import sys; sys.path.insert(0,'$RT'); from lib import handheld_res; handheld_res.sweep_all(); handheld_res.apply(sys.argv[1], sys.argv[2])" "$3" "$1" 2>/dev/null
exit 0
