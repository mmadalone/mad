#!/usr/bin/env bash
LOG=$HOME/Emulation/storage/sinden/logs/es-de-hooks.log
echo "[$(date +%H:%M:%S)] game-end args: $*" >> "$LOG"
$HOME/Emulation/tools/launchers/sinden-stop.sh >> "$LOG" 2>&1 || true
exit 0
