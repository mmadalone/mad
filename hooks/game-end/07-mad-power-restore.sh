#!/usr/bin/env bash
# game-end: restore the pre-launch TDP watt cap (sidecar-gated; a no-op when the
# launch never capped, i.e. docked / disabled / non-participating). Fires on normal
# exit AND on a quit-combo kill (ES-DE runs game-end hooks either way).
# $1=ROM $2=name $3=system $4=fullname
LOG="$HOME/Emulation/storage/controller-router/router.log"; mkdir -p "$(dirname "$LOG")"
RT="$HOME/Emulation/tools/launchers"
python3 -c "import sys; sys.path.insert(0,'$RT'); from lib import deck_power; sys.exit(deck_power.main(sys.argv[1:]))" restore >> "$LOG" 2>&1
exit 0
