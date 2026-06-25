#!/usr/bin/env bash
# Start ONLY the Sinden driver (LightgunMono): NO evdev smoother, NO MPX, NO Home
# Assistant LED hooks. A stripped-down sinden-start.sh for raw-device testing.
#
# Why: pcsx2x6's PCSX2_EVDEV_LIGHTGUN=auto reads a raw "SindenLightgun" /dev/input
# device directly (it needs ABS_X/ABS_Y). With the smoother running it would instead
# see the "Smoothed P1/P2" virtual mice, so this script keeps the smoother OFF so the
# emulator's discover() exercises the raw-gun fallback path (slot 0).
#
# Stop everything (this driver and, if present, the smoother) with sinden-stop.sh.
# Idempotent: if LightgunMono is already up, it just no-ops.
set -uo pipefail

HERE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd)"
. "$HERE_DIR/lib/mad-paths.sh"

log_dir="$storageRoot/sinden/logs"
mkdir -p "$log_dir"
log="$log_dir/sinden-simple-$(date +%Y%m%d-%H%M%S).log"
echo "==== $(date) sinden-simple-start (driver only: no smoother / MPX / HA) ====" > "$log"

# Guard: a running smoother would create "Smoothed P1/P2" devices that get picked up
# INSTEAD of the raw gun. Warn but do not auto-kill the user's setup.
if pgrep -f 'sinden-smoother.py' >/dev/null 2>&1; then
    echo "sinden-simple-start: WARNING the evdev smoother is running (pid $(pgrep -f sinden-smoother.py | head -1))." >&2
    echo "  Its 'Smoothed P1/P2' devices will be used instead of the raw gun." >&2
    echo "  Run sinden-stop.sh first if you want to exercise the raw-gun fallback." >&2
fi

# LightgunMono (the driver). Pin P1/P2 serial by USB PID first, same as sinden-start.sh.
if pgrep -f 'LightgunMono.exe' >/dev/null 2>&1; then
    echo "sinden-simple-start: LightgunMono already up (pid $(pgrep -f LightgunMono.exe | head -1)); nothing to do" >&2
    exit 0
fi

PREFLIGHT="$HOME/Emulation/tools/launchers/sinden-serial-preflight.py"
[[ -x $PREFLIGHT ]] && "$PREFLIGHT" >> "$log" 2>&1

cd "$HOME/Lightgun" || { echo "sinden-simple-start: cannot cd to $HOME/Lightgun, aborting" >&2; exit 1; }
echo "lightgunmono: starting (raw, no smoother)" >> "$log"
nohup mono-service LightgunMono.exe >> "$log" 2>&1 &
disown
# mono-service starts asynchronously and a cold launch can take several seconds; poll ~10s.
for _ in $(seq 1 40); do
    pgrep -f 'LightgunMono.exe' >/dev/null 2>&1 && break
    sleep 0.25
done
if ! pgrep -f 'LightgunMono.exe' >/dev/null 2>&1; then
    echo "sinden-simple-start: LightgunMono FAILED; see $log" >&2
    tail -5 "$log" >&2
    exit 1
fi
echo "sinden-simple-start: LightgunMono up (pid $(pgrep -f LightgunMono.exe | head -1)); smoother OFF" >&2
exit 0
