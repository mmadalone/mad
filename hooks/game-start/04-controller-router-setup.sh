#!/usr/bin/env bash
# game-start: run controller-router SETUP for RetroArch systems NOT launched through
# controller-router-wrap.sh (ES-DE-bundled es_systems). $1=ROM $2=name $3=system $4=fullname
#
# The skip answer comes from 02-launch-info.sh's cache (MAD_LI_SETUP_NEEDED, one shared
# router call per launch) instead of a per-launch python probe. It is keyed on the
# PER-GAME resolved command and also skips the binder-launched systems (ps2/ps3/switch/
# xbox via mad-*-launch.py), whose _setup run was proven fully discarded at the
# no-RA-core-dirs guard (~1-1.5 s of device enumeration for nothing per launch,
# AUDIT-2026-08-12). Missing/mismatched cache falls back to the legacy inline probe
# (wrap-only skip), so a stale router version degrades, never breaks.
LOG="$HOME/Emulation/storage/sinden/logs/es-de-hooks.log"; mkdir -p "$(dirname "$LOG")"
RT="$HOME/Emulation/tools/launchers"; SYSTEM="$3"
unset MAD_LI_ROM MAD_LI_SYSTEM MAD_LI_SETUP_NEEDED
LI="${XDG_RUNTIME_DIR:-/tmp}/mad-launch-info.env"
if [ -f "$LI" ] && [ -O "$LI" ]; then . "$LI" 2>/dev/null; fi
if [ "${MAD_LI_ROM:-}" = "${1//\\/}" ] && [ "${MAD_LI_SYSTEM:-}" = "$SYSTEM" ] \
   && [ -n "${MAD_LI_SETUP_NEEDED:-}" ]; then
    [ "${MAD_LI_SETUP_NEEDED:-}" = "0" ] && exit 0
else
    cmd=$(python3 -c "import sys; sys.path.insert(0,'$RT'); from lib import es_systems; print(es_systems.default_command(sys.argv[1]))" "$SYSTEM" 2>/dev/null)
    case "$cmd" in *controller-router-wrap.sh*) exit 0 ;; esac
fi
echo "[$(date +%H:%M:%S)] router-setup hook (unwrapped RA): system='$SYSTEM'" >> "$LOG"
"$RT/controller-router.py" setup "$1" "$2" "$3" "$4" >> "$LOG" 2>&1 \
  || echo "[$(date +%H:%M:%S)]   WARN: setup returned non-zero (launch continues)" >> "$LOG"
exit 0
