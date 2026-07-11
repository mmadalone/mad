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
# loader dies. So ALSO kill the game by its EXECUTABLE, matched via the real
# /proc/<pid>/exe target (ends in ".elf"), NOT by full cmdline. A `pkill -f '[.]elf'`
# substring match ALSO hits the loader on the Test/Calibrate (-t) tile, because
# controller-router-wrap.sh `exec`s `lindbergh-loader ... -t .../<name>.elf`, so the
# loader's argv carries ".elf". SIGKILLing that loader (ES-DE's waited-on child)
# leaves ES-DE un-exitable and wedges Steam's Game Mode, intermittently
# (see deck-docs/lindbergh-quit-wedges-esde-steam-2026-06-25.md). /proc/exe of the
# loader is the squashfs `lindbergh` binary (spared); /proc/exe of the game is the
# real `.elf` (killed). The loader still dies via the `-f lindbergh` group below,
# exactly as on the always-clean normal tile. pgrep -f '[.]elf' only enumerates
# candidates (cheap; the literal pattern "[.]elf" does not self-match); the exe
# filter is what actually targets the game. Kill the game FIRST so the
# self-matching `lindbergh` SIGKILL can't tear down this shell before the game dies;
# escalate to SIGKILL after 1s. QUIT is single-quoted: $p / $(...) must evaluate at
# quit-time, not now.
# Combo lookup key. The watcher uses --system ONLY to pick which [quit_combo.<key>]
# applies (the actual quit is --quit-cmd, computed above). Lindbergh quit combos are
# PER GAME (lightgun vs non-lightgun games use different peripherals — a Sinden gun is
# a mouse and can't press the default pad combo), so key on the game: lindbergh-<stem>,
# matching the MAD page's scope ([quit_combo.lindbergh-<titleid>]). Unset per-game ->
# _read_quit_combo falls back to the global default. Strip ES-DE's backslash escapes
# before taking the basename.
COMBO_SYS="$SYSTEM"
if [ "$SYSTEM" = "lindbergh" ]; then
    QUIT='for p in $(pgrep -f "[.]elf"); do case "$(readlink /proc/$p/exe 2>/dev/null)" in *.elf) kill -TERM "$p" 2>/dev/null ;; esac; done; pkill -TERM -f lindbergh; sleep 1; for p in $(pgrep -f "[.]elf"); do case "$(readlink /proc/$p/exe 2>/dev/null)" in *.elf) kill -KILL "$p" 2>/dev/null ;; esac; done; pkill -KILL -f lindbergh'
    COMBO_SYS="lindbergh-$(basename "${ROM//\\/}" .lindbergh)"
fi
# On-the-go (WS-G): when playing HANDHELD (on-the-go enabled + physically handheld), prefer the
# [quit_combo.handheld] Deck-pad chord, so games whose docked combo is keyboard/mouse-only (e.g.
# Lindbergh) can still be quit undocked. Docked launches pass nothing -> the normal combo, untouched.
RT=$HOME/Emulation/tools/launchers
HH=""
python3 -c "import sys; sys.path.insert(0,'$RT'); from lib import deck_state, policy; hh=policy.load_merged().get('handheld') or {}; sys.exit(0 if (isinstance(hh,dict) and hh.get('enabled') and deck_state.is_handheld(deck_state.resolve_force(hh))) else 1)" 2>/dev/null && HH="--handheld"
nohup python3 $HOME/Emulation/tools/launchers/quit-combo-watcher.py \
    --system "$COMBO_SYS" $HH --quit-cmd "$QUIT" >> "$LOG" 2>&1 &
echo $! > "$PIDF"
echo "[$(date +%H:%M:%S)] started quit-combo-watcher (system=$SYSTEM combo=$COMBO_SYS handheld=${HH:-no}, pid $!)" >> "$LOG"
exit 0
