#!/usr/bin/env bash
# game-end: revert the launch-time controller binding for a TRANSIENT standalone so
# the Steam-UI-compatible resting config returns. Every writer-backed standalone the
# user also launches via Steam UI on the go is transient (Switch, PS2, …); add its
# system here as each is migrated. restore_all() is sidecar-gated (no-op otherwise).
# $1=ROM $2=name $3=system $4=fullname.
case "$3" in switch|ps2|xbox|ps3) ;; *) exit 0 ;; esac
LOG="$HOME/Emulation/storage/controller-router/router.log"; mkdir -p "$(dirname "$LOG")"
exec "$HOME/Emulation/tools/launchers/mad-standalone-launch.py" --restore-all >>"$LOG" 2>&1
