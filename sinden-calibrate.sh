#!/usr/bin/env bash
# Run the Sinden Lightgun driver in calibration mode (foreground UI).
# Calibrate with the GUN: hold dpad-LEFT ~5s to recenter, shoot the centre, then
# move the cursor to the bottom-right corner.
# EXIT: press ANY button on ANY OTHER controller (not the Sinden gun) — a small
# evdev watcher kills the calibration UI. (The Sinden enumerates as USB vendor
# 0x16c0 and is excluded so its own trigger/d-pad don't quit calibration.)
# Debug log: ~/Emulation/storage/control-panel/calibrate.log
set -uo pipefail
HERE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd)"   # absolute, for sinden.conf

LOG="$HOME/Emulation/storage/control-panel/calibrate.log"
mkdir -p "$(dirname "$LOG")"
echo "=== calibrate $(date) ===" >> "$LOG"

# TV LED border strip: calibration runs LightgunMono DIRECTLY (not via sinden-start.sh),
# so the HA webhook that lights the strip never fired here — fire it now (config-driven
# via sinden.conf, same as sinden-start/stop), and turn it off again on exit.
CONF="$HERE_DIR/sinden.conf"
if [ -f "$CONF" ]; then . "$CONF"; else echo "sinden-calibrate: $CONF missing — LED off" >&2; fi
led(){
    if [ "${SINDEN_LED_ENABLED:-0}" = "1" ] && [ -n "${SINDEN_LED_HA_BASE:-}" ] && [ -n "${1:-}" ]; then
        curl -fsS -m 3 -X POST "$SINDEN_LED_HA_BASE/api/webhook/$1" >/dev/null 2>&1 &
        disown
    fi
}
led "${SINDEN_LED_WEBHOOK_START:-}"

pkill -f 'LightgunMono.exe' 2>/dev/null
sleep 0.5
cd "$HOME/Lightgun" || exit 1

# sdl = calibration UI, steam = scale to deck screen, joystick = stay in joystick mode
mono LightgunMono.exe sdl steam joystick >> "$LOG" 2>&1 &
MONO_PID=$!

# Watch every non-Sinden input device; first button press kills the UI.
LOG="$LOG" python3 - "$MONO_PID" <<'PY' &
import os, sys, time, select, subprocess
log = open(os.environ.get("LOG", "/tmp/calibrate.log"), "a")
def L(m): log.write("watcher: %s\n" % m); log.flush()
mono_pid = int(sys.argv[1])
try:
    from evdev import InputDevice, ecodes, list_devices
except Exception as e:
    L("evdev import FAILED: %r — EXIT-ON-BUTTON DISABLED (move cursor to "
      "bottom-right corner to quit calibration)" % e)
    sys.exit(1)
devs = []
for p in list_devices():
    try:
        d = InputDevice(p)
        if d.info.vendor == 0x16c0:                 # Sinden gun — ignore
            continue
        if ecodes.EV_KEY in d.capabilities():
            devs.append(d); L("watching %s (%04x:%04x) %s" %
                              (p, d.info.vendor, d.info.product, d.name))
    except Exception as e:
        pass
if not devs:
    L("no non-Sinden input devices found — exit-on-button disabled")
fds = {d.fd: d for d in devs}
def kill_ui():
    L("button detected -> killing LightgunMono")
    subprocess.run(["pkill", "-TERM", "-f", "LightgunMono.exe"])
    time.sleep(0.6)
    subprocess.run(["pkill", "-KILL", "-f", "LightgunMono.exe"])
# Drain whatever's already buffered at launch — the button press that STARTED calibration,
# window-focus synthetic events, controller noise — so it can't quit us before the UI is up.
for d in devs:
    try:
        while d.read_one() is not None:
            pass
    except Exception:
        pass
# Grace period: don't honour "press any button to quit" until the calibration UI has been up a
# few seconds. Without this the residual launch press killed it instantly ("nothing happens").
GRACE = 3.0
armed_at = time.time()
L("exit-on-button armed after %.0fs grace" % GRACE)
while os.path.exists("/proc/%d" % mono_pid):
    r, _, _ = select.select(list(fds), [], [], 1.0)
    hit = False
    for fd in r:
        try:
            for ev in fds[fd].read():                 # always drain, even during the grace window
                if ev.type == ecodes.EV_KEY and ev.value == 1:
                    hit = True
        except Exception:
            pass
    if hit and (time.time() - armed_at) >= GRACE:
        kill_ui(); break
L("watcher exiting")
PY
WATCH_PID=$!

wait "$MONO_PID" 2>/dev/null
kill "$WATCH_PID" 2>/dev/null || true
led "${SINDEN_LED_WEBHOOK_STOP:-}"      # turn the strip back off when calibration ends
exit 0
