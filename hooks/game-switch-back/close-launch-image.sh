#!/usr/bin/env bash
# ES-DE game-switch-back hook (fork event, MAD.172+): the user came back to ES-DE while a game it
# launched in the background (a Steam game) still runs. ES-DE keeps that game's game-end pending
# until the game really exits, so this only closes the launching splash, which would otherwise
# hold keyboard focus and keep every controller press away from ES-DE.
# Core (always deployed): without the launch-screen theme there is no splash and no
# launchscreen-pack.sh, and this is a no-op. Its presence also tells ES-DE that the scripts know
# this event; without any script here ES-DE runs the full game-end at the switch-back instead.
PACK_SH="$(dirname "$0")/../launchscreen-pack.sh"
[[ -f "$PACK_SH" ]] || exit 0
# launchscreen-pack.sh is found next to the deployed hook at runtime.
# shellcheck disable=SC1090
source "$PACK_SH"
[[ -f "$PIDF" ]] && kill "$(cat "$PIDF" 2>/dev/null)" 2>/dev/null
pkill -f show-launchscreen.py 2>/dev/null
rm -f "$PIDF" "$READY"
exit 0
