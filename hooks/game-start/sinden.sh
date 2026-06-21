#!/usr/bin/env bash
LOG=$HOME/Emulation/storage/sinden/logs/es-de-hooks.log
echo "[$(date +%H:%M:%S)] game-start args: $*" >> "$LOG"
ROUTER=$HOME/Emulation/tools/launchers/controller-router.py
# ES-DE passes the ROM with literal backslash-escapes (e.g. Duck\ Hunt\ \(World\).zip);
# strip them so the router can match against the collection's plain paths.
ROM="${1//\\/}"
# Start the Sinden driver iff this ROM belongs to a lightgun (require_sinden)
# custom collection. Replaces the old hardcoded grep of the Pew-Pew-Pew .cfg;
# now any collection marked require_sinden works (and fails safe = no driver).
if "$ROUTER" lightgun-rom "$ROM" 2>/dev/null; then
    echo "[$(date +%H:%M:%S)]   lightgun collection — starting driver" >> "$LOG"
    $HOME/Emulation/tools/launchers/sinden-start.sh >> "$LOG" 2>&1 || true
else
    echo "[$(date +%H:%M:%S)]   not a lightgun-collection rom — skipping" >> "$LOG"
fi
exit 0
