"""Tests for the pcsx2x6 (Namco 246/256) standalone onboarding.

pcsx2x6 reuses the (already golden-tested) pcsx2_cfg writer, pointed at its PORTABLE
ini. These tests lock in the pcsx2x6-SPECIFIC invariants:
  • it offers all three standalone sections (Settings / Input mapping / Controllers),
  • it is NON-transient (unlike pcsx2) and its launch target is the portable ini,
  • binding a pad NEVER disturbs the guncon2 ([USB1/2]) or [JVS] regions (gun-safety),
  • a DualSense binds to a clean DualShock2 SDL block over the keyboard resting [Pad1].

Run:  python3 -m unittest tests.test_pcsx2x6 -v
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from lib import inifile, pcsx2_cfg, switch_bind
from lib.madsrv import pads_cmds, rpc, standalones_cmds
from lib.madsrv import pcsx2x6_cmds, pcsx2x6_input_cmds  # noqa: F401  (register RPCs)

FIX = Path(__file__).parent / "fixtures" / "pcsx2x6" / "PCSX2.ini"
DS5 = "054c:0ce6"   # DualSense
PORTABLE = "Applications/pcsx2x6/PCSX2x6/inis/PCSX2.ini"
GUN_SECTIONS = ("USB1", "USB2", "JVS")


def _dev(index):
    from types import SimpleNamespace
    return SimpleNamespace(index=index, vidpid=DS5, name="DualSense", guid="g")


class Wiring(unittest.TestCase):
    def test_three_sections(self):
        entry = next(s for s in standalones_cmds.STANDALONES if s["key"] == "pcsx2x6")
        kinds = [s["kind"] for s in standalones_cmds._sections_for(entry)]
        self.assertEqual(kinds, ["settings", "input_map", "pads_map"])

    def test_rpcs_registered(self):
        for m in ("pcsx2x6.get", "pcsx2x6.set", "pcsx2x6.input_get", "pcsx2x6.input_set"):
            self.assertIn(m, rpc._METHODS, m)

    def test_in_pads_registry(self):
        # System 246/256 games are 1-2 players, so the Controllers page shows 2 slots.
        self.assertEqual(pads_cmds._EMUS["pcsx2x6"]["players"], 2)

    def test_handheld_class_is_deck(self):
        # Deck pad is a fallback only — so a DualSense takes P1 and the Deck pad
        # doesn't steal a slot.
        self.assertEqual(pads_cmds._handheld_class("pcsx2x6"), "28de:1205")


class BindWiring(unittest.TestCase):
    def test_non_transient(self):
        # The key difference from regular pcsx2: pcsx2x6 is ES-DE-only, so it persists
        # its bind (no snapshot/restore). Persisting keeps a real DS2 [Pad1] that the
        # Input-mapping page can edit.
        self.assertNotIn("pcsx2x6", switch_bind._TRANSIENT)
        self.assertIn("pcsx2", switch_bind._TRANSIENT)

    def test_managed_players(self):
        self.assertEqual(switch_bind._PLAYERS["pcsx2x6"], 2)

    def test_target_is_portable_ini(self):
        t = switch_bind._target("pcsx2x6", "NM00003.acgame")
        self.assertTrue(str(t).endswith(PORTABLE), t)


class GunSafety(unittest.TestCase):
    """Binding a pad must leave the guncon2 + JVS regions byte-identical."""

    def _bind(self, players):
        d = tempfile.mkdtemp()
        ini = Path(d) / "PCSX2.ini"
        shutil.copy2(FIX, ini)
        before = {s: inifile.section_body(ini.read_text(), s) for s in GUN_SECTIONS}
        pcsx2_cfg.assign_devices(players, ini_path=str(ini), manage=8)
        text = ini.read_text()
        after = {s: inifile.section_body(text, s) for s in GUN_SECTIONS}
        return before, after, text

    def test_single_dualsense_baked_ds2_and_guns_untouched(self):
        before, after, text = self._bind([_dev(0)])
        pad1 = inifile.section_body(text, "Pad1")
        self.assertIn("Type = DualShock2", pad1)
        self.assertIn("Cross = SDL-0/FaceSouth", pad1)
        self.assertNotIn("Keyboard/", pad1)                 # keyboard resting block replaced
        self.assertEqual(before, after)                     # [USB1]/[USB2]/[JVS] unchanged

    def test_two_players_p1_p2_no_multitap_guns_untouched(self):
        # pcsx2x6 manages 2 players (P1/P2) only — Pad1 + Pad2, no multitap.
        before, after, text = self._bind([_dev(0), _dev(1)])
        self.assertIn("Cross = SDL-0/FaceSouth", inifile.section_body(text, "Pad1"))
        self.assertIn("Cross = SDL-1/FaceSouth", inifile.section_body(text, "Pad2"))
        self.assertIn("MultitapPort1 = false", inifile.section_body(text, "Pad"))
        self.assertEqual(inifile.section_body(text, "Pad3").strip(), "Type = None")
        self.assertEqual(before, after)


class InputMap(unittest.TestCase):
    def test_remap_after_bind_changes_one_source(self):
        d = tempfile.mkdtemp()
        ini = Path(d) / "PCSX2.ini"
        shutil.copy2(FIX, ini)
        pcsx2_cfg.assign_devices([_dev(0)], ini_path=str(ini), manage=8)  # DS2 [Pad1]
        guns0 = {s: inifile.section_body(ini.read_text(), s) for s in GUN_SECTIONS}
        inp = pcsx2x6_input_cmds
        orig_ini, orig_run = inp._INI, inp._running
        try:
            inp._INI = ini
            inp._running = lambda: False
            inp._input_set({"id": "Cross", "kind": "btn", "value": 0x131})  # BTN_EAST
        finally:
            inp._INI, inp._running = orig_ini, orig_run
        text = ini.read_text()
        self.assertIn("Cross = SDL-0/FaceEast", inifile.section_body(text, "Pad1"))
        # guns still untouched by an input remap
        self.assertEqual(guns0, {s: inifile.section_body(text, s) for s in GUN_SECTIONS})

    def test_remap_before_bind_refused_no_halfwrite(self):
        # Pre-launch [Pad1] is keyboard-bound (no SDL). A remap must be refused
        # UNIFORMLY (not half-write SDL over the keyboard defaults).
        d = tempfile.mkdtemp()
        ini = Path(d) / "PCSX2.ini"
        shutil.copy2(FIX, ini)
        inp = pcsx2x6_input_cmds
        orig_ini, orig_run = inp._INI, inp._running
        try:
            inp._INI = ini
            inp._running = lambda: False
            with self.assertRaises(Exception):
                inp._input_set({"id": "Cross", "kind": "btn", "value": 0x130})
        finally:
            inp._INI, inp._running = orig_ini, orig_run
        # the keyboard binding is untouched (no half-write happened)
        self.assertIn("Cross = Keyboard/X", inifile.section_body(ini.read_text(), "Pad1"))


if __name__ == "__main__":
    unittest.main()
