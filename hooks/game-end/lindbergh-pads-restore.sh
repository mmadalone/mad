#!/usr/bin/env bash
# ES-DE game-end hook — restore the game's lindbergh.ini from <ini>.mad-restore after a
# per-game "pads -> players" launch (undoes the transient materialization the game-start
# hook applied). No-op if there's no backup (the game wasn't materialized).
# See hooks/game-start/lindbergh-pads-apply.sh + lib/lindbergh_pads.py.
#
# ES-DE args: $1=ROM  $2=name  $3=system  $4=fullname
ROM="${1//\\/}"
SYSTEM="$3"
[ "$SYSTEM" = "lindbergh" ] || exit 0
LAUNCHERS="$HOME/Emulation/tools/launchers"
LOG="$HOME/Emulation/storage/sinden/logs/es-de-hooks.log"
echo "[$(date +%H:%M:%S)] lindbergh-pads restore: $ROM" >> "$LOG"
( cd "$LAUNCHERS" && python3 -m lib.lindbergh_pads restore "$ROM" ) >> "$LOG" 2>&1 || true
exit 0
