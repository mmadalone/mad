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

    def test_unassigned_slot_kept_canonical_when_blank_disabled(self):
        # legacy/unknown shape: a whole unassigned slot is left at canonical, not blanked, so a
        # single-driver game's gear shifter on PLAYER_2 survives until the page heals the sidecar
        out = P.render_ini(SAMPLE, {1: "DS"}, PADS, 2, blank_unassigned=False)
        self.assertIn('PLAYER_1_BUTTON_1 = "DS_BTN_SOUTH"', out)   # slot 1 written
        self.assertIn('PLAYER_2_BUTTON_1 = "OLD2"', out)           # slot 2 unassigned -> canonical kept

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
        self.assertEqual(data["version"], 2)   # save() now writes the v2 schema (may carry analog)

    def test_missing_is_empty(self):
        gd = _game(Path(tempfile.mkdtemp()))
        self.assertEqual(P.load(gd), {})

    def test_load_drops_corrupt_analog(self):
        gd = _game(Path(tempfile.mkdtemp()))
        P.sidecar_path(gd).write_text('{"version":2,"priority":[],"pads":{},"analog":"oops"}')
        self.assertEqual(P.load(gd), {})        # corrupt analog -> clean no-op, not a launch crash

    def test_load_coerces_single_player_to_bool(self):
        gd = _game(Path(tempfile.mkdtemp()))
        P.sidecar_path(gd).write_text(
            '{"version":2,"priority":[],"pads":{"DS":{"BUTTON_1":"x"}},"single_player":1}')
        self.assertIs(P.load(gd)["single_player"], True)


class HandheldSlice(unittest.TestCase):
    """The per-game handheld Deck-pad override slice (On-the-go), independent of the docked pads."""

    def test_roundtrip_and_clear(self):
        gd = _game(Path(tempfile.mkdtemp()))
        P.save_handheld(gd, {"BUTTON_1": "BTN_NORTH", "BUTTON_2": "BTN_WEST"})
        self.assertEqual(P.load_handheld(gd), {"BUTTON_1": "BTN_NORTH", "BUTTON_2": "BTN_WEST"})
        P.save_handheld(gd, {})                                # empty -> cleared
        self.assertEqual(P.load_handheld(gd), {})

    def test_preserves_docked_pads(self):
        gd = _game(Path(tempfile.mkdtemp()))
        P.save(gd, {"priority": ["XARC"], "pads": {"XARC": {"BUTTON_1": "BTN_SOUTH"}}})
        P.save_handheld(gd, {"BUTTON_1": "BTN_EAST"})
        data = P.load(gd)
        self.assertEqual(data["pads"], {"XARC": {"BUTTON_1": "BTN_SOUTH"}})   # docked untouched
        self.assertEqual(P.load_handheld(gd), {"BUTTON_1": "BTN_EAST"})

    def test_load_filters_unknown_and_empty(self):
        gd = _game(Path(tempfile.mkdtemp()))
        P.sidecar_path(gd).write_text(
            '{"version":2,"priority":[],"pads":{},'
            '"handheld":{"BUTTON_1":"BTN_EAST","BOGUS":"x","BUTTON_2":""}}')
        self.assertEqual(P.load_handheld(gd), {"BUTTON_1": "BTN_EAST"})   # BOGUS key + empty val dropped


class MaterializeHandheldDefault(unittest.TestCase):
    """materialize_handheld_default overlays the per-game override on DEFAULT_DECK_MAP (PLAYER_1)."""

    def _patched(self, gun=False):
        return (mock.patch.object(P, "_handheld", lambda: True),
                mock.patch.object(P, "is_gun_game", lambda g: gun),
                mock.patch.object(P, "_deck_pad_tag", lambda: "DECK"))

    def test_override_overlays_default(self):
        gd = _game(Path(tempfile.mkdtemp()))
        ini = P.ini_of(gd)
        P.save_handheld(gd, {"BUTTON_1": "BTN_NORTH"})        # override A(BTN_SOUTH) -> X(BTN_NORTH)
        a, b, c = self._patched()
        with a, b, c:
            res = P.materialize_handheld_default(gd, 2)
        self.assertTrue(res["applied"])
        txt = ini.read_text()
        self.assertIn('PLAYER_1_BUTTON_1 = "DECK_BTN_NORTH"', txt)     # override wins
        self.assertIn('PLAYER_1_BUTTON_2 = "DECK_BTN_EAST"', txt)      # unset control keeps the default
        self.assertIn('PLAYER_2_BUTTON_1 = "OLD2"', txt)              # docked P2 left canonical
        self.assertTrue(P.restore(gd))                                # reverted on game-end
        self.assertIn('PLAYER_1_BUTTON_1 = "OLD1"', ini.read_text())

    def test_no_override_is_pure_default(self):
        gd = _game(Path(tempfile.mkdtemp()))
        ini = P.ini_of(gd)
        a, b, c = self._patched()
        with a, b, c:
            self.assertTrue(P.materialize_handheld_default(gd, 2)["applied"])
        self.assertIn('PLAYER_1_BUTTON_1 = "DECK_BTN_SOUTH"', ini.read_text())   # DEFAULT_DECK_MAP


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

    def test_legacy_flagless_does_not_blank_p2_at_launch(self):
        # a pre-rework sidecar (no single_player flag, slot-agnostic keys) for a single-driver game:
        # the LAUNCH path must NOT blank the canonical PLAYER_2 gear before the page heals it
        gd = _game(Path(tempfile.mkdtemp()),
                   '[EVDEV]\nPLAYER_1_BUTTON_1 = "OLD1"\nPLAYER_2_BUTTON_1 = "CANON_GEAR"\n')
        ini = P.ini_of(gd)
        P.save(gd, {"priority": ["DS"], "pads": {"DS": {"BUTTON_1": "BTN_SOUTH"}}})   # no flag
        with mock.patch.object(P, "loader_tags", self._tags("DS")):
            res = P.materialize(gd, 2)
        self.assertTrue(res["applied"])
        out = ini.read_text()
        self.assertIn('PLAYER_1_BUTTON_1 = "DS_BTN_SOUTH"', out)    # the one pad drives P1
        self.assertIn('PLAYER_2_BUTTON_1 = "CANON_GEAR"', out)      # gear NOT blanked (unknown shape)

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


ANALOG_INI = ("[EVDEV]\n"
              'ANALOGUE_1 = "OLD1"\nANALOGUE_DEADZONE_1 = 0 0 0\n'
              'ANALOGUE_2 = "OLD2"\nANALOGUE_DEADZONE_2 = 0 0 0\n'
              'ANALOGUE_3 = "OLD3"\nANALOGUE_DEADZONE_3 = 0 0 0\n')
# 1P driving layout (Harley-style non-contiguous channels: wheel=2, gas=1, brake=4).
DRIVE_ANALOG = [{"fn": "ANALOG_1", "p1": 2, "p2": None},
                {"fn": "ANALOG_2", "p1": 1, "p2": None},
                {"fn": "ANALOG_3", "p1": 4, "p2": None}]
DRIVE_PADS = {"WHEEL": {"ANALOG_1": "ABS_X", "ANALOG_2": "ABS_RZ", "ANALOG_3": "ABS_Z"}}
# 2P Hummer layout (P1=1/2/3, P2=5/6/7).
HUMMER_ANALOG = [{"fn": "ANALOG_1", "p1": 1, "p2": 5},
                 {"fn": "ANALOG_2", "p1": 2, "p2": 6},
                 {"fn": "ANALOG_3", "p1": 3, "p2": 7}]


class RenderIniAnalog(unittest.TestCase):
    def test_one_player_writes_channels_and_deadzones(self):
        out = P.render_ini(ANALOG_INI, {1: "WHEEL"}, DRIVE_PADS, 2, DRIVE_ANALOG)
        self.assertIn('ANALOGUE_2 = "WHEEL_ABS_X"', out)    # ANALOG_1 -> p1 channel 2
        self.assertIn('ANALOGUE_1 = "WHEEL_ABS_RZ"', out)   # ANALOG_2 -> channel 1
        self.assertIn('ANALOGUE_4 = "WHEEL_ABS_Z"', out)    # ANALOG_3 -> channel 4 (non-contiguous)
        self.assertIn("ANALOGUE_DEADZONE_4 = 0 0 0", out)   # new channel gets a neutral deadzone
        self.assertEqual(out.count("ANALOGUE_DEADZONE_1 = 0 0 0"), 1)  # existing one not duplicated

    def test_two_player_hummer_channels(self):
        pads = {"P1": dict(DRIVE_PADS["WHEEL"]), "P2": dict(DRIVE_PADS["WHEEL"])}
        out = P.render_ini("[EVDEV]\nPLAYER_1_COIN = \"\"\n", {1: "P1", 2: "P2"}, pads, 2, HUMMER_ANALOG)
        for ch in (1, 2, 3):
            self.assertIn(f'ANALOGUE_{ch} = "P1_', out)
        for ch in (5, 6, 7):
            self.assertIn(f'ANALOGUE_{ch} = "P2_', out)
            self.assertIn(f"ANALOGUE_DEADZONE_{ch} = 0 0 0", out)

    def test_unassigned_slot_blanks_its_channels(self):
        # Hummer with only P1 connected -> P2 channels 5/6/7 blanked (not left stale)
        out = P.render_ini(ANALOG_INI, {1: "WHEEL"}, DRIVE_PADS, 2, HUMMER_ANALOG)
        self.assertIn('ANALOGUE_1 = "WHEEL_ABS_X"', out)
        self.assertIn('ANALOGUE_5 = ""', out)

    def test_optin_digital_only_leaves_analog_untouched(self):
        # a pad with only digital controls must NOT disturb the canonical wheel/pedals
        out = P.render_ini(ANALOG_INI, {1: "BTN"}, {"BTN": {"BUTTON_1": "BTN_SOUTH"}}, 2, DRIVE_ANALOG)
        self.assertIn('ANALOGUE_1 = "OLD1"', out)
        self.assertIn('ANALOGUE_2 = "OLD2"', out)

    def test_no_analog_layout_is_digital_only(self):
        out = P.render_ini(ANALOG_INI, {1: "WHEEL"}, DRIVE_PADS, 2, None)
        self.assertIn('ANALOGUE_1 = "OLD1"', out)            # analog untouched without a layout


class MaterializeAnalog(unittest.TestCase):
    def _tags(self, *tags):
        return lambda: [{"path": f"/d{i}", "name": t, "tag": t} for i, t in enumerate(tags)]

    def test_materialize_then_restore_reverts_analog(self):
        gd = _game(Path(tempfile.mkdtemp()), ANALOG_INI)
        ini = P.ini_of(gd)
        P.save(gd, {"priority": ["WHEEL"], "pads": DRIVE_PADS, "analog": DRIVE_ANALOG})
        with mock.patch.object(P, "loader_tags", self._tags("WHEEL")):
            res = P.materialize(gd, 2)
        self.assertTrue(res["applied"])
        self.assertIn('ANALOGUE_2 = "WHEEL_ABS_X"', ini.read_text())
        self.assertTrue(P.restore(gd))
        self.assertIn('ANALOGUE_2 = "OLD2"', ini.read_text())   # canonical analog restored

    def test_missed_restore_does_not_leave_stale_analog(self):
        # 2-human path, canonical-base regression: a wheel pad materializes ANALOGUE; a 2nd launch
        # with the restore MISSED (crash) and only a DIGITAL pad connected must revert the channel to
        # canonical, not keep the stale wheel binding.
        gd = _game(Path(tempfile.mkdtemp()), ANALOG_INI)
        ini = P.ini_of(gd)
        P.save(gd, {"priority": ["WHEEL", "BTN"],
                    "pads": {"WHEEL": dict(DRIVE_PADS["WHEEL"]), "BTN": {"BUTTON_1": "BTN_SOUTH"}},
                    "analog": DRIVE_ANALOG})
        with mock.patch.object(P, "loader_tags", self._tags("WHEEL")):
            P.materialize(gd, 2)
        self.assertIn('ANALOGUE_2 = "WHEEL_ABS_X"', ini.read_text())
        with mock.patch.object(P, "loader_tags", self._tags("BTN")):  # restore missed; relaunch
            P.materialize(gd, 2)
        self.assertIn('ANALOGUE_2 = "OLD2"', ini.read_text())         # reverted, not stale


class RenderIniSingle(unittest.TestCase):
    PAD = {"PLAYER_1_BUTTON_1": "BTN_SOUTH", "PLAYER_2_BUTTON_1": "BTN_EAST", "ANALOG_1": "ABS_X"}
    INI = ('[EVDEV]\nPLAYER_1_BUTTON_1 = "CANON1"\nPLAYER_2_BUTTON_1 = "CANON_GEAR"\n'
           'PLAYER_1_BUTTON_UP = "CANONUP"\nANALOGUE_1 = "CANONW"\nANALOGUE_DEADZONE_1 = 0 0 0\n')

    def test_writes_both_jvs_slots_no_blank(self):
        out = P.render_ini_single(self.INI, "DS", self.PAD, [{"fn": "ANALOG_1", "p1": 1, "p2": None}])
        self.assertIn('PLAYER_1_BUTTON_1 = "DS_BTN_SOUTH"', out)
        self.assertIn('PLAYER_2_BUTTON_1 = "DS_BTN_EAST"', out)   # the gear, bound from the SAME pad
        self.assertIn('PLAYER_1_BUTTON_UP = "CANONUP"', out)      # unmapped -> canonical, NOT blanked
        self.assertIn('ANALOGUE_1 = "DS_ABS_X"', out)

    def test_no_evdev_returns_none(self):
        self.assertIsNone(P.render_ini_single("[Display]\nW=1\n", "DS", self.PAD, []))


class MaterializeSinglePlayer(unittest.TestCase):
    def _tags(self, *tags):
        return lambda: [{"path": f"/d{i}", "name": t, "tag": t} for i, t in enumerate(tags)]

    INI = ('[EVDEV]\nPLAYER_1_BUTTON_1 = "CANON"\nPLAYER_2_BUTTON_1 = "CANON_GEAR"\n'
           'ANALOGUE_1 = "CANONW"\nANALOGUE_DEADZONE_1 = 0 0 0\n')

    def _save(self, gd):
        P.save(gd, {"single_player": True, "priority": ["DS"],
                    "pads": {"DS": {"PLAYER_1_BUTTON_1": "BTN_SOUTH", "PLAYER_2_BUTTON_1": "BTN_EAST",
                                    "ANALOG_1": "ABS_X"}},
                    "analog": [{"fn": "ANALOG_1", "p1": 1, "p2": None}]})

    def test_one_pad_drives_both_slots_and_restore(self):
        gd = _game(Path(tempfile.mkdtemp()), self.INI)
        ini = P.ini_of(gd)
        self._save(gd)
        with mock.patch.object(P, "loader_tags", self._tags("DS")):
            res = P.materialize(gd, 2)
        self.assertTrue(res["applied"])
        self.assertEqual(res["slots"], {"1": "DS"})
        out = ini.read_text()
        self.assertIn('PLAYER_1_BUTTON_1 = "DS_BTN_SOUTH"', out)
        self.assertIn('PLAYER_2_BUTTON_1 = "DS_BTN_EAST"', out)   # gear on P2, NOT blanked
        self.assertIn('ANALOGUE_1 = "DS_ABS_X"', out)
        self.assertTrue(P.restore(gd))
        self.assertIn('PLAYER_2_BUTTON_1 = "CANON_GEAR"', ini.read_text())

    def test_noop_when_chosen_pad_absent(self):
        gd = _game(Path(tempfile.mkdtemp()), self.INI)
        ini = P.ini_of(gd)
        self._save(gd)
        before = ini.read_text()
        with mock.patch.object(P, "loader_tags", self._tags("SOMETHING_ELSE")):
            res = P.materialize(gd, 2)
        self.assertFalse(res["applied"])
        self.assertEqual(ini.read_text(), before)


class HandheldDefault(unittest.TestCase):   # WS-D: auto-default Deck-pad map, handheld-only
    def _game(self):
        d = Path(tempfile.mkdtemp())
        g = d / "vf5"; g.mkdir()
        (g / "vf5.elf").write_bytes(b"\x7fELF" + b"\x00" * 200)   # not a real ELF -> crc None -> non-gun
        (g / "vf5.commands").write_text("vf5.elf\n")
        ini = g / "lindbergh.ini"
        ini.write_text("[General]\nX=1\n[EVDEV]\nINPUT_MODE = 2\nPLAYER_1_BUTTON_1 = \"OLD\"\n")
        return g, ini

    def _apply(self, g, *, handheld=True, gun=False, tag="MICROSOFT_X_BOX_360_PAD_0"):
        with mock.patch.object(P, "_handheld", lambda: handheld), \
             mock.patch.object(P, "is_gun_game", lambda gd: gun), \
             mock.patch.object(P, "_deck_pad_tag", lambda: tag):
            return P.materialize_handheld_default(g)

    def test_injects_default_with_deck_landmines(self):
        g, ini = self._game()
        res = self._apply(g)
        self.assertTrue(res["applied"])
        t = ini.read_text()
        self.assertIn('PLAYER_1_BUTTON_1 = "MICROSOFT_X_BOX_360_PAD_0_BTN_SOUTH"', t)
        self.assertIn('PLAYER_1_BUTTON_7 = "MICROSOFT_X_BOX_360_PAD_0_ABS_Z"', t)     # bare axis, not _MAX
        self.assertIn('PLAYER_1_BUTTON_UP = "MICROSOFT_X_BOX_360_PAD_0_ABS_HAT0Y_MIN"', t)  # hat
        self.assertTrue((g / "lindbergh.ini.mad-restore").exists())

    def test_restore_reverts_docked_safe(self):
        g, ini = self._game()
        before = ini.read_text()
        self._apply(g)
        self.assertNotEqual(ini.read_text(), before)
        self.assertTrue(P.restore(g))
        self.assertEqual(ini.read_text(), before)   # canonical [EVDEV] back, docked config untouched

    def test_gates(self):
        for kw in (dict(handheld=False), dict(gun=True), dict(tag=None)):
            g, ini = self._game()
            before = ini.read_text()
            self.assertFalse(self._apply(g, **kw)["applied"])
            self.assertEqual(ini.read_text(), before)                 # no-op leaves the ini untouched
            self.assertFalse((g / "lindbergh.ini.mad-restore").exists())

    def test_apply_cli_heals_orphan_when_docked(self):
        # ES-DE death leaves the Deck map in [EVDEV] + a .mad-restore orphan. A later DOCKED launch
        # (no config) must heal at GAME-START via the apply CLI, not run a session on the stale map.
        import json as _json
        g, ini = self._game()
        self._apply(g)                                          # handheld default -> deck map + orphan
        self.assertTrue((g / "lindbergh.ini.mad-restore").exists())
        deck_map = ini.read_text()
        with mock.patch.object(P, "_handheld", lambda: False):  # now docked, no configured pad
            P._main(["prog", "apply", str(g)])
        self.assertNotEqual(ini.read_text(), deck_map)          # [EVDEV] reverted to canonical
        self.assertFalse((g / "lindbergh.ini.mad-restore").exists())   # restore consumed the orphan

    def test_default_map_keys_match_controls(self):
        self.assertEqual(set(P.DEFAULT_DECK_MAP), set(P.CONTROLS))   # every JVS control has a default

    def test_connected_pads_re_admits_deck(self):
        from types import SimpleNamespace as NS
        deck = NS(path="/dev/input/event10", phys="", is_steam_virtual=True)   # the 11ff Deck pad
        xarc = NS(path="/dev/input/event6", phys="", is_steam_virtual=False)
        tags = [{"path": "/dev/input/event10", "name": "Microsoft X-Box 360 pad 0",
                 "tag": "MICROSOFT_X_BOX_360_PAD_0"},
                {"path": "/dev/input/event6", "name": "X-Arcade", "tag": "XARCADE"}]
        with mock.patch.object(P, "loader_tags", lambda: tags), \
             mock.patch("lib.devices.enumerate_devices", lambda: [deck, xarc]), \
             mock.patch("lib.devices.joypads", lambda devs: [xarc]), \
             mock.patch("lib.devices.port_of", lambda phys: ""), \
             mock.patch("lib.pad_labels.device_label", lambda d, xp: "X-Arcade"), \
             mock.patch("lib.routing.xarcade_port", lambda pol: ""), \
             mock.patch("lib.policy.load_merged", lambda: {}):
            labels = {p["label"]: p["tag"] for p in P.connected_pads()}
        self.assertEqual(labels.get("Steam Deck"), "MICROSOFT_X_BOX_360_PAD_0")  # 11ff re-admitted
        self.assertIn("X-Arcade", labels)                                        # normal pads intact

    def test_crc_parity_with_rpc_module(self):
        # the self-contained _region_crc copy must never drift from lindbergh_cmds._region_crc
        from lib.madsrv import lindbergh_cmds as C
        elf = Path(tempfile.mkdtemp()) / "g.elf"
        elf.write_bytes(b"\x7fELF\x01" + b"\x00" * (0x34 + 3 * 0x20) + b"\x11" * 0x8000)
        self.assertEqual(P._region_crc(elf), C._region_crc(elf))


if __name__ == "__main__":
    unittest.main()
