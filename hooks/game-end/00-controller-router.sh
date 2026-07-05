#!/usr/bin/env bash
# game-end: strip the controller-router's per-game sentinel block so the next launch
# starts clean. $1=ROM $2=name $3=system $4=fullname
LOG="$HOME/Emulation/storage/controller-router/router.log"; mkdir -p "$(dirname "$LOG")"
exec "$HOME/Emulation/tools/launchers/controller-router.py" cleanup "$1" "$2" "$3" "$4" >>"$LOG" 2>&1
