#!/usr/bin/env bash
# game-end: revert any on-the-go internal-resolution downshift to the docked resting value.
# Ungated so an orphan from ANY backend is healed (revert-if-unchanged leaves a mid-session edit).
# $1=ROM $2=name $3=system $4=fullname
RT="$HOME/Emulation/tools/launchers"
python3 -c "import sys; sys.path.insert(0,'$RT'); from lib import handheld_res; handheld_res.sweep_all()" 2>/dev/null
exit 0
