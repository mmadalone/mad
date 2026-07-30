#!/usr/bin/env bash
# game-end: thaw the transfers 20-cloud-pause.sh froze for gameplay. Runs BEFORE
# 20-cloud-push.sh on purpose (ES-DE runs hook dirs sorted case-sensitively, 19 < 20)
# so a resumed job re-holds the push flock before the on-exit push starts - the
# push's flock -n then skips cleanly and self-heals on its next run. Only jobs
# paused BY the gameplay policy are thawed; a user-paused job stays paused.
# Always exit 0: never delay ES-DE's return to the carousel.
# $1=ROM $2=name $3=system $4=fullname
LOG="$HOME/Emulation/storage/controller-router/router.log"; mkdir -p "$(dirname "$LOG")"
RT="$HOME/Emulation/tools/launchers"
STATE="${DECK_CLOUD_STATE_DIR:-$HOME/.config/deck-cloud}"
rm -f "$STATE/gameplay.active"
python3 "$RT/lib/job_registry.py" resume-gameplay >> "$LOG" 2>&1 || true
exit 0
