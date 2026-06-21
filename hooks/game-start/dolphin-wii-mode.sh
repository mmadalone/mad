#!/usr/bin/env bash
# ES-DE game-start hook — choose Dolphin's Wii Remote source per game:
#   * Wii game IN the Pew-Pew-Pew (lightgun) collection -> "sinden" (Emulated
#     Wii Remotes = the 2 Sinden lightgun profiles)
#   * any OTHER Wii game                                -> "real"  (Real Wii
#     Remotes via the Mayflash DolphinBar)
#
# These are Dolphin (standalone flatpak) games — NOT RetroArch — so the switch
# is done here at the ES-DE hook level (not via the controller-router, which
# skips Wii). It only edits the `Source =` line in Dolphin's WiimoteNew.ini via
# dolphin-wii-mode.sh; mappings are never touched. Runs before Dolphin starts
# (Dolphin is closed at game-start, which is what dolphin-wii-mode.sh requires).
#
# ES-DE args: $1=ROM path (backslash-escaped)  $2=name  $3=system  $4=fullname
LOG=$HOME/Emulation/storage/sinden/logs/es-de-hooks.log
ROM="${1//\\/}"            # strip ES-DE's backslash escapes
SYSTEM="$3"
echo "[$(date +%H:%M:%S)] dolphin-wii-mode hook: system='$SYSTEM' rom='$ROM'" >> "$LOG"

# Only act on Wii (Dolphin) launches; leave every other system alone.
if [[ "$SYSTEM" != "wii" && "$ROM" != */ROMs/wii/* ]]; then
    exit 0
fi

ROUTER=$HOME/Emulation/tools/launchers/controller-router.py
# Lightgun (require_sinden) collection member -> emulated Wiimotes (Sinden guns);
# any other Wii game -> real Wiimotes (DolphinBar). Replaces the old hardcoded
# Pew-Pew-Pew grep; fails safe to `real`.
if "$ROUTER" lightgun-rom "$ROM" 2>/dev/null; then
    mode=sinden
else
    mode=real
fi
echo "[$(date +%H:%M:%S)]   -> dolphin-wii-mode $mode" >> "$LOG"

if [[ "$mode" == sinden ]]; then
    # Lightgun Wii game: this hook owns the Sinden source switch.
    $HOME/Emulation/tools/launchers/dolphin-wii-mode.sh "$mode" >> "$LOG" 2>&1 \
        || echo "[$(date +%H:%M:%S)]   WARN: mode switch failed (launch continues)" >> "$LOG"
else
    # Non-lightgun Wii game: the controller-router (game-start hook
    # 05-controller-router-standalone.sh) now picks real vs real2 from the
    # connected Wiimote count and warns if no DolphinBar. We only keep the
    # quit-watcher below so the two scripts never both write WiimoteNew.ini.
    echo "[$(date +%H:%M:%S)]   real-mode source delegated to controller-router" >> "$LOG"
fi

# Real-Wiimote games: start the "+ & -" quit-combo watcher (game-end hook stops it).
if [[ "$mode" == real* ]]; then
    PIDF="$HOME/Emulation/storage/sinden/wiimote-quit-watcher.pid"
    pkill -f wiimote-quit-watcher.py 2>/dev/null
    nohup python3 $HOME/Emulation/tools/launchers/wiimote-quit-watcher.py >> "$LOG" 2>&1 &
    echo $! > "$PIDF"
    echo "[$(date +%H:%M:%S)]   started wiimote-quit-watcher (pid $!)" >> "$LOG"
fi
exit 0
