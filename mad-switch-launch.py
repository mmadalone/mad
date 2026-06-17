#!/usr/bin/env python3
"""ES-DE launch wrapper for Switch games (Ryujinx / Eden).

Two modes:
  • `mad-switch-launch.py <emu> <rom> -- <emulator cmd...>` — bind the connected
    pads to the emulator's input config (by the user's stored priority), then
    EXEC the emulator (this process BECOMES the emulator, so no separate wrapper
    lingers for the quit-combo's `pkill -f` to hit). Steam Input must stay OFF for
    ES-DE; the bind runs in this launch session so the SDL slot index matches.
  • `mad-switch-launch.py --restore-all` — called by the ES-DE game-end hook after
    the game exits (however it died); reverts the input to the on-the-go default,
    keeping the per-game SETTINGS the emulator wrote.

Wired from es_systems.xml, e.g.:
    mad-switch-launch.py ryujinx %ROM% -- %EMULATOR_RYUJINX% %ROM%
    mad-switch-launch.py eden %ROM% -- /path/Eden.AppImage -f -g %ROM%
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
        print("usage: mad-switch-launch.py <emu> <rom> -- <emulator cmd...>",
              file=sys.stderr)
        return 2
    sep = argv.index("--")
    emu, rom = argv[0], argv[1]
    cmd = argv[sep + 1:]
    if not cmd:
        print("mad-switch-launch: empty emulator command", file=sys.stderr)
        return 2

    from lib import switch_bind
    # Ryujinx ids are "{sdl_index}-{guid}" — the index must match RYUJINX's own SDL
    # enumeration, which differs from the system SDL's (e.g. SDL 2.30 surfaces the
    # Steam Virtual Gamepad as a separate joystick, shifting indices). So probe with
    # the emulator's BUNDLED libSDL2.
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
    switch_bind._log(f"{emu}: exec {cmd}")
    try:
        os.execvp(cmd[0], cmd)
    except OSError as e:                 # bad/missing binary — stderr is lost in Game Mode
        switch_bind._log(f"{emu}: exec FAILED ({e!r})")
        raise
    return 127                          # unreachable unless execvp fails


if __name__ == "__main__":
    sys.exit(main())
