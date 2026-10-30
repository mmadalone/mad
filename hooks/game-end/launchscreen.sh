#!/usr/bin/env bash
# ES-DE game-end hook: close the launching splash if it's still up. The splash does
# NOT close on focus changes (see show-launchscreen.py); this hook, the next launch
# that shows a splash, or its own safety timer are what close it.
# PIDF / READY come from the shared resolver (kept in $XDG_RUNTIME_DIR).
source "$(dirname "$0")/../launchscreen-pack.sh"
[[ -f "$PIDF" ]] && kill "$(cat "$PIDF" 2>/dev/null)" 2>/dev/null
pkill -f show-launchscreen.py 2>/dev/null
rm -f "$PIDF" "$READY"
exit 0
