#!/usr/bin/env python3
"""ES-DE launch wrapper for MAD-managed standalone emulators (PCSX2, Switch, …).

Generic sibling of mad-switch-launch.py — the Standalones migration points each
migrated emulator's es_systems <command> here. Two modes:
  • `mad-standalone-launch.py <emu> <rom> -- <emulator cmd...>` — bind the connected
    pads to the emulator's input config (by the user's stored priority), then EXEC
    the emulator (this process BECOMES it, so no separate wrapper lingers for the
    quit-combo's `pkill -f` to hit). Steam Input must stay OFF for ES-DE; the bind
    runs in this launch session so the SDL slot index matches.
  • `mad-standalone-launch.py --restore-all` — called by the ES-DE game-end hook
    after the game exits (however it died); reverts the input to the resting config,
    keeping the SETTINGS the emulator wrote.

Wired from es_systems.xml, e.g.:
    mad-standalone-launch.py pcsx2 %ROM% -- %EMULATOR_PCSX2% -batch %ROM%

A `_hands_off` emulator or one with no connected pads is launched unchanged.
"""
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def main() -> int:
    argv = sys.argv[1:]

    if argv[:1] == ["--restore-all"]:
        from lib import switch_bind
        switch_bind.restore_all()
        return 0

    if "--" not in argv or len(argv) < 4:
        print("usage: mad-standalone-launch.py <emu> <rom> -- <emulator cmd...>",
              file=sys.stderr)
        return 2
    sep = argv.index("--")
    emu, rom = argv[0], argv[1]
    cmd = argv[sep + 1:]
    if not cmd:
        print("mad-standalone-launch: empty emulator command", file=sys.stderr)
        return 2

    from lib import switch_bind
    # Ryujinx ids are "{sdl_index}-{guid}" — the index must match RYUJINX's own SDL
    # enumeration (its bundled libSDL2 can surface a different joystick order), so
    # probe with the emulator's bundled lib. No-op for every other emulator.
    if emu == "ryujinx":
        from lib import devices
        for cand in (os.path.join(os.path.dirname(os.path.realpath(cmd[0])), "libSDL2.so"),
                     os.path.expanduser("~/Applications/publish/libSDL2.so")):
            if os.path.isfile(cand):
                devices.set_sdl_lib(cand)
                switch_bind._log(f"using bundled SDL for index match: {cand}")
                break
    switch_bind.bind(emu, rom)          # writes input + the .mad-restore sidecar
    # Become the emulator: ES-DE waits on it, the quit-combo kills IT, and the
    # game-end hook (--restore-all) reverts the input afterwards.
    os.execvp(cmd[0], cmd)
    return 127                          # unreachable unless execvp fails


if __name__ == "__main__":
    sys.exit(main())
