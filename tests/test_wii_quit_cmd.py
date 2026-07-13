"""es_systems.quit_cmd for Wii: an explicit [backends.dolphin].quit_cmd enables the evdev pad-combo
quit (for Classic-Controller / gamepad play) and wins over the historical HID-only "" default.

Run:  python3 -m unittest tests.test_wii_quit_cmd -v
"""
from __future__ import annotations

import unittest

from lib import es_systems


class WiiQuitCmd(unittest.TestCase):
    def setUp(self):
        self._orig = (es_systems.default_command, es_systems.is_standalone, es_systems._resolve_backend)
        es_systems.default_command = lambda s, systems=None: "/usr/bin/dolphin-emu %ROM%"
        es_systems.is_standalone = lambda cmd: True
        es_systems._resolve_backend = lambda policy, system: "dolphin"

    def tearDown(self):
        (es_systems.default_command, es_systems.is_standalone, es_systems._resolve_backend) = self._orig

    def test_explicit_quit_cmd_wins_over_hid_default(self):
        pol = {"backends": {"dolphin": {"quit_cmd": "pkill -TERM -f dolphin"}}}
        self.assertEqual(es_systems.quit_cmd("wii", pol), "pkill -TERM -f dolphin")

    def test_no_quit_cmd_keeps_hid_only_default(self):
        # Without an explicit quit_cmd, Wii still returns "" (real Wii Remotes quit via the +/- watcher).
        self.assertEqual(es_systems.quit_cmd("wii", {"backends": {"dolphin": {}}}), "")

    def test_empty_quit_cmd_still_opts_out(self):
        pol = {"backends": {"dolphin": {"quit_cmd": ""}}}
        self.assertEqual(es_systems.quit_cmd("wii", pol), "")


class QuitComboSystems(unittest.TestCase):
    """quit_combo_systems lists only systems with a gamelist that has >=1 VISIBLE game AND a
    non-empty quit command. An emptied stub (xbox's <gameList/> with 0 games) must drop out even
    though its gamelist.xml file still exists -- the bug where xbox lingered in the add-quit-combo
    picker. Both the RPC (policy_cmds.quitcombo.get) and the Tk GUI route through this fn."""

    def setUp(self):
        from lib import es_gamelist
        self._egl = es_gamelist
        self._orig = (es_systems.load_systems, es_systems._has_gamelist,
                      es_systems.quit_cmd, es_gamelist.visible_records)
        es_systems.load_systems = lambda: {"xbox": [], "ps2": []}
        es_systems._has_gamelist = lambda s: True                          # both gamelist files exist
        es_systems.quit_cmd = lambda s, policy, systems=None: "pkill -TERM -f emu"

    def tearDown(self):
        (es_systems.load_systems, es_systems._has_gamelist,
         es_systems.quit_cmd, self._egl.visible_records) = self._orig

    def test_emptied_gamelist_system_dropped(self):
        self._egl.visible_records = lambda s: {"g": 1} if s == "ps2" else {}   # xbox emptied
        self.assertEqual(es_systems.quit_combo_systems({}), ["ps2"])           # xbox gone

    def test_both_kept_when_both_have_games(self):
        self._egl.visible_records = lambda s: {"g": 1}                         # both have games
        self.assertEqual(es_systems.quit_combo_systems({}), ["ps2", "xbox"])   # sorted, both shown


if __name__ == "__main__":
    unittest.main()
