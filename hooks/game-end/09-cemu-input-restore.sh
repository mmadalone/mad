#!/usr/bin/env bash
# Revert the transient Wii U (Cemu) controller seating after the game exits. No-op unless a seat was
# applied this session (restore() reverts both the new family seats and the legacy handheld swap; it
# no-ops when no snapshot is present). game-start apply() also heals orphans, so a missed game-end
# self-heals on the next launch.
# $1=ROM $2=name $3=system $4=fullname
case "$3" in wiiu) ;; *) exit 0 ;; esac
RT="$HOME/Emulation/tools/launchers"
python3 -c "import sys; sys.path.insert(0,'$RT'); from lib import cemu_seat; cemu_seat.restore()" 2>/dev/null
exit 0
