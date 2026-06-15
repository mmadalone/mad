"""
proc_guard.py — shared "is process X running?" guards for the launcher tools.

Canonicalises the pgrep-based checks that were copy-pasted inline across the
repo (steam-collection-sync.py:83, lib/madsrv/systems_cmds.py:53
``_retroarch_running``, dolphin-wii-mode.sh:32). The point of every one of these
is the same: REFUSE a config/gamelist write while the app that OWNS that file is
live, because it rewrites the file on exit and would silently clobber our edit
(ES-DE rewrites gamelists, RetroArch rewrites its cfgs, the standalone emulators
rewrite their configs).

Pure stdlib; safe to import either from a top-level script
(``from lib.proc_guard import esde_running``) or from a sibling lib module
(``from . import proc_guard``).
"""
from __future__ import annotations

import re
import subprocess

__all__ = ["process_running", "esde_running", "abort_if_esde_running",
           "emulator_running", "retroarch_running", "EMULATOR_PROCS"]

# The ES-DE process matches BOTH the AppImage process name 'ES-DE' AND the
# legacy 'emulationstation' binary — the alternation is intentional, do NOT
# "simplify" it to just 'ES-DE' (it would stop matching the legacy binary).
ESDE_PATTERN = "ES-DE|emulationstation"

# Command lines that mention an emulator's name but are NOT the emulator. Our own
# controller-router quit machinery names EVERY Switch/standalone emulator in its
# argv — the quit-combo-watcher is launched with
#   --quit-cmd "pkill -TERM -f 'Eden|Yuzu|Suyu|Ryujinx'; sleep 2; pkill -KILL ..."
# so a plain `pgrep -f Ryujinx` (or `Eden`) matches the WATCHER and its pkill
# command and FALSELY reports the emulator as running. That false "running"
# blocks MAD's config/input writes ("close <emu> first" / EBUSY) even though no
# emulator is live — the same false-positive class as the pcsx2-qt loose-pgrep
# bug. These tokens never appear in a real emulator's own command line, so an
# `-f` match consisting only of these is discarded.
_FALSE_POSITIVE_RE = re.compile(r"quit-combo-watcher|\bpkill\b|\bpgrep\b")


def process_running(pattern: str, *, exact: bool = False) -> bool:
    """True iff pgrep finds a live process matching ``pattern``.

    ``exact=False`` (default) → ``pgrep -f pattern``: matches against the full
    command line, and ``pattern`` may be an extended regex (e.g.
    ``'ES-DE|emulationstation'``). Matches that are only our own quit machinery
    (quit-combo-watcher / pkill / pgrep — see ``_FALSE_POSITIVE_RE``) are
    ignored, so naming an emulator in a kill command can't masquerade as the
    emulator running. ``exact=True`` → ``pgrep -x pattern``: the process NAME
    must equal ``pattern`` exactly (mirrors ``systems_cmds._retroarch_running``'s
    ``pgrep -x retroarch``); argv can't false-positive, so no filtering needed.

    Never raises — any failure to even spawn pgrep is treated as "not running"
    (same try/except as ``_retroarch_running``), so a guard can never itself
    crash the caller it is meant to protect.
    """
    try:
        if exact:
            return subprocess.run(["pgrep", "-x", pattern],
                                  capture_output=True).returncode == 0
        # -f: inspect each matching command line (`-a`) and keep only matches
        # that are NOT our own quit machinery.
        out = subprocess.run(["pgrep", "-af", pattern],
                             capture_output=True, text=True)
        if out.returncode != 0:
            return False
        for line in out.stdout.splitlines():
            cmd = line.split(" ", 1)[1] if " " in line else line
            if not _FALSE_POSITIVE_RE.search(cmd):
                return True
        return False
    except Exception:
        return False


def esde_running() -> bool:
    """True iff ES-DE (AppImage 'ES-DE' or legacy 'emulationstation') is up."""
    return process_running(ESDE_PATTERN)


# Logical emulator name → (pgrep pattern, exact?). The MAD config pages use this
# to refuse a settings write while the emulator is live: every standalone emulator
# (and RetroArch) rewrites its own config on exit and would silently clobber the
# edit. Names not listed fall back to an exact match on the name itself. Extend
# this map as Standalones tiles are added (each entry verified in its own phase).
EMULATOR_PROCS: dict[str, tuple[str, bool]] = {
    "retroarch": ("retroarch", True),
    "dolphin": ("dolphin-emu", True),
    # eden is a sharun AppImage — match its path OR the inner binary (pgrep -f).
    "eden": ("[Ee]den", False),
    "cemu": ("Cemu", True),
    "rpcs3": ("rpcs3", True),
    "pcsx2": ("pcsx2-qt", True),
    "supermodel": ("supermodel", True),
    # Ryujinx (canary) AppImage — match its path/inner name (pgrep -f), like eden.
    "ryujinx": ("[Rr]yujinx", False),
    # ElSemi's Model 2 Emulator runs as the Windows EMULATOR.EXE under umu/Proton
    # (model2-m2emu.sh: `exec ... ./EMULATOR.EXE <game>`). Match the exe name in the
    # command line (pgrep -f): the Wine thread name is truncated to 15 chars so an
    # exact process-name match (pgrep -x) is unreliable, whereas both the umu
    # launcher and the Wine process carry EMULATOR.EXE in their argv.
    "model2": ("EMULATOR\\.EXE", False),
}


def emulator_running(name: str) -> bool:
    """True iff the emulator known as ``name`` is live (see ``EMULATOR_PROCS``).
    Unknown names fall back to an exact process-name match on ``name``."""
    pattern, exact = EMULATOR_PROCS.get(name, (name, True))
    return process_running(pattern, exact=exact)


def retroarch_running() -> bool:
    """True iff RetroArch is live (it rewrites its cfgs on exit)."""
    return emulator_running("retroarch")


def abort_if_esde_running(action: str = "write the gamelist") -> bool:
    """One-line guard for the top of a gamelist/config writer's ``main()``.

    Usage::

        if abort_if_esde_running():
            return 1

    If ES-DE is running, print the standard "close it first" message (with
    ``action`` woven in so the reason is specific to this caller) and return
    True so the caller bails. Returns False when ES-DE is closed (the normal
    case), so the caller proceeds.
    """
    if esde_running():
        print(f"ES-DE is running — close it first to {action} "
              "(ES-DE rewrites gamelists on exit). Aborting.")
        return True
    return False


if __name__ == "__main__":  # smoke test: `python3 lib/proc_guard.py`
    import subprocess as _sp
    import sys as _sys
    import time as _t

    assert isinstance(esde_running(), bool), "esde_running must return a bool"
    assert ESDE_PATTERN == "ES-DE|emulationstation", "esde regex changed"
    assert process_running("no_such_proc_zzz_0000") is False
    _m = "proc_guard_selftest_marker_42"
    _p = _sp.Popen([_sys.executable, "-c", "import time;time.sleep(5)", _m])
    _t.sleep(0.4)
    # A fake quit-combo-watcher whose argv NAMES the emulator must NOT count as
    # the emulator running (the Eden/Ryujinx false-positive regression test).
    _w = _sp.Popen([_sys.executable, "-c", "import time;time.sleep(5)",
                    "quit-combo-watcher.py", "--quit-cmd",
                    "pkill -TERM -f 'Eden|Yuzu|Suyu|Ryujinx_marker_zzz'"])
    _t.sleep(0.4)
    try:
        assert process_running(_m) is True, "-f should find the live marker"
        assert process_running("sleep_no_such", exact=True) is False
        assert process_running("Ryujinx_marker_zzz") is False, \
            "a quit-combo-watcher naming the emulator must not read as running"
    finally:
        _p.terminate(); _p.wait()
        _w.terminate(); _w.wait()
    print("proc_guard self-test OK")
