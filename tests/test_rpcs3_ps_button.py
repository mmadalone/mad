"""Tests for the surviving PS-button chord page (rpcs3ps.input_*) — the one per-button
mapping that outlived the input-profile migration. Chord capture/serialization relocated
from the retired test_rpcs3_input.py; plus the data-preservation contract: a buffered save
must ride legacy gameplay keys in the sidecar through UNTOUCHED.

Run:  python3 -m unittest tests.test_rpcs3_ps_button -v
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

from lib import rpcs3_cfg
from lib.madsrv import rpcs3_ps_cmds as P
from lib.madsrv.rpc import RpcError

# evdev codes (from the retired chord suite): 0x13A=Select/Back, 0x13B=Start, 0x13C=Guide/PS
SELECT, START, GUIDE, BAD = 0x13A, 0x13B, 0x13C, 0x2C0


def _native_doc():
    return {
        "Player 1 Input": {"Handler": "DualSense", "Device": "DualSense Pad #1",
                           "Config": {}, "Buddy Device": "Null"},
        "Player 2 Input": {"Handler": "DualShock 4", "Device": "DualShock 4 Pad #1",
                           "Config": {}, "Buddy Device": "Null"},
        "Player 3 Input": {"Handler": "Null", "Device": "Null", "Config": {},
                           "Buddy Device": "Null"},
    }


class PsButtonPage(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.d, True)
        self.default = self.d / "global" / "Default.yml"
        self.default.parent.mkdir(parents=True)
        self.default.write_text(yaml.safe_dump(_native_doc(), sort_keys=False),
                                encoding="utf-8")
        self.ovr = self.d / "global" / ".mad-input-overrides.yml"
        self._ovf, rpcs3_cfg._OVERRIDES_FILE = rpcs3_cfg._OVERRIDES_FILE, self.ovr
        self.addCleanup(setattr, rpcs3_cfg, "_OVERRIDES_FILE", self._ovf)
        p = mock.patch.object(P, "load_merged",
                              lambda: {"backends": {"rpcs3": {"config_file": str(self.default)}}})
        p.start()
        self.addCleanup(p.stop)
        P._buf.reset()
        self.addCleanup(P._buf.reset)

    def _set(self, **params):
        res = P._input_set(params)
        P._input_save(params)
        return res

    # ── page shape ──
    def test_page_is_one_capturable_chord_row(self):
        r = P._input_get({})
        self.assertTrue(r["buffered"])                             # arms X=Save / Y=Cancel
        self.assertEqual(len(r["groups"]), 1)
        binds = r["groups"][0]["binds"]
        self.assertEqual([(b["id"], b["kind"], b["capturable"]) for b in binds],
                         [("PS Button", "chord", True)])
        self.assertEqual(binds[0]["value"], "Guide")               # template default, unset

    def test_players_follow_resting_config(self):
        r = P._input_get({})
        self.assertEqual([p["id"] for p in r["players"]], ["1", "2"])   # dense non-Null walk
        with self.assertRaises(RpcError):
            P._input_get({"player": "3"})                          # beyond count -> reject
        with self.assertRaises(RpcError):
            P._input_get({"player": "x"})

    def test_resting_ladder_follows_active_pointer(self):
        # the page reads the file the next launch will target (active_input_configurations)
        steam = self.d / "global" / "Steamdeck.yml"
        steam.write_text(yaml.safe_dump({f"Player {k} Input":
                                         {"Handler": "SDL", "Device": f"X {k}",
                                          "Config": {"Cross": "South"}, "Buddy Device": "Null"}
                                         for k in (1, 2, 3)}, sort_keys=False), encoding="utf-8")
        (self.d / "active_input_configurations.yml").write_text(
            yaml.safe_dump({"Active Configurations": {"global": "Steamdeck"}}), encoding="utf-8")
        r = P._input_get({})
        self.assertEqual([p["id"] for p in r["players"]], ["1", "2", "3"])

    # ── chord capture (relocated suite) ──
    def test_chord_writes_combo(self):
        self._set(id="PS Button", kind="chord", codes=[SELECT, START], player="1")
        self.assertEqual(rpcs3_cfg.load_overrides(), {1: {"PS Button": "Back&Start"}})

    def test_combo_display(self):
        self._set(id="PS Button", kind="chord", codes=[SELECT, START], player="1")
        r = P._input_get({"player": "1"})
        self.assertEqual(r["groups"][0]["binds"][0]["value"], "Select + Start")

    def test_single_hold_is_plain_token(self):
        self._set(id="PS Button", kind="chord", codes=[GUIDE], player="1")
        self.assertEqual(rpcs3_cfg.load_overrides(), {1: {"PS Button": "Guide"}})

    def test_combo_dedup(self):
        self._set(id="PS Button", kind="chord", codes=[SELECT, SELECT, START], player="1")
        self.assertEqual(rpcs3_cfg.load_overrides(), {1: {"PS Button": "Back&Start"}})

    def test_combo_rejects_unmappable(self):
        with self.assertRaises(RpcError):
            P._input_set({"id": "PS Button", "kind": "chord", "codes": [SELECT, BAD],
                          "player": "1"})

    def test_combo_rejects_empty(self):
        with self.assertRaises(RpcError):
            P._input_set({"id": "PS Button", "kind": "chord", "codes": [], "player": "1"})

    def test_gameplay_keys_not_remappable_here(self):
        # profiles own gameplay buttons now — the page only maps the PS chord
        with self.assertRaises(RpcError):
            P._input_set({"id": "Cross", "kind": "btn", "value": str(0x131), "player": "1"})

    def test_per_player_chords(self):
        self._set(id="PS Button", kind="chord", codes=[SELECT, START], player="1")
        self._set(id="PS Button", kind="chord", codes=[GUIDE], player="2")
        self.assertEqual(rpcs3_cfg.load_overrides(),
                         {1: {"PS Button": "Back&Start"}, 2: {"PS Button": "Guide"}})

    # ── buffer + data preservation ──
    def test_cancel_discards(self):
        P._input_set({"id": "PS Button", "kind": "chord", "codes": [SELECT, START],
                      "player": "1"})
        P._input_cancel({})
        self.assertFalse(self.ovr.exists())
        self.assertEqual(rpcs3_cfg.load_overrides(), {})

    def test_save_preserves_foreign_gameplay_keys(self):
        # A legacy sidecar (flat, gameplay keys) must ride through a chord save UNTOUCHED —
        # the flush replays onto a FRESH full store read (never destroy user data). The
        # legacy flat shape migrates context-keyed on the way (docked slice).
        self.ovr.write_text(yaml.safe_dump(
            {1: {"L2": "LT", "Left Stick Up": "LS Y+"}}), encoding="utf-8")
        self._set(id="PS Button", kind="chord", codes=[SELECT, START], player="1")
        disk = yaml.safe_load(self.ovr.read_text(encoding="utf-8"))
        self.assertEqual(disk["docked"][1],
                         {"L2": "LT", "Left Stick Up": "LS Y+", "PS Button": "Back&Start"})

    def test_save_replays_onto_fresh_disk_preserving_foreign_edit(self):
        # REVIEW FIX (relocated contract from the retired editor, mirror of
        # test_eden_hotkeys' fresh-replay pin): an edit staged BEFORE a foreign sidecar
        # write must not clobber that write on save — _flush must re-read the store, not
        # blind-write the buffer's open-time snapshot (the Eden-corruption class).
        P._input_set({"id": "PS Button", "kind": "chord", "codes": [SELECT, START],
                      "player": "1"})                              # stage FIRST
        rpcs3_cfg.save_overrides({2: {"PS Button": "Guide"}}, "docked")   # foreign write
        P._input_save({})
        self.assertEqual(rpcs3_cfg.load_overrides("docked"),
                         {1: {"PS Button": "Back&Start"}, 2: {"PS Button": "Guide"}})

    def test_display_mirrors_the_picked_profile(self):
        # REVIEW FIX: since the migration the launch injects the picked profile BEFORE
        # seating, so with no MAD chord the in-game PS binding is the PROFILE's — the page
        # must show it (not the resting/template value). A staged MAD chord still wins.
        (self.d / "global" / "Race.yml").write_text(yaml.safe_dump(
            {"Player 1 Input": {"Handler": "SDL", "Device": "X 1",
                                "Config": {"Cross": "South", "PS Button": "Back&Start"},
                                "Buddy Device": "Null"}}), encoding="utf-8")
        with mock.patch.object(P, "load_merged",
                               lambda: {"backends": {"rpcs3": {
                                   "config_file": str(self.default),
                                   "profile_docked": "Race"}}}):
            r = P._input_get({"player": "1"})
            self.assertEqual(r["groups"][0]["binds"][0]["value"], "Select + Start")
            P._input_set({"id": "PS Button", "kind": "chord", "codes": [GUIDE],
                          "player": "1"})
            r = P._input_get({"player": "1"})
            self.assertEqual(r["groups"][0]["binds"][0]["value"], "Guide")   # chord wins

    def test_context_slices_are_independent(self):
        self._set(id="PS Button", kind="chord", codes=[SELECT, START], player="1",
                  context="docked")
        self._set(id="PS Button", kind="chord", codes=[GUIDE], player="1",
                  context="handheld")
        self.assertEqual(rpcs3_cfg.load_overrides("docked"), {1: {"PS Button": "Back&Start"}})
        self.assertEqual(rpcs3_cfg.load_overrides("handheld"), {1: {"PS Button": "Guide"}})
        # and the launch slice picks each context's own chord
        self.assertEqual(rpcs3_cfg.ps_button_overrides("handheld"),
                         {1: {"PS Button": "Guide"}})

    def test_effective_value_prefers_override_then_resting_sdl(self):
        # resting SDL config with its own PS chord shows through when no MAD override
        doc = _native_doc()
        doc["Player 1 Input"] = {"Handler": "SDL", "Device": "X 1",
                                 "Config": {"PS Button": "Back&Start"}, "Buddy Device": "Null"}
        self.default.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
        r = P._input_get({"player": "1"})
        self.assertEqual(r["groups"][0]["binds"][0]["value"], "Select + Start")


if __name__ == "__main__":
    unittest.main()
