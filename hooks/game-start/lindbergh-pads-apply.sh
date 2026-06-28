#!/usr/bin/env bash
# ES-DE game-start hook — materialize the per-game Lindbergh "pads -> players" profile
# into the game's lindbergh.ini BEFORE the loader reads it (game-start hooks run before
# the es_systems launch command). Resolves the connected pads against the per-game
# priority and writes each player slot from the chosen pad's own button map, after
# backing the ini up to <ini>.mad-restore. No-op when the game has no per-pad config or
# none of its configured pads are connected (the ini's classic bindings then stand).
# Restored by hooks/game-end/lindbergh-pads-restore.sh. See lib/lindbergh_pads.py.
#
# ES-DE args: $1=ROM  $2=name  $3=system  $4=fullname
ROM="${1//\\/}"      # strip ES-DE's backslash escapes
SYSTEM="$3"
[ "$SYSTEM" = "lindbergh" ] || exit 0
LAUNCHERS="$HOME/Emulation/tools/launchers"
LOG="$HOME/Emulation/storage/sinden/logs/es-de-hooks.log"
echo "[$(date +%H:%M:%S)] lindbergh-pads apply: $ROM" >> "$LOG"
( cd "$LAUNCHERS" && python3 -m lib.lindbergh_pads apply "$ROM" ) >> "$LOG" 2>&1 || true
exit 0
