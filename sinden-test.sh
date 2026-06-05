#!/usr/bin/env bash
# Test BOTH Sinden guns: bring up the driver + the MPX 2nd cursor (P2) so each gun
# drives a pointer, then aim the guns and watch the cursor(s) move on screen.
#
# No separate window on purpose: a fullscreen window just HIDES the cursors (and in
# Game Mode the system/MPX cursors don't composite over it). The cursors move over
# whatever's displayed (MAD / the desktop). Stop via the Lightgun page's Stop button.
#
# NOTE: P2's 2nd cursor is an X11 MPX feature — it appears in DESKTOP mode; in Game
# Mode (gamescope) you'll typically see only P1's cursor.
set -uo pipefail
LOG="$HOME/Emulation/storage/control-panel/sinden-test.log"
mkdir -p "$(dirname "$LOG")"
echo "=== test $(date) ===" >> "$LOG"

# sinden-start.sh ALREADY runs the MPX setup (xinput create-master) exactly once —
# do NOT run it again here: a second xinput create-master can corrupt the P1/P2 pointer
# attachment Dolphin/XInput2 depends on (suspected cause of HotD-Overkill losing its aim
# cursor). Just bring the driver up.
"$HOME/Emulation/tools/launchers/sinden-start.sh" >> "$LOG" 2>&1 || true
echo "driver + MPX (via sinden-start) up — aim both guns; cursor(s) should move" >> "$LOG"
exit 0
