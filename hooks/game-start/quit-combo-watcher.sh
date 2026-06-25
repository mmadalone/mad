#!/usr/bin/env bash
# ES-DE game-start hook — start the configurable hold-to-quit combo watcher for
# STANDALONE emulators (evdev pads). The combo + hold come from [quit_combo] in
# controller-policy(.local).toml; the config GUI's "Detect" feature edits them.
# Generalises the old cemu-quit-watcher.sh to every standalone emulator.
# (RetroArch systems keep RA's own quit hotkey; real Wii Remotes keep
# wiimote-quit-watcher.py — they're HID, not evdev.)
#
# ES-DE args: $1=ROM  $2=name  $3=system  $4=fullname
LOG=$HOME/Emulation/storage/sinden/logs/es-de-hooks.log
ROM="$1"
SYSTEM="$3"
ROUTER=$HOME/Emulation/tools/launchers/controller-router.py
# Ask the router how to quit this system's emulator (data-driven: derived from
# ES-DE's active emulator + curated [backends.*].quit_cmd in the policy). Empty
# => RetroArch / Wii-HID / unknown system -> no evdev quit watcher.
QUIT="$("$ROUTER" quit-cmd "$SYSTEM" 2>/dev/null)"
# RetroArch LIGHTGUN games are the exception: P1/P2 mouse = the Sinden guns, so RA's
# mouse-button quit hotkey can't fire (RA polls hotkeys on P1's mouse only). For those
# the router hands back RA's own quit so the red-button combo still quits them. Returns
# empty for non-lightgun RA games and for standalone systems (incl. Wii/dolphin).
[ -z "$QUIT" ] && QUIT="$("$ROUTER" lightgun-quit-cmd "$ROM" "$SYSTEM" 2>/dev/null)"
[ -z "$QUIT" ] && exit 0

PIDF="$HOME/Emulation/storage/sinden/quit-combo-watcher.pid"
pkill -f quit-combo-watcher.py 2>/dev/null
# (Set QUIT_COMBO_DEBUG=1 here to log per-pad held combo buttons for diagnostics —
# verified 2026-06-01 that all connected pads register the combo, so left off.)
# Lindbergh's loader ignores SIGTERM and has no save-on-exit (sram is written
# during play), so escalate to SIGKILL fast for a responsive quit (~hold + 2s);
# other systems keep the gentle 6s default.
# Lindbergh: `pkill -f lindbergh` hits the loader/wrapper but NOT the game, which
# runs as `./<name>.elf` (no "lindbergh" in its argv) and is NOT reaped when the
# loader dies. Also kill the game by its `.elf` cmdline — the `[.]elf` char-class
# matches ".elf" while the literal pattern string is "[.]elf", so this kill shell
# (whose cmdline carries the pattern) does not self-match. Kill the game FIRST so
# the self-matching `lindbergh` SIGKILL can't tear down this shell before the game
# dies; escalate to SIGKILL after 1s for a snappy quit.
if [ "$SYSTEM" = "lindbergh" ]; then
    QUIT="pkill -TERM -f '[.]elf'; pkill -TERM -f lindbergh; sleep 1; pkill -KILL -f '[.]elf'; pkill -KILL -f lindbergh"
fi
nohup python3 $HOME/Emulation/tools/launchers/quit-combo-watcher.py \
    --system "$SYSTEM" --quit-cmd "$QUIT" >> "$LOG" 2>&1 &
echo $! > "$PIDF"
echo "[$(date +%H:%M:%S)] started quit-combo-watcher ($SYSTEM, pid $!)" >> "$LOG"
exit 0
