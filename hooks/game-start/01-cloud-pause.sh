#!/usr/bin/env bash
# game-start: the BACKUP DURING GAMEPLAY policy (ES-DE > Other settings).
# Marks a game as live (gameplay.active), then applies the toggle to every running
# registered transfer (lib/job_registry.py - the panel/hook/CLI job registry):
#   toggle OFF (no gameplay.enabled file, the default) -> FREEZE them (SIGSTOP per
#     process group; user-paused jobs untouched). 19-cloud-resume.sh thaws on exit.
#   toggle ON -> keep them running but imperceptible (ionice idle + nice 19; renice
#     is one-way for non-root - accepted, transfers are background work anyway).
#     A restore/fetch is frozen EITHER WAY (lib/job_registry.protected_during_gameplay): the
#     toggle's name only ever promised to keep BACKUPS running, and a restore overwrites live
#     saves the running game may hold open.
# WHY 01 AND NOT 20 (audit 2026-08-12 phase 5): ES-DE runs a hook dir in byte-sorted order,
# sequentially. At 20 this ran AFTER the six hooks that write emulator configs (04, 05, 07, 08,
# 09, 10), so a cloud restore in flight kept writing those same files for a 1-2 second window on
# every single launch. Freezing first closes it. Nothing here consumes another hook's output
# (02-launch-info's cache is not read by this hook), so moving to the front is safe.
# The game-END counterpart keeps its number: 19-cloud-resume.sh must sort BEFORE 20-cloud-push.sh
# so a resumed job re-holds the push flock first. That constraint is internal to game-end/ and is
# unaffected by this rename.
# Always exit 0: a policy hiccup must never block a game launch.
# $1=ROM $2=name $3=system $4=fullname
LOG="$HOME/Emulation/storage/controller-router/router.log"; mkdir -p "$(dirname "$LOG")"
RT="$HOME/Emulation/tools/launchers"
STATE="${DECK_CLOUD_STATE_DIR:-$HOME/.config/deck-cloud}"
mkdir -p "$STATE"
: > "$STATE/gameplay.active"
if [ -f "$STATE/gameplay.enabled" ]; then
    echo "[$(date +%H:%M:%S)] cloud-pause: gameplay toggle ON -> deprioritize transfers" >> "$LOG"
    python3 "$RT/lib/job_registry.py" deprioritize-running >> "$LOG" 2>&1 || true
else
    echo "[$(date +%H:%M:%S)] cloud-pause: gameplay toggle OFF -> freeze transfers" >> "$LOG"
    python3 "$RT/lib/job_registry.py" pause-all-gameplay >> "$LOG" 2>&1 || true
fi
exit 0
