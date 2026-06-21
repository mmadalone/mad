#!/usr/bin/env bash
# Stop the configurable hold-to-quit combo watcher when a game exits.
LOG=$HOME/Emulation/storage/sinden/logs/es-de-hooks.log
PIDF="$HOME/Emulation/storage/sinden/quit-combo-watcher.pid"
[ -f "$PIDF" ] && kill "$(cat "$PIDF" 2>/dev/null)" 2>/dev/null
pkill -f quit-combo-watcher.py 2>/dev/null
rm -f "$PIDF"
echo "[$(date +%H:%M:%S)] stopped quit-combo-watcher" >> "$LOG"
exit 0
