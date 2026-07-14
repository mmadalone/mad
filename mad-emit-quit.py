#!/usr/bin/env python3
"""Gracefully quit an emulator by emitting ITS OWN keyboard quit shortcut through a virtual
(uinput) keyboard -- the same mechanism the controller-router uses to feed emulators virtual
pads. For emulators that ignore SIGTERM and whose game process `pkill -f` can't reliably match
(RPCS3 in Game Mode), the pad quit-combo can then trigger the emulator's built-in clean stop.

Reads the shortcut from the emulator's OWN config, so it tracks whatever the user set (e.g.
RPCS3 `[Shortcuts] game_window_stop` in ~/.config/rpcs3/GuiConfigs/CurrentSettings.ini).

Usage:  mad-emit-quit.py rpcs3
Exit 0 on emit, non-0 otherwise (so a caller can fall back to a pkill backstop).
Logs to the shared ES-DE hooks log (also dumps the emulator process list, so a failed quit is
diagnosable). Best-effort: never raises out of main().
"""
from __future__ import annotations

import configparser
import subprocess
import sys
import time
from pathlib import Path

_LOG = Path.home() / "Emulation/storage/sinden/logs/es-de-hooks.log"


def _log(msg: str) -> None:
    try:
        _LOG.parent.mkdir(parents=True, exist_ok=True)
        with _LOG.open("a", encoding="utf-8") as f:
            f.write(f"[mad-emit-quit] {msg}\n")
    except OSError:
        pass


# Qt QKeySequence token -> evdev KEY_ name (modifiers first). RPCS3 writes Qt sequences like
# "Alt+Esc", "Ctrl+Alt+1", "Alt+Return".
_QT_TO_KEY = {
    "CTRL": "KEY_LEFTCTRL", "ALT": "KEY_LEFTALT", "SHIFT": "KEY_LEFTSHIFT", "META": "KEY_LEFTMETA",
    "ESC": "KEY_ESC", "RETURN": "KEY_ENTER", "ENTER": "KEY_ENTER", "SPACE": "KEY_SPACE",
    "TAB": "KEY_TAB", "BACKSPACE": "KEY_BACKSPACE", "DEL": "KEY_DELETE", "DELETE": "KEY_DELETE",
    "HOME": "KEY_HOME", "END": "KEY_END", "PGUP": "KEY_PAGEUP", "PGDOWN": "KEY_PAGEDOWN",
}
_MODS = {"CTRL", "ALT", "SHIFT", "META"}


def _tok_to_code(tok: str, e) -> int | None:
    up = tok.strip().upper()
    name = _QT_TO_KEY.get(up)
    if name is None:
        if len(up) == 1 and (up.isalpha() or up.isdigit()):
            name = f"KEY_{up}"
        elif up and up[0] == "F" and up[1:].isdigit():
            name = f"KEY_{up}"
    return getattr(e, name, None) if name else None


def _rpcs3_quit_sequence() -> str:
    ini = Path.home() / ".config/rpcs3/GuiConfigs/CurrentSettings.ini"
    cp = configparser.ConfigParser()
    cp.optionxform = str                         # keep key case
    try:
        cp.read(ini, encoding="utf-8")
        return cp.get("Shortcuts", "game_window_stop", fallback="Alt+Esc").strip() or "Alt+Esc"
    except Exception:
        return "Alt+Esc"


# emu -> (config quit-shortcut reader). Extendable to other keyboard-quit emulators.
_QUIT_SEQ = {"rpcs3": _rpcs3_quit_sequence}


def _diag(emu: str) -> None:
    """Dump the emulator's live processes so a quit that DIDN'T work is diagnosable (which
    process is the game, does its cmdline carry a pkill-matchable token, etc.)."""
    try:
        out = subprocess.run(["ps", "-eo", "pid,comm,args"], capture_output=True,
                             text=True, timeout=5).stdout
        marks = ("rpcs3", "RPCS3", "EBOOT", ".mount_")
        hits = [ln for ln in out.splitlines() if any(m in ln for m in marks)]
        _log(f"DIAG {emu}: {len(hits)} matching process(es)\n  " + "\n  ".join(hits[:20]))
    except Exception as ex:
        _log(f"DIAG failed ({ex!r})")


def _emit(seq: str) -> bool:
    try:
        from evdev import UInput, ecodes as e
    except Exception as ex:
        _log(f"evdev/UInput unavailable ({ex!r}); cannot emit")
        return False
    toks = [t for t in seq.split("+") if t.strip()]
    pairs = [(t, _tok_to_code(t, e)) for t in toks]
    if not pairs or any(c is None for _, c in pairs):
        _log(f"unparseable quit shortcut {seq!r} (codes={[c for _, c in pairs]}); not emitting")
        return False
    mods = [c for t, c in pairs if t.strip().upper() in _MODS]
    keys = [c for t, c in pairs if t.strip().upper() not in _MODS]
    codes = [c for _, c in pairs]
    try:
        ui = UInput({e.EV_KEY: codes}, name="MAD Quit Keyboard")
    except Exception as ex:
        _log(f"UInput open failed ({ex!r}); cannot emit (check /dev/uinput perms)")
        return False
    try:
        time.sleep(0.5)                          # let the compositor register the new keyboard
        for c in mods:
            ui.write(e.EV_KEY, c, 1)
        for c in keys:
            ui.write(e.EV_KEY, c, 1)
        ui.syn()
        time.sleep(0.12)                         # hold long enough for the shortcut to register
        for c in reversed(keys):
            ui.write(e.EV_KEY, c, 0)
        for c in reversed(mods):
            ui.write(e.EV_KEY, c, 0)
        ui.syn()
        time.sleep(0.05)
    finally:
        try:
            ui.close()
        except Exception:
            pass
    _log(f"emitted {seq!r} (mods={mods} keys={keys})")
    return True


def main() -> int:
    emu = (sys.argv[1] if len(sys.argv) > 1 else "rpcs3").lower()
    _diag(emu)
    reader = _QUIT_SEQ.get(emu)
    if reader is None:
        _log(f"no keyboard-quit shortcut known for {emu!r}")
        return 2
    seq = reader()
    _log(f"{emu} keyboard quit shortcut = {seq!r}")
    return 0 if _emit(seq) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as ex:                      # never crash the quit path
        _log(f"fatal ({ex!r})")
        sys.exit(1)
