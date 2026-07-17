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

    def _pick(self, value):
        return policy_cmds._set_backend_key(
            {"backend": "openbor", "key": "__openbor_reseed__", "value": value})



class ResetRow(_Base):

    def test_picking_a_game_forgets_only_that_game(self):
        M.mark_seeded("GHDC")
        M.mark_seeded("MIW_Definitive")
        self._pick("GHDC")
        self.assertFalse(M.is_seeded("GHDC"), "the pick did not reset the game")
        self.assertTrue(M.is_seeded("MIW_Definitive"), "it reset a bystander")

    def test_an_empty_value_is_a_no_op_never_a_rig_wide_wipe(self):
        # LOAD-BEARING: openbor_maps.clear_seeded(None) forgets EVERY game, so a
        # stray empty value must do nothing at all. Since f103072 the picker's
        # FIRST row IS "" on purpose (the inert "Nothing selected" the cursor parks
        # on), so this is now the behaviour of a row the user can actually hit, not
        # only a defence against a stray value.
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

    def _knob(self):
        return next(k for k in
                    backends_cmds._backends_describe({"backend": "openbor"})["knobs"]
                    if k["key"] == "__openbor_reseed__")

    def test_every_launchable_game_is_offered_and_marked(self):
        M.mark_seeded("GHDC")
        knob = self._knob()
        vals = [o["value"] for o in knob["options"]]
        self.assertEqual(vals, ["", "Contra", "GHDC", "MIW_Definitive"],
                         "the picker does not offer the inert row + exactly the "
                         "launchable games")
        self.assertEqual(len(vals), len(set(vals)), "duplicate games offered")
        marks = {o["value"]: o["label"][:1] for o in knob["options"]}
        self.assertEqual(marks["GHDC"], "✓", "a seeded game is not marked")
        self.assertEqual(marks["Contra"], "·", "an unseeded game is marked seeded")

    def test_the_cursor_parks_on_an_inert_row_not_on_a_game(self):
        # THE FINDING (2026-07-17). This knob is an ACTION, so its value is "". The
        # C++ selects with `mList->addRow(row, value == mCurrent)`; when nothing
        # matched, IList defaulted the cursor to row 0 = THE FIRST GAME. Open the
        # row and press A again -- to scroll, to back out, or because A is the
        # button that just opened it -- and that game was reset: no confirmation,
        # no undo, and its next launch overwrites every in-game rebind. Row 0 must
        # be an option whose value EQUALS the knob's value, so the cursor lands on
        # it by the same rule every other picker uses.
        knob = self._knob()
        self.assertEqual(knob["options"][0]["value"], knob["value"],
                         "row 0 does not match the knob's value, so the C++ cursor "
                         "falls back to row 0 = the first GAME")
        self.assertEqual(knob["options"][0]["value"], "",
                         "row 0 is not the inert value")

    def test_choosing_the_inert_row_resets_nothing(self):
        M.mark_seeded("GHDC")
        M.mark_seeded("MIW_Definitive")
        inert = self._knob()["options"][0]["value"]
        self._pick(inert)
        self.assertEqual(M.seeded_keys(), ["GHDC", "MIW_Definitive"],
                         "the reflex A-press reset a game")

    def test_the_row_reads_as_nothing_selected_until_you_pick(self):
        # It also stops the row rendering as a setting whose value is permanently
        # "none" (the _choice_knob fallback when no option matches).
        knob = self._knob()
        self.assertEqual(knob["value_label"], "Nothing selected")
        self.assertNotEqual(knob["value_label"], "none")


class Unmanageable(_Base):
    """A game MAD can NEVER write must not be offered a reset it cannot perform.

    Jennifer_By_MasterDerico ships the 2010-era 248-byte save struct, whose layout
    is unverified, so openbor_cfg refuses it FOREVER (SKIP_SIZES). MAD therefore
    never seeds it — yet the row listed it, and picking it cleared a seed mark that
    was never set and flashed "applied at next launch" for a write that never comes.
    """
    LIBRARY = (("Contra", "Contra"),
               ("Jennifer", "Jennifer"),
               ("MIW_Definitive", "MIW_Definitive"))

    def setUp(self):
        super().setUp()
        # Real shapes: Contra/MIW writable, Jennifer a 248-byte 2010 save.
        self.root = Path(self.tmp.name) / "OpenBor"
        for name, size in (("Contra", 352), ("MIW_Definitive", 352), ("Jennifer", 248)):
            (self.root / name / "Saves").mkdir(parents=True)
            (self.root / name / "Saves" / f"{name.lower()}.cfg").write_bytes(b"\0" * size)
        self._rom = mock.patch.object(MAN, "rom_dir", lambda: self.root)
        self._rom.start()

    def tearDown(self):
        self._rom.stop()
        super().tearDown()

    def _vals(self):
        knob = next(k for k in
                    backends_cmds._backends_describe({"backend": "openbor"})["knobs"]
                    if k["key"] == "__openbor_reseed__")
        return [o["value"] for o in knob["options"]]

    def test_a_game_mad_can_never_write_is_not_offered(self):
        vals = self._vals()
        self.assertNotIn("Jennifer", vals,
                         "the row offers a reset it can never perform")
        self.assertIn("Contra", vals, "a writable game was dropped")
        self.assertIn("MIW_Definitive", vals)

    def test_a_game_with_no_cfg_yet_is_STILL_offered(self):
        # The distinction that matters: no cfg / no engine log is TEMPORARY (the
        # game has just never run), and it seeds on a later launch. Only the
        # 248-byte struct is forever. Treating "not yet" as "never" would hide most
        # of a fresh library.
        (self.root / "Contra" / "Saves" / "contra.cfg").unlink()
        self.assertIn("Contra", self._vals(),
                      "a game that simply has not run yet was treated as unwritable")

    def test_picking_it_anyway_says_the_true_thing(self):
        # The page is CACHED, so a stale list can still send it.
        M.mark_seeded("Jennifer")
        flash = self._pick("Jennifer").get("flash", "")
        self.assertIn("does not manage", flash)
        self.assertNotIn("next launch", flash, "it still promises a write")
        self.assertNotIn("Reset", flash)
        self.assertTrue(M.is_seeded("Jennifer"),
                        "it cleared a seed mark for a game it cannot write")


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
