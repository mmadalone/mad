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


if __name__ == "__main__":
    unittest.main()
