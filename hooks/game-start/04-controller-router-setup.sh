#!/usr/bin/env bash
# game-start: run controller-router SETUP for RetroArch systems NOT launched through
# controller-router-wrap.sh (ES-DE-bundled es_systems). $1=ROM $2=name $3=system $4=fullname
LOG="$HOME/Emulation/storage/sinden/logs/es-de-hooks.log"; mkdir -p "$(dirname "$LOG")"
RT="$HOME/Emulation/tools/launchers"; SYSTEM="$3"
cmd=$(python3 -c "import sys; sys.path.insert(0,'$RT'); from lib import es_systems; print(es_systems.default_command(sys.argv[1]))" "$SYSTEM" 2>/dev/null)
case "$cmd" in *controller-router-wrap.sh*) exit 0 ;; esac
echo "[$(date +%H:%M:%S)] router-setup hook (unwrapped RA): system='$SYSTEM'" >> "$LOG"
"$RT/controller-router.py" setup "$1" "$2" "$3" "$4" >> "$LOG" 2>&1 \
  || echo "[$(date +%H:%M:%S)]   WARN: setup returned non-zero (launch continues)" >> "$LOG"
exit 0
