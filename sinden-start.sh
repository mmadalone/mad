#!/usr/bin/env bash
# Start the Sinden Lightgun pipeline:
#   1. evdev smoother (Linux equivalent of HOTDOV.ahk / AutoHotInterception):
#      grabs raw /dev/input/sinden-gun-p[12]-event devices exclusively, applies
#      EMA + deadzone (settings in ~/Emulation/storage/sinden/smoother.ini),
#      and re-emits via two uinput virtual mouse devices
#      ("SindenLightgun Mouse (Smoothed P1/P2)").
#   2. LightgunMono.exe via mono-service — reads the smoothed virtual mice (or
#      raw if the smoother is disabled) and drives the X11 system cursor +
#      synthesizes click events. RetroArch / MAME / Dolphin lightgun cores
#      consume this. Supermodel via ManyMouse reads the smoothed virtual
#      mice directly.
#
# Smoothing toggle: touch ~/Emulation/storage/sinden/.smoothing-off to skip the
# smoother step (LightgunMono reads raw, like the old setup). Toggle Cursor
# Smoother.sh in the Sinden Tools system manages this marker.
#
# Idempotent: if smoother and LightgunMono are already up, just no-op.
set -uo pipefail

# Absolute script dir, resolved BEFORE any `cd` — so sinden.conf is found regardless of
# how the script is invoked (relative `./sinden-start.sh`, absolute, or sourced).
HERE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd)"

log_dir="$HOME/Emulation/storage/sinden/logs"
mkdir -p "$log_dir"
log="$log_dir/sinden-$(date +%Y%m%d-%H%M%S).log"
echo "==== $(date) sinden-start ====" > "$log"

SMOOTHER="$HOME/Emulation/tools/launchers/sinden-smoother.py"
SMOOTH_OFF_MARKER="$HOME/Emulation/storage/sinden/.smoothing-off"

# --- 1. evdev smoother ---
if [[ -e $SMOOTH_OFF_MARKER ]]; then
    echo "sinden-start: smoothing disabled (marker present), skipping evdev smoother" >&2
    echo "smoothing: disabled (marker)" >> "$log"
elif pgrep -f 'sinden-smoother.py' >/dev/null 2>&1; then
    echo "sinden-start: smoother already up (pid $(pgrep -f sinden-smoother.py | head -1))" >&2
else
    echo "smoothing: starting evdev smoother" >> "$log"
    # Start in background, capture READY signal from stdout
    nohup "$SMOOTHER" >> "$log" 2>&1 &
    SMOOTHER_PID=$!
    disown
    # Wait up to 3s for virtual devices to appear in /dev/input
    for _ in $(seq 1 30); do
        if grep -q 'Smoothed' /sys/class/input/event*/device/name 2>/dev/null; then
            break
        fi
        sleep 0.1
    done
    if ! pgrep -f 'sinden-smoother.py' >/dev/null 2>&1; then
        echo "sinden-start: smoother FAILED to start — see $log" >&2
        tail -5 "$log" >&2
        # Continue with LightgunMono anyway (degraded mode: no smoothing)
    else
        echo "sinden-start: smoother up (pid $SMOOTHER_PID)" >&2
    fi
fi

# --- 2. LightgunMono (no LD_PRELOAD shim — smoothing already done by evdev) ---
if pgrep -f 'LightgunMono.exe' >/dev/null 2>&1; then
    echo "sinden-start: LightgunMono already up (pid $(pgrep -f LightgunMono.exe | head -1))" >&2
else
    # Pin SerialPortWrite / SerialPortWriteP2 indices to USB PID before launching
    # LightgunMono, so the gun with PID 0f38 always becomes Player 1 regardless
    # of which ttyACM number it enumerated as. Without this, P1/P2 side-button
    # assignment can flip across reboots while the PID-pinned smoother + Dolphin
    # bindings stay fixed — leaving aim/trigger correct but buttons swapped.
    PREFLIGHT="$HOME/Emulation/tools/launchers/sinden-serial-preflight.py"
    [[ -x $PREFLIGHT ]] && "$PREFLIGHT" >> "$log" 2>&1

    cd "$HOME/Lightgun" || { echo "sinden-start: cannot cd to $HOME/Lightgun — aborting" >&2; exit 1; }
    echo "lightgunmono: starting" >> "$log"
    nohup mono-service LightgunMono.exe >> "$log" 2>&1 &
    disown
    sleep 2
    if ! pgrep -f 'LightgunMono.exe' >/dev/null 2>&1; then
        echo "sinden-start: LightgunMono FAILED — see $log" >&2
        tail -5 "$log" >&2
        exit 1
    fi
    echo "sinden-start: LightgunMono up (pid $(pgrep -f LightgunMono.exe | head -1))" >&2
fi

# --- 3. Update RetroArch mouse_index to match current smoothed device indices ---
# /dev/input/eventN numbers shift when devices are added/removed/reordered;
# RetroArch uses numeric mouse_index so a hardcoded value breaks any time
# the topology changes. This script re-detects them after the smoother is up.
MIDX="$HOME/Emulation/tools/launchers/sinden-update-retroarch-mouseindex.py"
[[ -x $MIDX ]] && "$MIDX" >> "$log" 2>&1

# --- 4. MPX setup so P2 gets its own X11 cursor (Dolphin coop) ---
MPX="$HOME/Emulation/tools/launchers/sinden-mpx-setup.sh"
[[ -x $MPX ]] && "$MPX" >> "$log" 2>&1

# --- 4. HA webhook for TV LED border strip (configurable via sinden.conf, MAD-editable) ---
CONF="$HERE_DIR/sinden.conf"
if [ -f "$CONF" ]; then . "$CONF"; else echo "sinden-start: $CONF missing — LED strip control off" >&2; fi
if [ "${SINDEN_LED_ENABLED:-0}" = "1" ] && [ -n "${SINDEN_LED_HA_BASE:-}" ] && [ -n "${SINDEN_LED_WEBHOOK_START:-}" ]; then
    curl -fsS -m 3 -X POST "$SINDEN_LED_HA_BASE/api/webhook/$SINDEN_LED_WEBHOOK_START" \
        >/dev/null 2>&1 &
    disown
fi

exit 0
