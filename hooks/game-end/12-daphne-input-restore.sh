#!/usr/bin/env bash
# game-end: revert the handheld Daphne hypinput swap -- restore the docked hypinput.ini from the rail
# backup. Ungated (sweep is a no-op unless a swap is pending) so a crash orphan always heals.
# $1=ROM $2=name $3=system $4=fullname
RT="$HOME/Emulation/tools/launchers"
python3 -c "import sys; sys.path.insert(0,'$RT'); from lib import daphne_input; daphne_input.sweep()" 2>/dev/null
exit 0
