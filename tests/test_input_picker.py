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

from lib import inifile, pcsx2_cfg
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
        p._buf.reset()          # fresh buffer per case (module-level singleton; _INI is repointed)

    def tearDown(self):
        p._INI, p._running = self._ini, self._run
        p._buf.reset()
        shutil.rmtree(self.d, ignore_errors=True)

    def _ovr(self):
        return pcsx2_cfg.load_input_overrides(self.ini)

    def test_player_sections_in_player_order(self):
        self.assertEqual(p._player_sections(_PCSX2_INI), ["Pad1", "Pad2"])

    def test_player_clamps(self):
        self.assertEqual(p._player({"player": ""}, 2), 1)
        self.assertEqual(p._player({"player": "2"}, 2), 2)
        self.assertEqual(p._player({"player": "9"}, 2), 2)

    def test_input_get_two_players(self):
        res = p._input_get({"player": ""})
        self.assertEqual([pl["label"] for pl in res["players"]], ["Player 1", "Player 2"])
        self.assertEqual(res["player"], "1")

    def test_input_set_writes_store_not_ini(self):
        before = self.ini.read_text(encoding="utf-8")
        p._input_set({"id": "Cross", "kind": "btn", "value": str(0x134), "player": "2"})  # West
        p._input_save({})                                                # buffered: commit on save
        # the remap goes to the per-PLAYER store, NOT the [PadN] ini
        self.assertEqual(self._ovr().get(2, {}).get("Cross"), "FaceWest")
        self.assertEqual(self.ini.read_text(encoding="utf-8"), before)   # ini untouched

    # ── buffered editor: stage in memory, commit on Save, revert on Cancel ────
    def test_buffered_and_dirty_flags(self):
        r = p._input_get({"player": "1"})
        self.assertTrue(r["buffered"])
        self.assertFalse(r["dirty"])                                     # clean at rest

    def test_stage_leaves_store_unchanged_and_dirty(self):
        r = p._input_set({"id": "Cross", "kind": "btn", "value": str(0x134), "player": "2"})  # West
        self.assertTrue(r["dirty"])                                      # (b) set response reports staged
        self.assertEqual(self._ovr(), {})                               # (a) sidecar UNCHANGED after stage
        self.assertTrue(p._input_get({"player": "2"})["dirty"])          # (b) input_get reports dirty true

    def test_save_commits_once(self):
        p._input_set({"id": "Cross", "kind": "btn", "value": str(0x134), "player": "2"})
        saved = p._input_save({})
        self.assertTrue(saved["saved"])                                  # (c) a write happened
        self.assertFalse(saved["dirty"])
        self.assertEqual(self._ovr().get(2, {}).get("Cross"), "FaceWest")
        self.assertFalse(p._input_get({"player": "2"})["dirty"])
        self.assertFalse(p._input_save({})["saved"])                     # nothing more to save

    def test_cancel_reverts(self):
        p._input_set({"id": "Cross", "kind": "btn", "value": str(0x134), "player": "2"})
        p._input_cancel({})                                              # (d) discard
        self.assertFalse(p._input_get({"player": "2"})["dirty"])
        self.assertEqual(self._ovr(), {})                                # nothing written

    def test_clear_is_buffered_and_writes_baked_default(self):
        p._input_set({"id": "Cross", "kind": "btn", "value": str(0x134), "player": "2"})
        p._input_save({})
        self.assertEqual(self._ovr().get(2, {}).get("Cross"), "FaceWest")
        r = p._input_clear({"id": "Cross", "player": "2"})               # reset -> stage baked default
        self.assertTrue(r["dirty"])
        self.assertEqual(self._ovr().get(2, {}).get("Cross"), "FaceWest")  # not written yet
        p._input_save({})
        self.assertEqual(self._ovr().get(2, {}).get("Cross"), "FaceSouth")  # baked DualShock2 default

    def test_running_guard_blocks_stage_and_save(self):
        # EBUSY lives at the top of _apply, so it refuses at STAGE and at SAVE (a launch that
        # starts after a stage must not be able to commit).
        p._running = lambda: True
        with self.assertRaises(RpcError):
            p._input_set({"id": "Cross", "kind": "btn", "value": str(0x134), "player": "2"})
        p._running = lambda: False
        p._input_set({"id": "Cross", "kind": "btn", "value": str(0x134), "player": "2"})  # stage while idle
        p._running = lambda: True
        with self.assertRaises(RpcError):
            p._input_save({})                                            # refuses at save
        self.assertEqual(self._ovr(), {})                                # nothing reached disk

    def test_input_get_triggers_one_time_migration(self):
        # LANDMINE: input_get must still run migrate_overrides_from_ini inside the buffer's load,
        # so a legacy [PadN] non-default remap is seeded into the store on the page's first read.
        self.ini.write_text("[Pad1]\nType = DualShock2\nCross = SDL-0/FaceWest\n"
                            "Circle = SDL-0/FaceEast\n", encoding="utf-8")
        p._buf.reset()                                                   # force a fresh load
        p._input_get({"player": "1"})
        self.assertEqual(self._ovr().get(1, {}).get("Cross"), "FaceWest")


class Pcsx2OverrideStore(unittest.TestCase):
    """The per-player override store makes a remap FOLLOW the player across pad counts."""

    def test_remap_follows_player_across_pad_count(self):
        with tempfile.TemporaryDirectory() as d:
            ini = Path(d) / "PCSX2.ini"
            ini.write_text("[Pad1]\nType = DualShock2\nCross = SDL-0/FaceSouth\n\n[Pad]\n",
                           encoding="utf-8")
            pcsx2_cfg.save_input_overrides(ini, {2: {"Cross": "FaceWest"}})  # Player 2 remap
            ov = pcsx2_cfg.load_input_overrides(ini)
            players = [sd(i, DS5, f"g{i}", "DualSense") for i in range(3)]   # 3 pads
            with patch_sdl(players):
                pcsx2_cfg.assign_devices(players, ini_path=str(ini), manage=8, overrides=ov)
            text = ini.read_text(encoding="utf-8")
            # Player 2 lands on Pad3 with 3 pads (slot_plan), and the remap FOLLOWED there.
            self.assertIn("Cross = SDL-1/FaceWest", inifile.section_body(text, "Pad3"))
            self.assertIn("Cross = SDL-0/FaceSouth", inifile.section_body(text, "Pad1"))  # P1

    def test_empty_store_preserves_direct_gui_remap(self):
        # A remap made in PCSX2's OWN GUI (not via MAD, so the store is empty) must
        # survive a launch — the override path must NOT reset the slot to baked defaults.
        with tempfile.TemporaryDirectory() as d:
            ini = Path(d) / "PCSX2.ini"
            ini.write_text("[Pad1]\nType = DualShock2\nDeadzone = 0.1\n"
                           "Cross = SDL-0/FaceWest\nCircle = SDL-0/FaceEast\n", encoding="utf-8")
            ov = pcsx2_cfg.load_input_overrides(ini)   # {} — never touched MAD
            players = [sd(0, DS5, "gA", "DualSense")]
            with patch_sdl(players):
                pcsx2_cfg.assign_devices(players, ini_path=str(ini), manage=8, overrides=ov)
            pad1 = inifile.section_body(ini.read_text(), "Pad1")
            self.assertIn("Cross = SDL-0/FaceWest", pad1)   # direct-GUI remap preserved
            self.assertIn("Deadzone = 0.1", pad1)           # tuning preserved

    def test_migration_preserves_existing_remap(self):
        with tempfile.TemporaryDirectory() as d:
            ini = Path(d) / "PCSX2.ini"
            # legacy [Pad1] with a non-default Cross + DEFAULT Circle; empty store
            ini.write_text("[Pad1]\nType = DualShock2\nCross = SDL-0/FaceWest\n"
                           "Circle = SDL-0/FaceEast\n", encoding="utf-8")
            ov = pcsx2_cfg.migrate_overrides_from_ini(ini, ["Pad1"])
            self.assertEqual(ov.get(1, {}).get("Cross"), "FaceWest")    # non-default migrated
            self.assertNotIn("Circle", ov.get(1, {}))                   # default NOT migrated


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
        x._buf.reset()          # fresh buffer per case (module-level singleton; _FILE is repointed)

    def tearDown(self):
        x._FILE, x.proc_guard.emulator_running, x._supports_remap = self._file, self._run, self._sup
        x._buf.reset()
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
        x._input_save({})                                                        # buffered: commit on save
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

    def test_all_members_installed_keeps_group(self):
        # Switch group members = eden, ryujinx, citron (all present -> a sub-grid),
        # ordered ALPHABETICALLY by emulator name.
        st._emu_installed = lambda e: True
        sw = self._switch_tile()
        self.assertIn("members", sw)
        self.assertEqual([m["label"] for m in sw["members"]], ["Citron", "Eden", "Ryujinx"])

    def test_one_installed_collapses_to_sections(self):
        st._emu_installed = lambda e: e == "ryujinx"
        sw = self._switch_tile()
        self.assertIn("sections", sw)
        self.assertNotIn("members", sw)
        # the collapsed tile carries Ryujinx's own bespoke section tree
        self.assertEqual(sw["sections"], st._sections_for(st._EMUS["ryujinx"]))

    def test_neither_installed_drops_tile(self):
        st._emu_installed = lambda e: False
        self.assertIsNone(self._switch_tile())

    def test_unknown_member_treated_installed(self):
        self.assertTrue(self._inst("foo"))


if __name__ == "__main__":
    unittest.main()
