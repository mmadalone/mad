#!/usr/bin/env bash
# game-start: handheld-only Deck input for Daphne/Hypseus. When handheld, swap the Deck map
# (hypinput.deck.ini) in for the shared hypinput.ini (docked map backed up + restored on game-end),
# so the built-in pad's coin/start/buttons work handheld without touching the docked X-Arcade map.
# No-op docked / non-daphne. Sweeps a crash orphan first.
# $1=ROM $2=name $3=system $4=fullname
case "$3" in daphne) ;; *) exit 0 ;; esac
RT="$HOME/Emulation/tools/launchers"
python3 -c "import sys; sys.path.insert(0,'$RT'); from lib import daphne_input; daphne_input.apply()" 2>/dev/null
exit 0
