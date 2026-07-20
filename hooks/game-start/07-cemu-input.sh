#!/usr/bin/env bash
# game-start: family x context controller seating for Wii U (Cemu). lib/cemu_seat seats each pad's
# assigned profile ([backends.cemu.profile_map.<context>]) into its controllerN.xml, re-pinned, and
# reverts on exit (game-end/09-cemu-input-restore.sh). With seating_enabled=false it delegates to the
# legacy single-slot handheld swap (today's behaviour). apply() heals any orphaned seat first.
# $1=ROM $2=name $3=system $4=fullname
case "$3" in wiiu) ;; *) exit 0 ;; esac
RT="$HOME/Emulation/tools/launchers"
python3 -c "import sys; sys.path.insert(0,'$RT'); from lib import cemu_seat; print('mad-cemu:', cemu_seat.apply())" 2>/dev/null
exit 0
