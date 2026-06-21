#!/usr/bin/env bash
# Stop the Wii Remote +&- quit watcher when a game exits.
LOG=$HOME/Emulation/storage/sinden/logs/es-de-hooks.log
PIDF="$HOME/Emulation/storage/sinden/wiimote-quit-watcher.pid"
[ -f "$PIDF" ] && kill "$(cat "$PIDF" 2>/dev/null)" 2>/dev/null
pkill -f wiimote-quit-watcher.py 2>/dev/null
rm -f "$PIDF"
echo "[$(date +%H:%M:%S)] stopped wiimote-quit-watcher" >> "$LOG"
exit 0
