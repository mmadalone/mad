"""The OpenBOR "Reset a game's controls" row: MAD's only pad-reachable way back
once openbor_cfg has seeded a game and handed its cfg to the engine.

It is a `choice` knob on the Controllers page (backends.describe) whose pick is
routed by policy.set_backend_key's magic-key path — the same trick __sysflag__
uses. It lives there, and not as a sibling row on the tile, so the tile keeps
exactly ONE section and opens straight into the page with no chooser.
Run: python3 -m unittest tests.test_openbor_reset_row -v
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from lib import openbor_maps as M                   # noqa: E402
from lib.madsrv import backends_cmds, policy_cmds   # noqa: E402


class ResetRow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = mock.patch.object(M, "_STORE",
                                       Path(self.tmp.name) / "input-maps.json")
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def _pick(self, value):
        return policy_cmds._set_backend_key(
            {"backend": "openbor", "key": "__openbor_reseed__", "value": value})

    def test_picking_a_game_forgets_only_that_game(self):
        M.mark_seeded("GHDC")
        M.mark_seeded("MIW_Definitive")
        self._pick("GHDC")
        self.assertFalse(M.is_seeded("GHDC"), "the pick did not reset the game")
        self.assertTrue(M.is_seeded("MIW_Definitive"), "it reset a bystander")

    def test_an_empty_value_is_a_no_op_never_a_rig_wide_wipe(self):
        # LOAD-BEARING: openbor_maps.clear_seeded(None) forgets EVERY game, so a
        # stray empty value must do nothing at all. The picker offers no "none"
        # option for the same reason.
        M.mark_seeded("GHDC")
        M.mark_seeded("MIW_Definitive")
        for bad in ("", None):
            self._pick(bad)
        self.assertEqual(M.seeded_keys(), ["GHDC", "MIW_Definitive"])

    def test_the_pick_never_reaches_the_policy_file(self):
        # It is not a config knob: it must not be written into the local policy
        # overlay as if openbor had a "__openbor_reseed__" setting.
        with mock.patch.object(policy_cmds.localpolicy, "dump") as dump:
            self._pick("GHDC")
        dump.assert_not_called()

    def test_the_row_is_offered_on_the_openbor_page_only(self):
        keys = [k["key"] for k in
                backends_cmds._backends_describe({"backend": "openbor"})["knobs"]]
        self.assertIn("__openbor_reseed__", keys)
        self.assertNotIn("sdl_priority", keys,
                         "a knob the merger makes inert is still being offered")
        for other in ("rpcs3", "dolphin"):
            try:
                ks = [k["key"] for k in
                      backends_cmds._backends_describe({"backend": other})["knobs"]]
            except Exception:
                continue
            self.assertNotIn("__openbor_reseed__", ks, f"leaked onto {other}")

    def test_every_launchable_game_is_offered_and_marked(self):
        M.mark_seeded("GHDC")
        knob = next(k for k in
                    backends_cmds._backends_describe({"backend": "openbor"})["knobs"]
                    if k["key"] == "__openbor_reseed__")
        vals = [o["value"] for o in knob["options"]]
        self.assertNotIn("", vals, "a 'none' option would wipe every game")
        self.assertEqual(len(vals), len(set(vals)), "duplicate games offered")
        marks = {o["value"]: o["label"][:1] for o in knob["options"]}
        if "GHDC" in marks:                       # skip when the library is absent
            self.assertEqual(marks["GHDC"], "✓", "a seeded game is not marked")
            others = [v for v in vals if v != "GHDC"]
            if others:
                self.assertEqual(marks[others[0]], "·")


if __name__ == "__main__":
    unittest.main()
