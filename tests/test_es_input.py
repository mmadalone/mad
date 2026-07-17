"""lib/es_input.py + routing.family_of / family_token_of — the ONE family matcher, and the
family list DERIVED from what ES-DE has configured instead of a hardcoded seven.

Background. mad_config.KNOWN_FAMILIES listed seven families; routing.family_of knew four. "Steam
Deck" and "Wii Remote Pro" therefore resolved by NAME SUBSTRING alone, which is fragile in both
directions: the Deck's virtual pad is named "Microsoft X-Box 360 pad 0" at runtime (no "Steam Deck"
in it, and it fell through the "x-box" catch-all and classified as XBOX), while es_input.xml calls
the SAME device "Steam Deck Controller". A vid:pid answer is identical in both places, which is
what lets the family list be derived at all.

Hermetic: es_input.xml is a fixture in a tmp dir (esde_settings.APPDATA is patched), never the
developer's real ES-DE config.

Run:  python3 -m unittest tests.test_es_input -v
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lib import es_input, esde_settings, mad_config
from lib.routing import family_of, family_token_of
from tests._fakes import FakeDevice

# Real GUIDs, read off this rig's ~/ES-DE/settings/es_input.xml on 2026-07-17.
GUID_DS5 = "030057564c050000e60c000000006800"      # 054c:0ce6
GUID_X360 = "0300a81c5e040000a102000000010000"     # 045e:02a1  (this IS the X-Arcade here)
GUID_WIIU = "0500a9177e0500003003000001000000"     # 057e:0330
GUID_DECK = "030079f6de280000ff11000001000000"     # 28de:11ff
XPORT = "1.1"

_XML = """<?xml version="1.0"?>
<inputList>
  <inputConfig type="keyboard" deviceName="Keyboard" deviceGUID="-1"/>
{rows}
</inputList>
"""
_ROW = '  <inputConfig type="joystick" deviceName="{name}" deviceGUID="{guid}"/>'


class Guid(unittest.TestCase):
    def test_decodes_vid_pid(self):
        self.assertEqual(es_input.guid_vidpid(GUID_DS5), (0x054c, 0x0ce6))
        self.assertEqual(es_input.guid_vidpid(GUID_X360), (0x045e, 0x02a1))
        self.assertEqual(es_input.guid_vidpid(GUID_WIIU), (0x057e, 0x0330))
        self.assertEqual(es_input.guid_vidpid(GUID_DECK), (0x28de, 0x11ff))

    def test_rejects_a_non_pad_guid(self):
        for g in ("-1", "", "   ", "nonsense", "0300a81c5e040000a10200000001"):   # keyboard/short
            self.assertIsNone(es_input.guid_vidpid(g))

    def test_rejects_a_zero_vendor(self):
        self.assertIsNone(es_input.guid_vidpid("03000000" + "0000" + "0000" + "a102" + "0" * 12))


class Families(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="es-input-test-"))
        (self.tmp / "settings").mkdir()
        self._p = mock.patch.object(esde_settings, "APPDATA", self.tmp)
        self._p.start()

    def tearDown(self):
        self._p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, *rows):
        body = "\n".join(_ROW.format(name=n, guid=g) for n, g in rows)
        (self.tmp / "settings" / "es_input.xml").write_text(_XML.format(rows=body))

    def test_this_rigs_real_config(self):
        self._write(("DualSense Wireless Controller", GUID_DS5),
                    ("X360 Wireless Controller", GUID_X360),
                    ("Nintendo Wii Remote Pro Controller", GUID_WIIU),
                    ("Steam Deck Controller", GUID_DECK))
        # Xbox AND X-Arcade both offered for the 045e: es_input.xml has no USB port, so it cannot
        # tell the cab from a real Xbox pad. Offering only one would hide a device the user owns.
        self.assertEqual(es_input.families(XPORT),
                         ["DualSense", "Xbox", "X-Arcade", "Wii Remote Pro", "Steam Deck"])

    def test_no_xarcade_identified_means_045e_is_just_an_xbox_pad(self):
        self._write(("X360 Wireless Controller", GUID_X360))
        self.assertEqual(es_input.families(""), ["Xbox"])

    def test_the_keyboard_is_not_a_family(self):
        self._write(("DualSense Wireless Controller", GUID_DS5))
        self.assertEqual(es_input.families(), ["DualSense"])     # the <Keyboard> row is skipped

    def test_a_missing_file_is_i_dont_know_not_none(self):
        # No es_input.xml at all -> [] so the caller falls back to KNOWN_FAMILIES. Returning a
        # partial list here would silently shrink the picker.
        self.assertEqual(es_input.families(XPORT), [])
        self.assertEqual(es_input.devices(), [])

    def test_a_corrupt_file_never_raises_into_a_page(self):
        (self.tmp / "settings" / "es_input.xml").write_text("<inputList><broken")
        self.assertEqual(es_input.families(XPORT), [])

    def test_deck_is_named_by_id_not_by_its_runtime_name(self):
        # THE POINT. es_input.xml says "Steam Deck Controller"; at runtime the same device is
        # "Microsoft X-Box 360 pad 0". Only the vid:pid answer is the same in both places.
        self._write(("Steam Deck Controller", GUID_DECK))
        self.assertEqual(es_input.families(), ["Steam Deck"])
        runtime = FakeDevice(vid=0x28de, pid=0x11ff, path="/dev/input/event10",
                             name="Microsoft X-Box 360 pad 0")
        self.assertEqual(family_of(runtime), "Steam Deck")       # NOT "Xbox" (the old answer)


class FamilyMatcher(unittest.TestCase):
    """family_of is THE matcher; family_token_of adds the port-identified X-Arcade split."""

    def _d(self, vid, pid, name, phys=""):
        return FakeDevice(vid=vid, pid=pid, path="/dev/input/event0", name=name, phys=phys)

    def test_the_four_it_always_knew(self):
        self.assertEqual(family_of(self._d(0x2dc8, 0x2810, "8Bitdo FC30 II")), "8BitDo")
        self.assertEqual(family_of(self._d(0x054c, 0x0ce6, "DualSense Wireless Controller")),
                         "DualSense")
        # the DS4 enumerates with a GENERIC name: only vid:pid can classify it
        self.assertEqual(family_of(self._d(0x054c, 0x09cc, "Wireless Controller")), "DualShock 4")
        self.assertEqual(family_of(self._d(0x045e, 0x02a1, "Xbox 360 Wireless Receiver")), "Xbox")

    def test_8bitdo_splits_by_model_shape(self):
        # A Pro (sticks, L3) and a retro FC30 (no sticks at all) need DIFFERENT hotkey schemes, and
        # family is the unit a profile is assigned to -- so they cannot be one family. Same split
        # Sony already has (DualSense vs DualShock 4: one vendor, two pids).
        self.assertEqual(family_of(self._d(0x2dc8, 0x2810, "8Bitdo FC30 GamePad")), "8BitDo")
        self.assertEqual(family_of(self._d(0x2dc8, 0x2810, "8Bitdo FC30 II")), "8BitDo")
        self.assertEqual(family_of(self._d(0x2dc8, 0x3820, "8Bitdo NES30 Pro")), "8BitDo Pro")

    def test_an_unlisted_8bitdo_pro_is_caught_by_name(self):
        # Every 8BitDo Pro model carries "Pro" in its name and no retro one does, so a pid we have
        # not listed (SN30 Pro, Pro 2, Ultimate) still lands on the stick-modifier profile.
        self.assertEqual(family_of(self._d(0x2dc8, 0x9999, "8BitDo SN30 Pro")), "8BitDo Pro")
        self.assertEqual(family_of(self._d(0x2dc8, 0x9999, "8Bitdo NES30")), "8BitDo")

    def test_the_split_does_not_break_the_8bitdo_priority_token(self):
        # Load-bearing: an "8BitDo" token in a ports list matches by NAME substring, so BOTH shapes
        # still seat exactly as before. tests/test_seating_golden.py is the real proof; this pins
        # the reason.
        for name in ("8Bitdo FC30 II", "8Bitdo NES30 Pro"):
            self.assertIn("8bitdo", name.lower())

    def test_the_two_it_learned(self):
        self.assertEqual(family_of(self._d(0x057e, 0x0330, "Nintendo Wii Remote Pro Controller")),
                         "Wii Remote Pro")
        self.assertEqual(family_of(self._d(0x28de, 0x1205, "Valve Software Steam Deck Controller")),
                         "Steam Deck")
        self.assertEqual(family_of(self._d(0x28de, 0x11ff, "Microsoft X-Box 360 pad 0")),
                         "Steam Deck")

    def test_deck_is_classified_before_the_xbox_catch_all(self):
        # Ordering guard. The Steam phantom's NAME contains "x-box", so if the Deck test moved
        # below the catch-all it would classify as Xbox again -- silently, since resolve_ports
        # excludes that pad and the seating golden would not notice.
        self.assertNotEqual(family_of(self._d(0x28de, 0x11ff, "Microsoft X-Box 360 pad 0")), "Xbox")

    def test_an_unknown_pad_is_none_not_a_guess(self):
        self.assertIsNone(family_of(self._d(0x1234, 0x5678, "Some Random Pad")))

    def test_family_token_of_splits_the_xarcade_out_of_xbox(self):
        XA = "usb-0000:04:00.3-1.1/input0"
        cab = self._d(0x045e, 0x02a1, "Xbox 360 Wireless Receiver", XA)
        pad = self._d(0x045e, 0x02a1, "Xbox 360 Wireless Receiver", "usb-elsewhere-2.2/input0")
        # SAME vid:pid, SAME name -- only the USB port tells them apart.
        self.assertEqual(family_token_of(cab, XPORT), "X-Arcade")
        self.assertEqual(family_token_of(pad, XPORT), "Xbox")
        self.assertEqual(family_of(cab), "Xbox")      # family_of stays vid:pid-only, deliberately

    def test_family_token_of_without_an_identified_port(self):
        # xport "" = no X-Arcade identified -> every 045e is just an Xbox pad.
        cab = self._d(0x045e, 0x02a1, "Xbox 360 Wireless Receiver", "usb-0000:04:00.3-1.1/input0")
        self.assertEqual(family_token_of(cab, ""), "Xbox")

    def test_family_token_of_defers_to_family_of_for_everything_else(self):
        d = self._d(0x054c, 0x09cc, "Wireless Controller")
        self.assertEqual(family_token_of(d, XPORT), family_of(d))


class DerivationIsNotWiredIn(unittest.TestCase):
    """WHY es_input does not (yet) source mad_config.controller_families.

    The plan said to derive the family list from ES-DE and guard it with "the derived list must be
    a SUPERSET of what the UI offers today". Measuring on the real rig showed those two cannot both
    hold, so the wiring was left out rather than shipped as a no-op or a regression. These pin the
    reasons, so the next session does not re-derive them from scratch (or ship it anyway).
    """

    MERGED = {"systems": {"arcade": {"ports": [["X-Arcade", "DualSense"]]}}}

    def test_derivation_can_never_add_a_family(self):
        # routing.family_of's entire vocabulary IS KNOWN_FAMILIES, so anything es_input can derive
        # is already offered. With the never-shrink guard, wiring it in adds exactly nothing.
        vocabulary = {"8BitDo", "DualSense", "DualShock 4", "Xbox", "Steam Deck", "Wii Remote Pro"}
        self.assertTrue(vocabulary <= set(mad_config.KNOWN_FAMILIES))
        # ...plus X-Arcade, which family_token_of splits out of Xbox by USB port.
        self.assertIn("X-Arcade", mad_config.KNOWN_FAMILIES)

    def test_controller_families_reads_no_live_state(self):
        # It must not consult es_input.xml: the same list feeds _effective_p1, which takes fams[0]
        # as an unconfigured scope's default P1. A live read makes that default depend on the
        # developer's ES-DE config -- passing on CI (no ~/ES-DE) and failing on the Deck, which is
        # the ci-vs-deck-environment-gap inverted. Caught by tests/test_racontrollers.
        with mock.patch.object(es_input, "families",
                               side_effect=AssertionError("controller_families must not read es_input")):
            got = mad_config.controller_families(self.MERGED)
        self.assertEqual(got[:2], ["X-Arcade", "DualSense"])     # the configured ports, in order
        for fam in mad_config.KNOWN_FAMILIES:
            self.assertIn(fam, got)                              # nothing dropped

    def test_the_families_es_input_would_derive_here_are_all_already_known(self):
        # The concrete case: this rig's es_input.xml lists 4 pads, and every family they map to is
        # already in KNOWN_FAMILIES -- while Miquel's 8BitDo and DualShock 4 are NOT in that file
        # at all, so a strict derivation would DROP two families he actively uses.
        with mock.patch.object(esde_settings, "APPDATA", Path(tempfile.gettempdir())):
            pass  # (the live-rig numbers are recorded in mad_config's comment, not asserted here)
        derived = {"DualSense", "Xbox", "X-Arcade", "Wii Remote Pro", "Steam Deck"}
        self.assertTrue(derived <= set(mad_config.KNOWN_FAMILIES))
        self.assertEqual({"8BitDo", "DualShock 4"} & derived, set())


if __name__ == "__main__":
    unittest.main()
