#!/usr/bin/env bash
# game-start: on-the-go TDP watt cap. Sweeps any orphaned cap left by a crashed
# prior launch, then (feature enabled + handheld + participating system) snapshots
# the resting cap and lowers it. Docked / disabled / non-participating = no cap.
# The decision lives in lib/deck_power (policy-driven, single source of truth); this
# runs for EVERY system so the crash-sweep always fires.
# $1=ROM $2=name $3=system $4=fullname
LOG="$HOME/Emulation/storage/controller-router/router.log"; mkdir -p "$(dirname "$LOG")"
RT="$HOME/Emulation/tools/launchers"
echo "[$(date +%H:%M:%S)] mad-power apply: system='$3'" >> "$LOG"
python3 -c "import sys; sys.path.insert(0,'$RT'); from lib import deck_power; sys.exit(deck_power.main(sys.argv[1:]))" apply "$3" >> "$LOG" 2>&1 \
  || echo "[$(date +%H:%M:%S)]   WARN: deck_power apply returned non-zero (launch continues)" >> "$LOG"
exit 0
