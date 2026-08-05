"""Ryujinx per-game input: STILL deliberately absent.

History, two decisions deep:
  * The PER-GAME input page (ryujinx_pg_input.*) was removed earlier. A Ryujinx profile is a
    device+mapping pin; MAD's bake copied only the mapping and left the slot's (cloned) device,
    and the launch router reassigned devices by the global pads->players order anyway -- so
    "pick DS for P1" never bound the DS. Device -> player is owned by the global
    Controllers -> pads -> players routing.
  * The GLOBAL picker this file used to drive (``ryujinx.selector_set key=profile``, a selector
    buried inside the per-button editor page) was removed 2026-08-04 along with that page. It did
    not disappear: it became its own per-player picker, ``ryujinx_input_docked`` /
    ``ryujinx_input_handheld``, with the mapping-only bake moved verbatim into
    lib/ryujinx_profiles.MAP_KEYS.

The assertions that used to live here now live where the behaviour does:
  * mapping-only bake + the Handheld controller_type clamp -> tests/test_input_profile_resolvers.py
  * the overlay preserving id/backend/player_index         -> tests/test_ryujinx_cfg.py (ProfileBake)
  * the picker page itself                                  -> tests/test_input_profile_pages.py
What remains here is the guard that keeps per-game input from quietly coming back.

Run: python3 -m unittest tests.test_ryujinx_pg_input -v
"""
from __future__ import annotations

import unittest

from lib.madsrv import rpc, ryujinx_pergame, ryujinx_profile_cmds  # noqa: F401  (register methods)


class PerGameInputStaysGone(unittest.TestCase):
    def test_pergame_input_methods_unregistered(self):
        self.assertNotIn("ryujinx_pg_input.get", rpc._METHODS)
        self.assertNotIn("ryujinx_pg_input.set", rpc._METHODS)

    def test_the_removed_editor_namespace_is_gone_too(self):
        # ryujinx.input_* / ryujinx.selector_set belonged to the per-button editor page.
        for ns in ("ryujinx.input_get", "ryujinx.input_set", "ryujinx.input_clear",
                   "ryujinx.selector_set", "ryujinx.input_save", "ryujinx.input_cancel"):
            self.assertNotIn(ns, rpc._METHODS, ns)

    def test_the_picker_that_replaced_it_is_registered(self):
        for ns in ("ryujinx_input_docked.get", "ryujinx_input_docked.set",
                   "ryujinx_input_handheld.get", "ryujinx_input_handheld.set"):
            self.assertIn(ns, rpc._METHODS, ns)


if __name__ == "__main__":
    unittest.main()
