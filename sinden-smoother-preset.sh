#!/usr/bin/env bash
# Usage: sinden-smoother-preset.sh <alpha> <deadzone> [snap_threshold]
#   alpha          — EMA factor 0..1 (lower=smoother, higher=snappier)
#   deadzone       — ABS-unit deadband below which raw is emitted instead of
#                    filtered (kills sub-pixel swim at rest)
#   snap_threshold — ABS-unit jump above which the filter snaps to raw
#                    instead of slowly catching up (kills "stuck" feel on
#                    whip-aim). Default 1000.
ALPHA="${1:-0.12}"
DEADZONE="${2:-1.6}"
SNAP="${3:-1000}"
. "$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]:-$0}")" && pwd)/lib/mad-paths.sh" 2>/dev/null || . "$HOME/Emulation/tools/launchers/lib/mad-paths.sh"
CFG="$storageRoot/sinden/smoother.ini"
mkdir -p "$(dirname "$CFG")"
cat > "$CFG" <<INI
[smoothing]
alpha = $ALPHA
deadzone = $DEADZONE
snap_threshold = $SNAP
INI
# Reload the running daemon if any (SIGHUP), else (re)start the full pipeline.
if pgrep -f sinden-smoother.py >/dev/null 2>&1; then
    pkill -HUP -f sinden-smoother.py || true
    notify-send "Sinden Smoother" "alpha=$ALPHA deadzone=$DEADZONE snap=$SNAP (reloaded)" 2>/dev/null || true
else
    "$HOME/Emulation/tools/launchers/sinden-stop.sh" >/dev/null 2>&1
    sleep 1
    "$HOME/Emulation/tools/launchers/sinden-start.sh" >/dev/null 2>&1
    notify-send "Sinden Smoother" "alpha=$ALPHA deadzone=$DEADZONE snap=$SNAP (started)" 2>/dev/null || true
fi
exit 0
