#!/usr/bin/env bash
# game-start: route controllers for STANDALONE emulators (Cemu/Dolphin/PCSX2/...). The
# router self-filters (returns 0 for RetroArch/backend-less/router_skip systems).
# $1=ROM $2=name $3=system $4=fullname
LOG="$HOME/Emulation/storage/sinden/logs/es-de-hooks.log"; mkdir -p "$(dirname "$LOG")"
echo "[$(date +%H:%M:%S)] controller-router-standalone hook: system='$3'" >> "$LOG"
"$HOME/Emulation/tools/launchers/controller-router.py" standalone "$1" "$2" "$3" "$4" >> "$LOG" 2>&1 \
  || echo "[$(date +%H:%M:%S)]   WARN: standalone routing returned non-zero (launch continues)" >> "$LOG"
exit 0
