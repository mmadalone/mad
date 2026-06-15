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

import subprocess

__all__ = ["process_running", "esde_running", "abort_if_esde_running",
           "emulator_running", "retroarch_running", "EMULATOR_PROCS"]

# The ES-DE process matches BOTH the AppImage process name 'ES-DE' AND the
# legacy 'emulationstation' binary — the alternation is intentional, do NOT
# "simplify" it to just 'ES-DE' (it would stop matching the legacy binary).
ESDE_PATTERN = "ES-DE|emulationstation"


def process_running(pattern: str, *, exact: bool = False) -> bool:
    """True iff pgrep finds a live process matching ``pattern``.

    ``exact=False`` (default) → ``pgrep -f pattern``: matches against the full
    command line, and ``pattern`` may be an extended regex (e.g.
    ``'ES-DE|emulationstation'``). ``exact=True`` → ``pgrep -x pattern``: the
    process NAME must equal ``pattern`` exactly (mirrors
    ``systems_cmds._retroarch_running``'s ``pgrep -x retroarch``).

    Never raises — any failure to even spawn pgrep is treated as "not running"
    (same try/except as ``_retroarch_running``), so a guard can never itself
    crash the caller it is meant to protect.
    """
    flag = "-x" if exact else "-f"
    try:
        return subprocess.run(["pgrep", flag, pattern],
                              capture_output=True).returncode == 0
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
    try:
        assert process_running(_m) is True, "-f should find the live marker"
        assert process_running("sleep_no_such", exact=True) is False
    finally:
        _p.terminate()
        _p.wait()
    print("proc_guard self-test OK")
