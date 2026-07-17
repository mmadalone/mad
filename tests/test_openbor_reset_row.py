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
from lib import openbor_manifests as MAN            # noqa: E402
from lib import openbor_maps as M                   # noqa: E402
from lib.madsrv import backends_cmds, policy_cmds   # noqa: E402

# A FIXED library. The row is built from openbor_manifests, which scans the real
# ~/OpenBor: without this the tests assert against whatever games this machine
# happens to have, and on a machine with none (CI) the row is legitimately absent
# and they fail for a reason that is not a bug. `_scan` is the one chokepoint both
# dir_keys() and names() go through, and stubbing the attribute also sidesteps its
# lru_cache. Sorted, as the real _scan returns sorted.
FAKE_LIBRARY = (("Contra", "Contra"),
                ("GHDC", "GHDC"),
                ("MIW_Definitive", "MIW_Definitive"))


class _Base(unittest.TestCase):
    LIBRARY = FAKE_LIBRARY

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = mock.patch.object(M, "_STORE",
                                       Path(self.tmp.name) / "input-maps.json")
        self.patch.start()
        self._lib = mock.patch.object(MAN, "_scan", lambda: self.LIBRARY)
        self._lib.start()
        # names() would otherwise fold in this machine's ES-DE gamelist titles.
        self._titles = mock.patch.object(MAN.es_gamelist, "titles", lambda _s: {})
        self._titles.start()

    def tearDown(self):
        self._titles.stop()
        self._lib.stop()
        self.patch.stop()
        self.tmp.cleanup()


class ResetRow(_Base):

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

    def test_the_pick_names_its_own_outcome(self):
        # The page falls back to "Saved <backend>.<key> = <value>", which for a
        # magic key leaks the raw key AND claims something untrue (nothing was
        # saved). GuiMadPageBackendDetail prefers a "flash" from the payload.
        M.mark_seeded("GHDC")
        flash = self._pick("GHDC").get("flash", "")
        self.assertTrue(flash, "no flash: the page would leak __openbor_reseed__")
        self.assertNotIn("__openbor_reseed__", flash)
        self.assertNotIn("Saved", flash, "it did not save anything")
        self.assertIn("next launch", flash, "must say WHEN it takes effect")
        # Standing rule: plain ASCII in user-facing text (no em/en-dash, no arrows).
        for ch in "—–→":
            self.assertNotIn(ch, flash)

    def test_an_empty_pick_flashes_nothing(self):
        self.assertNotIn("flash", self._pick(""), "a no-op must not claim a reset")

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
        self.assertEqual(vals, ["Contra", "GHDC", "MIW_Definitive"],
                         "the picker does not offer exactly the launchable games")
        self.assertNotIn("", vals, "a 'none' option would wipe every game")
        self.assertEqual(len(vals), len(set(vals)), "duplicate games offered")
        marks = {o["value"]: o["label"][:1] for o in knob["options"]}
        self.assertEqual(marks["GHDC"], "✓", "a seeded game is not marked")
        self.assertEqual(marks["Contra"], "·", "an unseeded game is marked seeded")


class NoLibrary(_Base):
    """A machine with no OpenBOR games at all (a fresh install, or CI).

    `if _opts:` in backends_cmds already does the right thing here; nothing pinned
    it, and the reason it was worth pinning is that the tests above USED to read
    the real ~/OpenBor and failed on exactly this machine shape.
    """
    LIBRARY = ()

    def test_the_row_is_not_offered_at_all(self):
        keys = [k["key"] for k in
                backends_cmds._backends_describe({"backend": "openbor"})["knobs"]]
        self.assertNotIn("__openbor_reseed__", keys,
                         "an empty picker offers a reset that cannot resolve")

    def test_the_page_still_describes(self):
        # The rest of the Controllers page must survive a game-less library.
        d = backends_cmds._backends_describe({"backend": "openbor"})
        self.assertIn("pad_classes", [k["key"] for k in d["knobs"]])


if __name__ == "__main__":
    unittest.main()
