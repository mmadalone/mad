"""
Tests for the EmuDeck `all.sh` decoupling: lib/emudeck-shim.sh + the launcher
source guard. Runs bash subprocesses (the code under test is shell).

Covers:
  * the shim provides the path vars + no-op functions launchers consume,
  * scriptConfigFileGetVar parity (present var -> value, absent -> default),
  * MAD_DATA_ROOT override flows into the shim's path vars,
  * the guard sources EmuDeck's real all.sh when present, the shim when absent.

Run:  python3 -m unittest tests.test_emudeck_shim -v
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

LAUNCHERS = Path(__file__).resolve().parent.parent
LIB = LAUNCHERS / "lib"

# The guard exactly as written at the top of every launcher (lines 2-5), but with
# _MAD_LIB pinned to the real lib dir so the test doesn't depend on $0/BASH_SOURCE.
GUARD = f'''
_MAD_LIB="{LIB}"
EMUDECK_ALL="${{EMUDECK_FUNCTIONS:-$HOME/.config/EmuDeck/backend/functions/all.sh}}"
if [ -f "$EMUDECK_ALL" ]; then . "$EMUDECK_ALL"; else . "$_MAD_LIB/emudeck-shim.sh"; fi
. "$_MAD_LIB/mad-paths.sh"
'''


def _bash(script: str, env: dict | None = None) -> subprocess.CompletedProcess:
    e = dict(os.environ)
    # clear inherited overrides so cases are deterministic
    for k in ("MAD_DATA_ROOT", "storagePath", "EMUDECK_FUNCTIONS"):
        e.pop(k, None)
    if env:
        e.update(env)
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=e)


class Shim(unittest.TestCase):
    def test_shim_sets_path_vars_default(self):
        r = _bash(f'. "{LIB}/emudeck-shim.sh"; '
                  'echo "$emusFolder|$romsPath|$savesPath|$toolsPath|$storagePath|$biosPath"')
        self.assertEqual(r.returncode, 0, r.stderr)
        home = os.path.expanduser("~")
        self.assertEqual(r.stdout.strip(),
                         f"{home}/Applications|{home}/Emulation/roms|{home}/Emulation/saves|"
                         f"{home}/Emulation/tools|{home}/Emulation/storage|{home}/Emulation/bios")

    def test_shim_functions_are_noop(self):
        r = _bash(f'. "{LIB}/emudeck-shim.sh"; '
                  'emulatorInit x && cloud_sync_uploadForced && '
                  'cloud_sync_downloadEmu y && echo OK')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "OK")

    def test_scriptconfig_present_and_default(self):
        d = Path(tempfile.mkdtemp())
        cfg = d / "emu.config"
        cfg.write_text("FORCED_PROTON_VER=GE-Proton9-5\nOTHER=1\n")
        r = _bash(f'. "{LIB}/emudeck-shim.sh"; '
                  f'scriptConfigFileGetVar "{cfg}" FORCED_PROTON_VER fallback; echo; '
                  f'scriptConfigFileGetVar "{cfg}" MISSING_VAR fallback; echo')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.split("\n")[0], "GE-Proton9-5")
        self.assertEqual(r.stdout.split("\n")[1], "fallback")

    def test_override_flows_into_shim(self):
        r = _bash(f'. "{LIB}/emudeck-shim.sh"; echo "$romsPath|$storagePath"',
                  env={"MAD_DATA_ROOT": "/tmp/standalone"})
        self.assertEqual(r.stdout.strip(), "/tmp/standalone/roms|/tmp/standalone/storage")

    def test_guard_prefers_real_emudeck_when_present(self):
        d = Path(tempfile.mkdtemp())
        fake = d / "all.sh"
        # a stand-in all.sh that marks itself + defines the funcs the launcher needs
        fake.write_text('EMUDECK_REAL=1\nemulatorInit(){ :; }\ncloud_sync_uploadForced(){ :; }\n')
        r = _bash(GUARD + 'echo "real=${EMUDECK_REAL:-0}"',
                  env={"EMUDECK_FUNCTIONS": str(fake)})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "real=1")   # sourced the real all.sh, not the shim

    def test_guard_falls_back_to_shim_when_absent(self):
        r = _bash(GUARD + 'type emulatorInit >/dev/null && echo "shim=${storagePath##*/}"',
                  env={"EMUDECK_FUNCTIONS": "/nonexistent/all.sh"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "shim=storage")


class MadPathsShell(unittest.TestCase):
    """lib/mad-paths.sh: the roots the converted util + backup/restore scripts use
    must equal the legacy ~/Emulation/* under default env (non-regression)."""

    def test_roots_match_legacy(self):
        r = _bash(f'. "{LIB}/mad-paths.sh"; '
                  'echo "$MAD_DATA_ROOT|$storageRoot|$romsRoot|$toolsRoot|$savesRoot|$biosRoot"')
        self.assertEqual(r.returncode, 0, r.stderr)
        h = os.path.expanduser("~")
        self.assertEqual(r.stdout.strip(),
                         f"{h}/Emulation|{h}/Emulation/storage|{h}/Emulation/roms|"
                         f"{h}/Emulation/tools|{h}/Emulation/saves|{h}/Emulation/bios")

    def test_backup_restore_compositions_match_legacy(self):
        # the exact paths deck-backup.sh / deck-restore.sh now build
        r = _bash(f'. "{LIB}/mad-paths.sh"; '
                  'echo "$savesRoot|$biosRoot|$storageRoot/rpcs3/dev_hdd0/game|$storageRoot|$storageRoot/xemu"')
        h = os.path.expanduser("~")
        self.assertEqual(r.stdout.strip(),
                         f"{h}/Emulation/saves|{h}/Emulation/bios|"
                         f"{h}/Emulation/storage/rpcs3/dev_hdd0/game|"
                         f"{h}/Emulation/storage|{h}/Emulation/storage/xemu")

    def test_override_rebases_roots(self):
        r = _bash(f'. "{LIB}/mad-paths.sh"; echo "$storageRoot|$romsRoot"',
                  env={"MAD_DATA_ROOT": "/tmp/alt"})
        self.assertEqual(r.stdout.strip(), "/tmp/alt/storage|/tmp/alt/roms")


if __name__ == "__main__":
    unittest.main()
