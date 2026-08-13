"""mad-backend --selfcheck runs clean in a fresh interpreter (hermetic subprocess).

Before this test the selfcheck import path had ZERO coverage: tests/test_recovery.py only
exercises the evdev-missing fatal, which returns before the big import. This is the fence
that catches a module falling out of the backend's import list - the failure the panel
sees as a whole page answering ENOMETHOD.

Run: python3 -m unittest tests.test_selfcheck -v
"""
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "mad-backend.py"

# The frozen backend module list: every lib/madsrv command module the daemon serves.
# This mirrors mad-backend._ALL_CMDS on purpose - a module dropped from the tuple (typo,
# merge conflict) would make a whole panel page answer ENOMETHOD while every other test
# stays green, because tests import their modules directly. Update BOTH on any change.
_EXPECTED = frozenset({
    "backends_cmds", "backup_cmds", "bezel_cmds", "capture_cmds",
    "cemu_games", "cemu_hh_cmds", "cemu_hhpacks_cmds", "cemu_input_cmds",
    "cemu_packs_cmds", "cemu_pergame", "cemu_pg_input_cmds",
    "cemu_pgmap_cmds", "cemu_res_cmds", "cemu_settings",
    "citron_addons_cmds", "citron_cheats_cmds", "citron_dock_cmds",
    "citron_games", "citron_hotkeys_cmds", "citron_pergame",
    "citron_pg_input_cmds", "citron_settings", "cloud_cmds", "daphne_cmds",
    "device_cmds", "dolphin_codes_cmds", "dolphin_games",
    "dolphin_gc_pads_cmds", "dolphin_hotkeys_cmds", "dolphin_pergame_cmds",
    "dolphin_profile_cmds", "dolphin_settings", "dolphin_wii_hh_cmds",
    "eden_addons_cmds", "eden_cheats_cmds", "eden_cmds", "eden_dock_cmds",
    "eden_hotkeys_cmds", "eden_pergame", "eden_pg_input_cmds",
    "eden_settings", "granular_cmds", "guncon2_retail_input_cmds",
    "lindbergh_cmds", "model2_cmds", "model3_cmds", "mugen_cmds",
    "mugen_onthego_cmds", "onthego_cmds", "pads_cmds",
    "pcsx2_blacklist_cmds", "pcsx2_cmds", "pcsx2_fork_settings",
    "pcsx2_games", "pcsx2_hotkeys_cmds", "pcsx2_pergame_cmds",
    "pcsx2_pergame_input_cmds", "pcsx2_profile_cmds", "pcsx2_settings",
    "pcsx2x6_cmds", "pcsx2x6_global_cmds", "pcsx2x6_hotkeys_cmds",
    "pcsx2x6_input_cmds", "pcsx2x6_lightgun_cmds",
    "pcsx2x6_retail_input_cmds", "policy_cmds", "policy_settings_cmds",
    "postupdate_cmds", "preview_cmds", "ra_profiles_cmds",
    "retroarch_cmds", "retroarch_game_cmds", "retroarch_settings", "rpc",
    "rpcs3_patches_cmds", "rpcs3_pergame_cmds", "rpcs3_profile_cmds",
    "rpcs3_ps_cmds", "rpcs3_settings", "ryujinx_addons_cmds",
    "ryujinx_cheats_cmds", "ryujinx_cmds", "ryujinx_dock_cmds",
    "ryujinx_hotkeys_cmds", "ryujinx_pergame", "ryujinx_profile_cmds",
    "ryujinx_settings", "sidebar_cmds", "sinden_cmds", "standalones_cmds",
    "systems_cmds", "tester_cmds", "xemu_input_cmds", "yuzu_profile_cmds",
})


class Selfcheck(unittest.TestCase):
    def test_selfcheck_ok_and_hermetic(self):
        try:
            import evdev  # noqa: F401
        except ImportError:
            self.skipTest("python-evdev not installed (CI installs it; the Deck has it)")
        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as home:
            # Redirect HOME too, not just MAD_DATA_ROOT (phase-3 lesson: a fence that
            # watches only the data root is blind to ~/.config writes).
            env = dict(os.environ, MAD_DATA_ROOT=td, HOME=home,
                       PYTHONDONTWRITEBYTECODE="1")
            r = subprocess.run([sys.executable, str(BACKEND), "--selfcheck"],
                               cwd=ROOT, env=env, capture_output=True, text=True,
                               timeout=120)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("selfcheck OK", r.stdout)
            # Hermeticity tripwire: importing the backend must never WRITE anywhere
            # under the data root OR the home dir (registration only; RUN_DIR mkdir
            # happens after the selfcheck early-return).
            self.assertEqual(os.listdir(td), [],
                             "a backend import wrote into the data root at import time")
            self.assertEqual(os.listdir(home), [],
                             "a backend import wrote into $HOME at import time")

    def test_all_cmds_matches_frozen_set(self):
        # Load mad-backend.py as a module WITHOUT running main(): its module level is
        # sys.path setup + pure path constants + the _ALL_CMDS tuple, no IO.
        spec = importlib.util.spec_from_file_location("mad_backend_under_test", BACKEND)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.assertEqual(len(mod._ALL_CMDS), len(set(mod._ALL_CMDS)),
                         "duplicate name in _ALL_CMDS")
        self.assertEqual(set(mod._ALL_CMDS), set(_EXPECTED))
        self.assertEqual(list(mod._ALL_CMDS), sorted(mod._ALL_CMDS),
                         "_ALL_CMDS must stay alphabetized (merge-conflict hygiene)")


if __name__ == "__main__":
    unittest.main()
