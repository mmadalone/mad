#!/usr/bin/env bash
# Controller Setup launcher: open an emulator DIRECTLY so the user can remap controllers from
# GAME MODE.
#
# Why direct: ES-DE runs with Steam Input OFF, so the emulator sees the RAW pad. A DualSense's
# d-pad is a HAT (ABS_HAT0) and its triggers are analog axes at the evdev positions -- NOT the
# SDL-GameController button/axis numbers you get when you configure in Desktop mode. So a config
# built in Desktop mode does not match what ES-DE actually launches games with, and the pad reads
# wrong / dead in games. Configuring here (in Game mode) captures the correct raw layout.
#
# Scope: this matters for emulators MAD does NOT bind at launch and that use their OWN config --
# the Switch trio (Eden/Citron/Ryujinx) and Dolphin/Cemu. For PCSX2/pcsx2x6/RPCS3 MAD already binds
# the raw layout at launch (and would overwrite a manual config), so they are intentionally absent.
#
# RetroArch is here for a NARROWER reason: to SEE what RetroArch itself reads from a pad, in Game
# mode, with no content. Nothing else on this rig can answer that -- every tool we have (including
# ra-input-monitor.py) only REPLICATES RetroArch's udev enumeration in Python and can be wrong.
# Open Settings > Input > Port N Controls and press a control: RetroArch names what IT detected.
# CAUTION, read before rebinding here rather than just looking:
#   * DOCKED binds live in the global retroarch.cfg (input_player1_*) and MANUAL BINDS WIN over any
#     autoconfig profile (see lib/ra_handheld_input.py's header, sourced from the RetroArch docs).
#   * HANDHELD, controller-router flips the joypad driver to sdl2 and lib/ra_handheld_input.py
#     TRANSIENTLY rewrites those same input_player1_* keys, restoring the docked values at game-end.
#     So a rebind made here is a DOCKED/udev bind; it is not what handheld uses.
#   * config_save_on_exit is "false" today, so RetroArch will NOT persist a rebind on exit unless
#     you save the config explicitly. That makes LOOKING here completely safe.
#
# Why no mad-switch-launch.py wrapper: the wrapper applies a transient controller bind to the
# emulator's config at launch and reverts it on exit, so a remap done under it would vanish.
#
# Arg 1 = the emulator name (from ES-DE's %BASENAME%: Eden / Citron / Ryujinx / Dolphin / Cemu /
# RetroArch).
set -u
raw="${1:-}"
emu="$(printf '%s' "$raw" | tr '[:upper:]' '[:lower:]')"

case "$emu" in
    *eden*)      cmd=("$HOME/Applications/Eden.AppImage") ;;
    *citron*)    cmd=("$HOME/Applications/Citron.AppImage") ;;
    *ryujinx*)   cmd=("$HOME/Applications/Ryujinx.AppImage") ;;
    *cemu*)      cmd=("$HOME/Applications/Cemu.AppImage") ;;
    *dolphin*)   cmd=(flatpak run org.DolphinEmu.dolphin-emu) ;;
    # Bare RetroArch: no core, no content -- it boots straight to its own menu.
    *retroarch*) cmd=(flatpak run org.libretro.RetroArch) ;;
    *) echo "controller-setup: unknown emulator '$raw'" >&2; exit 2 ;;
esac

# For an AppImage target, verify it exists (glob-fallback if the canonical name moved).
first="${cmd[0]}"
if [[ "$first" == *.AppImage ]]; then
    if [ ! -x "$first" ]; then
        for c in "$HOME/Applications/"*"$emu"*.AppImage; do
            [ -x "$c" ] && { cmd[0]="$c"; first="$c"; break; }
        done
    fi
    [ -x "$first" ] || {
        echo "controller-setup: could not find the $raw AppImage in ~/Applications" >&2; exit 3; }
fi

# Launch WINDOWED (no -f) so the menu bar is reachable: in the Switch emus / Cemu use
# Emulation|Options > Configure/Settings > Controls/Input; in Dolphin use Controllers.
echo "controller-setup: opening ${cmd[*]} (direct, no ROM/wrapper) to configure controllers" >&2
exec "${cmd[@]}"
