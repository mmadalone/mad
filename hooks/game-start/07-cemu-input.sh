#!/usr/bin/env bash
# game-start: on-the-go handheld controller swap for Wii U (Cemu). When undocked + on-the-go
# enabled + wiiu participating + a handheld_profile set, swap controller0.xml (the GamePad) to the
# saved handheld profile so the Deck's built-in pad drives the game; reverted on exit by
# game-end/09-cemu-input-restore.sh. No-op docked / feature-off / non-participating / no profile.
# apply() sweeps any crash-orphaned swap back to the resting profile first.
# $1=ROM $2=name $3=system $4=fullname
case "$3" in wiiu) ;; *) exit 0 ;; esac
RT="$HOME/Emulation/tools/launchers"
python3 -c "import sys; sys.path.insert(0,'$RT'); from lib import cemu_input_dock; print('mad-cemu:', cemu_input_dock.apply())" 2>/dev/null
exit 0
