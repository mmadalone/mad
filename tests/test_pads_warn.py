"""pads.get surfaces each console emulator's lone X-Arcade `warn` flag on its Controllers
(pads-to-players) page, so the control survives tile gridification (the inline tile chip is dropped
by _gridify_tile). ps2/ps3/xbox/switch(eden/ryujinx/citron)/gc get a `warn` descriptor
{label, system, flag, value}; the Namco arcade fork (pcsx2x6) and wii (which keeps its own
"Controller options" page) do NOT. The C++ pads page renders it as a switch above Hands-off and
writes it back via policy.set_system_flag {system, flag, value}.

Regression for the warn-toggle-on-Controllers-page work.
Run:  python3 -m unittest tests.test_pads_warn -v
"""
from __future__ import annotations

import unittest

from lib.madsrv import pads_cmds


def _warn(emu):
    return pads_cmds._pads_get({"emu": emu}).get("warn")


class PadsWarn(unittest.TestCase):
    def test_console_emus_carry_warn(self):
        cases = {"pcsx2": "ps2", "rpcs3": "ps3", "xemu": "xbox",
                 "eden": "switch", "ryujinx": "switch", "citron": "switch",
                 "dolphin_gc": "gc"}
        for emu, system in cases.items():
            w = _warn(emu)
            self.assertIsNotNone(w, f"{emu} should carry a warn descriptor")
            self.assertEqual(w["system"], system, emu)
            self.assertEqual(w["flag"], "warn_when_only_xarcade", emu)
            self.assertIsInstance(w["value"], bool, emu)
            self.assertTrue(w["label"], emu)          # non-empty descriptive label

    def test_non_console_and_wii_have_no_warn(self):
        # pcsx2x6 = Namco arcade group (warn_when_no_xarcade, not a console-warn system);
        # dolphin_wii keeps its warn on the "Controller options" page, not the pads page.
        self.assertIsNone(_warn("pcsx2x6"))
        self.assertIsNone(_warn("dolphin_wii"))


if __name__ == "__main__":
    unittest.main()
