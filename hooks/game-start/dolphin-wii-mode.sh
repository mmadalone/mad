#!/usr/bin/env bash
# ES-DE game-start hook -- decide + apply Dolphin's Wii Remote source for this game.
#
# The WHOLE decision is made in lib.dolphin_wii_source, the SINGLE writer of WiimoteNew.ini:
#   * DolphinBar present            -> real / real2 by connected-remote count (lightgun + non-lightgun)
#   * no bar, lightgun collection   -> Sinden emulated Wii Remotes (the gun profiles)
#   * no bar, GameTDB CC-capable    -> Classic Controller (docked pads->players / handheld the Deck)
#   * no bar, otherwise             -> real (the router shows a "no Wii Remote" warning)
# This runs before Dolphin starts (Dolphin is closed at game-start, which the writer requires). The CC
# branch is transient and reverted by hooks/game-end/dolphin-wii-cc-restore.sh. We only start the
# "+ & -" quit-combo watcher here for real-Wiimote modes.
#
# ES-DE args: $1=ROM path (backslash-escaped)  $2=name  $3=system  $4=fullname
LOG=$HOME/Emulation/storage/sinden/logs/es-de-hooks.log
LAUNCHERS=$HOME/Emulation/tools/launchers
ROM="${1//\\/}"            # strip ES-DE's backslash escapes
SYSTEM="$3"
echo "[$(date +%H:%M:%S)] dolphin-wii-mode hook: system='$SYSTEM' rom='$ROM'" >> "$LOG"

# Only act on Wii (Dolphin) launches; leave every other system alone.
if [[ "$SYSTEM" != "wii" && "$ROM" != */ROMs/wii/* ]]; then
    exit 0
fi

# The decider prints the chosen mode to stdout (real|real2|sinden|classic|skip); its log lines go to
# stderr, captured into LOG. It is the sole WiimoteNew.ini writer, so the router no longer applies real.
mode=$( cd "$LAUNCHERS" && python3 -m lib.dolphin_wii_source apply "$ROM" 2>>"$LOG" )
echo "[$(date +%H:%M:%S)]   -> dolphin_wii_source mode='$mode'" >> "$LOG"

# Real-Wiimote games: start the "+ & -" quit-combo watcher (game-end hook stops it).
if [[ "$mode" == real* ]]; then
    PIDF="$HOME/Emulation/storage/sinden/wiimote-quit-watcher.pid"
    pkill -f wiimote-quit-watcher.py 2>/dev/null
    nohup python3 "$LAUNCHERS/wiimote-quit-watcher.py" >> "$LOG" 2>&1 &
    echo $! > "$PIDF"
    echo "[$(date +%H:%M:%S)]   started wiimote-quit-watcher (pid $!)" >> "$LOG"
fi
exit 0
