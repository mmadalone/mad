#!/usr/bin/env bash
# ES-DE game-end hook: close the launching splash if it's still up. Normally the
# splash self-closes when the game window takes focus (see game-start/launchscreen.sh);
# this is the definitive backstop so it never lingers after the game exits.
# PIDF / READY come from the shared resolver (kept in $XDG_RUNTIME_DIR).
source "$(dirname "$0")/../launchscreen-pack.sh"
[[ -f "$PIDF" ]] && kill "$(cat "$PIDF" 2>/dev/null)" 2>/dev/null
pkill -f show-launchscreen.py 2>/dev/null
rm -f "$PIDF" "$READY"
exit 0
