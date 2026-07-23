#!/usr/bin/env bash
# game-end: back up saves + configs to MEGA (Tier A) the instant a game quits.
# FIRE-AND-FORGET - it detaches and returns immediately, so it never delays ES-DE
# getting back to the carousel. Fires on normal exit AND on a quit-combo kill
# (ES-DE runs game-end hooks either way), which is exactly when the save is final.
# Gated two ways so it is a harmless no-op until you opt in AND connect:
#   - the on-exit toggle flag (~/.config/deck-cloud/onexit.enabled), and
#   - deck-cloud.sh push-precious self-skips when the account isn't set up yet.
# $1=ROM $2=name $3=system $4=fullname
[ -f "$HOME/.config/deck-cloud/onexit.enabled" ] || exit 0
CLOUD="$HOME/Emulation/tools/launchers/deck-cloud.sh"
[ -x "$CLOUD" ] || exit 0
# setsid+nohup = fully detached new session, so ES-DE does not wait on the upload.
setsid nohup "$CLOUD" push-precious >/dev/null 2>&1 &
exit 0
