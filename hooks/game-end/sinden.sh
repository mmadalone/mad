#!/usr/bin/env bash
LOG=$HOME/Emulation/storage/sinden/logs/es-de-hooks.log
echo "[$(date +%H:%M:%S)] game-end args: $*" >> "$LOG"
MARKER=$HOME/Emulation/storage/sinden/.esde-hook-started-driver
# Stop the driver ONLY if game-start started it for this lightgun ROM (marker
# present). A MAD 'Test Both Guns'/'Calibrate' session runs outside the ES-DE
# hook lifecycle and leaves no marker, so exiting an unrelated game no longer
# kills the test out from under the user.
if [ -f "$MARKER" ]; then
    rm -f "$MARKER"
    $HOME/Emulation/tools/launchers/sinden-stop.sh >> "$LOG" 2>&1 || true
else
    echo "[$(date +%H:%M:%S)]   no hook-started-driver marker — leaving driver as-is" >> "$LOG"
fi
exit 0
