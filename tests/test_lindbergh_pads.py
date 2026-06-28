"""Tests for the per-game per-pad Lindbergh controller profiles (lib/lindbergh_pads.py):
the priority->slot resolver, the ini materializer/restore, and the sidecar store. Pure
where possible; materialize() monkeypatches loader_tags so it needs no real hardware.
Run with the rest:  python3 -m unittest discover -s tests -t .
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lib import lindbergh_pads as P

PADS = {"XARC": {"BUTTON_1": "BTN_SOUTH", "BUTTON_START": "BTN_START"},
        "DS":   {"BUTTON_1": "BTN_SOUTH"}}


class Resolve(unittest.TestCase):
    def test_priority_order(self):
        self.assertEqual(P.resolve(["XARC", "DS"], PADS, {"XARC", "DS"}, 2), {1: "XARC", 2: "DS"})

    def test_seamless_fallback(self):
        # top-priority pad absent -> next connected pad takes Player 1 (the whole point)
        self.assertEqual(P.resolve(["XARC", "DS"], PADS, {"DS"}, 2), {1: "DS"})

    def test_none_connected(self):
        self.assertEqual(P.resolve(["XARC", "DS"], PADS, set(), 2), {})

    def test_skips_profileless_tag(self):
        pads = {"XARC": {"BUTTON_1": "BTN_SOUTH"}, "DS": {}}  # DS has no map
        self.assertEqual(P.resolve(["DS", "XARC"], pads, {"XARC", "DS"}, 2), {1: "XARC"})

    def test_player_cap(self):
        self.assertEqual(P.resolve(["XARC", "DS"], PADS, {"XARC", "DS"}, 1), {1: "XARC"})

    def test_extras_after_priority(self):
        # a configured pad missing from priority is still assignable (appended)
        self.assertEqual(P.resolve([], PADS, {"XARC", "DS"}, 2),
                         {1: list(PADS)[0], 2: list(PADS)[1]})


SAMPLE = ("[Display]\nWIDTH = 1280\n\n[EVDEV]\n"
          'PLAYER_1_BUTTON_1 = "OLD1"\nPLAYER_2_BUTTON_1 = "OLD2"\nTEST_BUTTON = "KEEP"\n')


class RenderIni(unittest.TestCase):
    def test_fills_slot_blanks_unassigned_preserves_others(self):
        out = P.render_ini(SAMPLE, {1: "DS"}, PADS, 2)
        self.assertIn('PLAYER_1_BUTTON_1 = "DS_BTN_SOUTH"', out)   # slot 1 = DS map
        self.assertIn('PLAYER_2_BUTTON_1 = ""', out)               # slot 2 unassigned -> blank
        self.assertIn('TEST_BUTTON = "KEEP"', out)                 # untouched
        self.assertIn("WIDTH = 1280", out)

    def test_two_slots(self):
        out = P.render_ini(SAMPLE, {1: "XARC", 2: "DS"}, PADS, 2)
        self.assertIn('PLAYER_1_BUTTON_1 = "XARC_BTN_SOUTH"', out)
        self.assertIn('PLAYER_1_BUTTON_START = "XARC_BTN_START"', out)
        self.assertIn('PLAYER_2_BUTTON_1 = "DS_BTN_SOUTH"', out)

    def test_no_evdev_returns_none(self):
        self.assertIsNone(P.render_ini("[Display]\nWIDTH = 1\n", {1: "DS"}, PADS, 2))


def _game(tmp: Path, ini_text: str = SAMPLE) -> Path:
    gd = tmp / "id5.lindbergh"
    gd.mkdir(parents=True)
    (gd / "id5.lindbergh.commands").write_text("game.elf\n")
    (gd / "game.elf").write_text("x")
    P.ini_of(gd).write_text(ini_text)
    return gd


class SidecarStore(unittest.TestCase):
    def test_roundtrip_and_prune(self):
        gd = _game(Path(tempfile.mkdtemp()))
        P.save(gd, {"priority": ["XARC", "DS", "GHOST", "XARC"], "pads": dict(PADS, EMPTY={})})
        data = P.load(gd)
        self.assertEqual(set(data["pads"]), {"XARC", "DS"})        # empty map dropped
        # priority kept as given (deduped); a not-yet-mapped pad (GHOST) is retained for ordering
        self.assertEqual(data["priority"], ["XARC", "DS", "GHOST"])
        self.assertEqual(data["version"], 1)

    def test_missing_is_empty(self):
        gd = _game(Path(tempfile.mkdtemp()))
        self.assertEqual(P.load(gd), {})


class Materialize(unittest.TestCase):
    def _tags(self, *tags):
        return lambda: [{"path": f"/d{i}", "name": t, "tag": t} for i, t in enumerate(tags)]

    def test_materialize_and_restore(self):
        gd = _game(Path(tempfile.mkdtemp()))
        ini = P.ini_of(gd)
        P.save(gd, {"priority": ["XARC", "DS"], "pads": PADS})
        with mock.patch.object(P, "loader_tags", self._tags("DS")):   # only DS connected
            res = P.materialize(gd, 2)
        self.assertTrue(res["applied"])
        self.assertEqual(res["slots"], {"1": "DS"})
        self.assertIn('PLAYER_1_BUTTON_1 = "DS_BTN_SOUTH"', ini.read_text())
        self.assertTrue(ini.with_name(ini.name + P.RESTORE_SUFFIX).exists())
        # restore brings back the canonical ini and removes the backup
        self.assertTrue(P.restore(gd))
        self.assertIn('PLAYER_1_BUTTON_1 = "OLD1"', ini.read_text())
        self.assertFalse(ini.with_name(ini.name + P.RESTORE_SUFFIX).exists())

    def test_no_sidecar_is_noop(self):
        gd = _game(Path(tempfile.mkdtemp()))
        ini = P.ini_of(gd)
        before = ini.read_text()
        with mock.patch.object(P, "loader_tags", self._tags("DS")):
            res = P.materialize(gd, 2)
        self.assertFalse(res["applied"])
        self.assertEqual(ini.read_text(), before)                  # untouched
        self.assertFalse(ini.with_name(ini.name + P.RESTORE_SUFFIX).exists())

    def test_none_connected_is_noop(self):
        gd = _game(Path(tempfile.mkdtemp()))
        ini = P.ini_of(gd)
        P.save(gd, {"priority": ["XARC"], "pads": {"XARC": {"BUTTON_1": "BTN_SOUTH"}}})
        before = ini.read_text()
        with mock.patch.object(P, "loader_tags", self._tags("SOMETHING_ELSE")):
            res = P.materialize(gd, 2)
        self.assertFalse(res["applied"])
        self.assertEqual(ini.read_text(), before)
        self.assertFalse(ini.with_name(ini.name + P.RESTORE_SUFFIX).exists())

    def test_restore_reverts_only_evdev_keeps_settings(self):
        # A MAD Settings edit (non-EVDEV) made to the live ini while a stale .mad-restore exists must
        # survive the next restore — restore reverts only [EVDEV], never clobbers settings (rule #5).
        gd = _game(Path(tempfile.mkdtemp()),
                   '[Emulation]\nREGION = US\n\n[EVDEV]\nPLAYER_1_BUTTON_1 = "OLD1"\n')
        ini = P.ini_of(gd)
        P.save(gd, {"priority": ["DS"], "pads": {"DS": {"BUTTON_1": "BTN_SOUTH"}}})
        with mock.patch.object(P, "loader_tags", self._tags("DS")):
            P.materialize(gd, 2)                      # materializes [EVDEV], backs up canonical
        ini.write_text(ini.read_text().replace("REGION = US", "REGION = JP"))  # simulate a MAD edit
        self.assertTrue(P.restore(gd))
        out = ini.read_text()
        self.assertIn("REGION = JP", out)                         # settings edit preserved
        self.assertIn('PLAYER_1_BUTTON_1 = "OLD1"', out)          # [EVDEV] reverted to canonical

    def test_missed_restore_preserves_canonical(self):
        gd = _game(Path(tempfile.mkdtemp()))
        ini = P.ini_of(gd)
        P.save(gd, {"priority": ["XARC", "DS"], "pads": PADS})
        with mock.patch.object(P, "loader_tags", self._tags("XARC")):
            P.materialize(gd, 2)                                    # backup = canonical (OLD1)
        with mock.patch.object(P, "loader_tags", self._tags("DS")):
            P.materialize(gd, 2)                                    # 2nd launch, restore was missed
        # the .mad-restore must STILL hold the canonical OLD1, not the 1st materialization
        bak = ini.with_name(ini.name + P.RESTORE_SUFFIX)
        self.assertIn('PLAYER_1_BUTTON_1 = "OLD1"', bak.read_text())
        P.restore(gd)
        self.assertIn('PLAYER_1_BUTTON_1 = "OLD1"', ini.read_text())


if __name__ == "__main__":
    unittest.main()
