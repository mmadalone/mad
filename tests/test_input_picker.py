"""Tests for the per-player input-map PICKER added to PCSX2 + xemu, and the dynamic
Switch tile collapse. Pure temp-copy / given-text; no hardware.

Run:  python3 -m unittest tests.test_input_picker -v
"""
from __future__ import annotations

import shutil
import tempfile
import tomllib
import unittest
from pathlib import Path

from lib import pcsx2_cfg
from lib.madsrv import pcsx2_input_cmds as p
from lib.madsrv import standalones_cmds as st
from lib.madsrv import xemu_input_cmds as x
from lib.madsrv.rpc import RpcError
from tests._fakes import patch_sdl, sd

DS5 = "054c:0ce6"

_PCSX2_INI = (
    "[Pad1]\nType = DualShock2\nCross = SDL-0/FaceSouth\nCircle = SDL-0/FaceEast\n\n"
    "[Pad2]\nType = DualShock2\nCross = SDL-1/FaceSouth\nCircle = SDL-1/FaceEast\n\n"
    "[Pad3]\nType = None\n"
)

GA = "0300aaaa0000000000000000000000aa"
GB = "0300bbbb0000000000000000000000bb"
_XEMU_TOML = (
    "[input.bindings]\n"
    f"port1 = '{GA}'\n"
    f"port2 = '{GB}'\n\n"
    "[input]\n"
    f"gamepad_mappings = [\n    {{ gamepad_id = '{GA}' }},\n    ]\n"
)


class Pcsx2Picker(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.ini = self.d / "PCSX2.ini"
        self.ini.write_text(_PCSX2_INI, encoding="utf-8")
        self._ini, p._INI = p._INI, self.ini
        self._run, p._running = p._running, lambda: False
        self._cp, p._configured_pad = p._configured_pad, lambda text, section: ""

    def tearDown(self):
        p._INI, p._running, p._configured_pad = self._ini, self._run, self._cp
        shutil.rmtree(self.d, ignore_errors=True)

    def test_player_sections_in_player_order(self):
        self.assertEqual(p._player_sections(_PCSX2_INI), ["Pad1", "Pad2"])

    def test_resolve_player_clamps(self):
        secs = ["Pad1", "Pad2"]
        self.assertEqual(p._resolve_player({"player": ""}, secs), ("1", "Pad1"))
        self.assertEqual(p._resolve_player({"player": "2"}, secs), ("2", "Pad2"))
        self.assertEqual(p._resolve_player({"player": "9"}, secs), ("2", "Pad2"))

    def test_input_get_two_players(self):
        res = p._input_get({"player": ""})
        self.assertEqual([pl["label"] for pl in res["players"]], ["Player 1", "Player 2"])
        self.assertEqual(res["player"], "1")

    def test_input_set_targets_selected_player(self):
        p._input_set({"id": "Cross", "kind": "btn", "value": str(0x134), "player": "2"})  # West
        text = self.ini.read_text(encoding="utf-8")
        # Pad2's Cross re-sourced to FaceWest; Pad1 untouched.
        self.assertIn("Cross = SDL-1/FaceWest", text)
        self.assertIn("Cross = SDL-0/FaceSouth", text)


class Pcsx2BinderPreserve(unittest.TestCase):
    """The launch binder (pcsx2_cfg.assign_devices) must keep each slot's OWN button
    sources so a Player-2+ remap actually applies in-game — not clone [Pad1] over every
    slot. Regression test for the high-sev review finding."""

    def _ini(self, p1_cross, p2_cross):
        return (f"[Pad1]\nType = DualShock2\nCross = SDL-0/{p1_cross}\n"
                f"Circle = SDL-0/FaceEast\n\n"
                f"[Pad2]\nType = DualShock2\nCross = SDL-1/{p2_cross}\n"
                f"Circle = SDL-1/FaceEast\n")

    def test_player2_remap_survives_bind(self):
        with tempfile.TemporaryDirectory() as d:
            ini = Path(d) / "PCSX2.ini"
            # Player 1 = FaceSouth (default), Player 2 REMAPPED to FaceWest.
            ini.write_text(self._ini("FaceSouth", "FaceWest"), encoding="utf-8")
            players = [sd(0, DS5, "gA", "DualSense"), sd(1, DS5, "gB", "DualSense")]
            with patch_sdl(players):
                pcsx2_cfg.assign_devices(players, ini_path=str(ini), manage=8)
            text = ini.read_text(encoding="utf-8")
            self.assertIn("Cross = SDL-1/FaceWest", text)    # Player 2 remap preserved in-game
            self.assertIn("Cross = SDL-0/FaceSouth", text)   # Player 1 untouched

    def test_unconfigured_slot_falls_back_to_pad1(self):
        with tempfile.TemporaryDirectory() as d:
            ini = Path(d) / "PCSX2.ini"
            # Only [Pad1] configured; [Pad2] absent → Player 2 clones Pad1's layout.
            ini.write_text("[Pad1]\nType = DualShock2\nCross = SDL-0/FaceSouth\n"
                           "Circle = SDL-0/FaceEast\n", encoding="utf-8")
            players = [sd(0, DS5, "gA", "DualSense"), sd(1, DS5, "gB", "DualSense")]
            with patch_sdl(players):
                pcsx2_cfg.assign_devices(players, ini_path=str(ini), manage=8)
            text = ini.read_text(encoding="utf-8")
            self.assertIn("Cross = SDL-1/FaceSouth", text)   # Pad2 cloned from Pad1


class XemuPicker(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.toml = self.d / "xemu.toml"
        self.toml.write_text(_XEMU_TOML, encoding="utf-8")
        self._file, x._FILE = x._FILE, self.toml
        self._run, x.proc_guard.emulator_running = x.proc_guard.emulator_running, lambda n: False
        self._sup, x._supports_remap = x._supports_remap, lambda: True

    def tearDown(self):
        x._FILE, x.proc_guard.emulator_running, x._supports_remap = self._file, self._run, self._sup
        shutil.rmtree(self.d, ignore_errors=True)

    def test_bound_ports(self):
        self.assertEqual(x._bound_ports(_XEMU_TOML), [(1, GA), (2, GB)])

    def test_players_and_target(self):
        players, pid, guid = x._players_and_target(_XEMU_TOML, {"player": "2"})
        self.assertEqual([pl["label"] for pl in players], ["Player 1", "Player 2"])
        self.assertEqual((pid, guid), ("2", GB))

    def test_players_labelled_by_real_port_when_noncontiguous(self):
        # port1 + port3 bound, port2 empty → labels are Player 1 / Player 3 (not 1/2),
        # and selecting Player 3 edits port3's GUID (not a dense renumber to "2").
        noncontig = (f"[input.bindings]\nport1 = '{GA}'\nport3 = '{GB}'\n\n"
                     f"[input]\ngamepad_mappings = [\n    {{ gamepad_id = '{GA}' }},\n    ]\n")
        players, pid, guid = x._players_and_target(noncontig, {"player": "3"})
        self.assertEqual([pl["label"] for pl in players], ["Player 1", "Player 3"])
        self.assertEqual((pid, guid), ("3", GB))

    def test_players_fallback_when_unbound(self):
        players, pid, guid = x._players_and_target("[input.bindings]\n", {})
        self.assertEqual(len(players), 1)
        self.assertEqual((pid, guid), ("1", ""))

    def test_input_set_targets_selected_port(self):
        x._input_set({"id": "a", "kind": "btn", "value": 0x131, "player": "2"})  # B → idx 1
        data = tomllib.loads(self.toml.read_text(encoding="utf-8"))
        gms = {e["gamepad_id"]: e for e in data["input"]["gamepad_mappings"]}
        self.assertEqual(gms[GB].get("controller_mapping"), {"a": 1})   # port2 pad got it
        self.assertNotIn("controller_mapping", gms[GA])                 # port1 pad untouched


class SwitchDynamicTile(unittest.TestCase):
    def setUp(self):
        # Make the Switch system "present" deterministically (a gamelist would gate it).
        self._hg, st.es_systems._has_gamelist = st.es_systems._has_gamelist, lambda s: True
        self._inst = st._emu_installed

    def tearDown(self):
        st.es_systems._has_gamelist = self._hg
        st._emu_installed = self._inst

    def _switch_tile(self):
        return next((t for t in st._standalones_list({})["tiles"] if t["key"] == "switch"), None)

    def test_both_installed_keeps_group(self):
        st._emu_installed = lambda e: True
        sw = self._switch_tile()
        self.assertIn("members", sw)
        self.assertEqual(len(sw["members"]), 2)

    def test_one_installed_collapses_to_sections(self):
        st._emu_installed = lambda e: e == "ryujinx"
        sw = self._switch_tile()
        self.assertIn("sections", sw)
        self.assertNotIn("members", sw)
        self.assertTrue(all(s.get("arg") == "ryujinx" for s in sw["sections"]))

    def test_neither_installed_drops_tile(self):
        st._emu_installed = lambda e: False
        self.assertIsNone(self._switch_tile())

    def test_unknown_member_treated_installed(self):
        self.assertTrue(self._inst("foo"))


if __name__ == "__main__":
    unittest.main()
