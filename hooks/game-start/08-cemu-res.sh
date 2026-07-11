#!/usr/bin/env bash
# game-start: on-the-go handheld RESOLUTION downshift for Wii U (Cemu). When undocked + on-the-go
# enabled + wiiu participating + the launching game has an ENABLED resolution graphic pack AND a
# handheld preset configured for it (On-the-go page), switch that pack's preset to the chosen one;
# reverted on exit by game-end/10-cemu-res-restore.sh. No-op otherwise. sweep_all() self-heals a
# crash orphan first. INDEPENDENT of the input rail (07-cemu-input.sh) -- a torn input backup must
# never drop the res revert. Cemu is CLOSED at game-start, so editing settings.xml here is safe
# (Cemu rewrites it on exit). $1=ROM $2=name $3=system $4=fullname
case "$3" in wiiu) ;; *) exit 0 ;; esac
RT="$HOME/Emulation/tools/launchers"
python3 -c "import sys; sys.path.insert(0,'$RT'); from lib import cemu_res; cemu_res.sweep_all(); cemu_res.apply(sys.argv[1])" "$1" 2>/dev/null
exit 0
