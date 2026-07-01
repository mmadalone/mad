"""Tests for the RPCS3 input-map RPC (rpcs3.input_get / rpcs3.input_set) and the
rpcs3_cfg per-player Config-preserve change. Pure temp-copy; no hardware.

Run:  python3 -m unittest tests.test_rpcs3_input -v
"""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

import yaml

from lib import rpcs3_cfg, switch_bind
from lib.madsrv import rpcs3_input_cmds as r
from lib.madsrv.input_translate import rpcs3_token_label
from lib.madsrv.rpc import RpcError
from tests._fakes import patch_sdl, sd

DS5 = "054c:0ce6"


def _native_doc():
    # Resting config on RPCS3's NATIVE handlers (the common real-world state). MAD must
    # let the user remap regardless — overrides are merged into the SDL profile at launch.
    return {
        "Player 1 Input": {"Handler": "DualSense", "Device": "DualSense Pad #1",
                           "Config": {}, "Buddy Device": "Null"},
        "Player 2 Input": {"Handler": "DualShock 4", "Device": "DualShock 4 Pad #1",
                           "Config": {}, "Buddy Device": "Null"},
        "Player 3 Input": {"Handler": "Null", "Device": "Null", "Config": {},
                           "Buddy Device": "Null"},
    }


class Rpcs3InputMap(unittest.TestCase):
    """The picker edits a MAD-owned override file (NOT Default.yml), works regardless of
    the resting handler, and never touches the resting config."""

    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.default = self.d / "Default.yml"
        self.default.write_text(yaml.safe_dump(_native_doc(), sort_keys=False), encoding="utf-8")
        self.ovr = self.d / ".mad-input-overrides.yml"
        self._def, r._DEFAULT = r._DEFAULT, self.default
        self._ovf, rpcs3_cfg._OVERRIDES_FILE = rpcs3_cfg._OVERRIDES_FILE, self.ovr

    def tearDown(self):
        r._DEFAULT = self._def
        rpcs3_cfg._OVERRIDES_FILE = self._ovf
        shutil.rmtree(self.d, ignore_errors=True)

    def test_players_from_resting_nonnull(self):
        res = r._input_get({"player": ""})
        self.assertEqual([p["label"] for p in res["players"]], ["Player 1", "Player 2"])
        self.assertEqual(res["player"], "1")

    def test_editable_regardless_of_native_handler(self):
        res = r._input_get({})
        self.assertTrue(res["groups"][0]["binds"][0]["capturable"])    # buttons editable
        self.assertTrue(res["groups"][2]["binds"][0]["capturable"])    # sticks now remappable

    def test_input_set_writes_override_not_default(self):
        before = self.default.read_text(encoding="utf-8")
        r._input_set({"id": "Cross", "kind": "btn", "value": str(0x131), "player": "2"})  # East
        self.assertEqual(rpcs3_cfg.load_overrides(), {2: {"Cross": "East"}})
        self.assertEqual(self.default.read_text(encoding="utf-8"), before)   # Default.yml untouched

    def test_input_set_dpad(self):
        r._input_set({"id": "Up", "kind": "hat", "value": "h0left", "player": "1"})
        self.assertEqual(rpcs3_cfg.load_overrides(), {1: {"Up": "Left"}})

    def test_input_set_stick(self):
        # push the physical stick up (evdev -Y) -> RPCS3 up token "LS Y+" (Y is up-positive)
        r._input_set({"id": "Left Stick Up", "kind": "axis", "value": "-left_y", "player": "1"})
        r._input_set({"id": "Right Stick Right", "kind": "axis", "value": "+right_x", "player": "1"})
        self.assertEqual(rpcs3_cfg.load_overrides(),
                         {1: {"Left Stick Up": "LS Y+", "Right Stick Right": "RS X+"}})
        res = r._input_get({"player": "1"})
        up = next(b for b in res["groups"][2]["binds"] if b["id"] == "Left Stick Up")
        self.assertEqual(up["value"], rpcs3_token_label("LS Y+"))   # friendly label shown

    def test_input_get_reflects_override(self):
        r._input_set({"id": "Cross", "kind": "btn", "value": str(0x131), "player": "1"})  # East
        res = r._input_get({"player": "1"})
        cross = next(b for b in res["groups"][0]["binds"] if b["id"] == "Cross")
        self.assertEqual(cross["value"], rpcs3_token_label("East"))

    def test_rejects_unmappable_code(self):
        with self.assertRaises(RpcError):
            r._input_set({"id": "Cross", "kind": "btn", "value": str(0x2c0)})

    def test_rejects_unknown_key(self):
        with self.assertRaises(RpcError):
            r._input_set({"id": "Nope", "kind": "btn", "value": str(0x130)})

    def test_out_of_range_player_raises_not_misdirects(self):
        # count == 2 (Player 1 + 2 non-Null) → Player 5 must error, not silently write P1.
        with self.assertRaises(RpcError):
            r._input_set({"id": "Cross", "kind": "btn", "value": str(0x131), "player": "5"})

    def test_input_get_shows_resting_sdl_config(self):
        # A user who set RPCS3's SDL handler with a custom map (Cross=North), no MAD
        # override → the page shows THEIR value (preserved in-game), not the template.
        doc = {"Player 1 Input": {"Handler": "SDL", "Device": "Pad 1",
                                  "Config": {"Cross": "North"}, "Buddy Device": "Null"}}
        self.default.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
        res = r._input_get({"player": "1"})
        cross = next(b for b in res["groups"][0]["binds"] if b["id"] == "Cross")
        self.assertEqual(cross["value"], rpcs3_token_label("North"))


class Rpcs3OverrideAppliesAtBind(unittest.TestCase):
    """A MAD override is merged into the SDL profile the launch binder writes
    (apply-in-game), regardless of the resting native handler."""

    def test_assign_devices_merges_override(self):
        with tempfile.TemporaryDirectory() as d:
            default = Path(d) / "Default.yml"
            default.write_text(yaml.safe_dump(_native_doc(), sort_keys=False), encoding="utf-8")
            ovr = Path(d) / ".mad-input-overrides.yml"
            ovr.write_text(yaml.safe_dump({1: {"Cross": "East"}, 2: {"Square": "North"}}),
                           encoding="utf-8")
            self._ovf, rpcs3_cfg._OVERRIDES_FILE = rpcs3_cfg._OVERRIDES_FILE, ovr
            try:
                players = [sd(0, DS5, "ga", "DualSense"), sd(1, DS5, "gb", "DualSense")]
                with patch_sdl(players):
                    rpcs3_cfg.assign_devices(players, config_path=str(default), manage=7)
                out = yaml.safe_load(default.read_text(encoding="utf-8"))
                self.assertEqual(out["Player 1 Input"]["Handler"], "SDL")
                self.assertEqual(out["Player 1 Input"]["Config"]["Cross"], "East")    # override applied
                self.assertEqual(out["Player 2 Input"]["Config"]["Square"], "North")  # override applied
                self.assertEqual(out["Player 1 Input"]["Config"]["Circle"], "East")   # template default kept
            finally:
                rpcs3_cfg._OVERRIDES_FILE = self._ovf


class Rpcs3ConfigPreserve(unittest.TestCase):
    """rpcs3_cfg now PRESERVES an existing SDL per-button Config at launch (only the
    Device string changes) so a remap applies in-game, instead of resetting to the
    canonical template every launch."""

    def test_player_block_preserves_sdl_config(self):
        existing = {"Handler": "SDL", "Device": "old",
                    "Config": {"Cross": "East"}, "Buddy Device": "Null"}
        b = rpcs3_cfg._player_block(existing, "NEWDEV 1")
        self.assertEqual(b["Config"]["Cross"], "East")   # preserved
        self.assertEqual(b["Device"], "NEWDEV 1")        # only Device changed

    def test_player_block_fallback_non_sdl(self):
        ds = {"Handler": "DualSense", "Device": "x", "Config": {"Cross": "Cross"}}
        b = rpcs3_cfg._player_block(ds, "NEWDEV 1")
        self.assertEqual(b["Handler"], "SDL")
        self.assertEqual(b["Config"]["Cross"], "South")  # canonical template

    def test_player_block_fallback_none(self):
        b = rpcs3_cfg._player_block(None, "NEWDEV 1")
        self.assertEqual(b["Handler"], "SDL")
        self.assertEqual(b["Config"]["Cross"], "South")

    def test_assign_devices_preserves_existing_sdl_config(self):
        with tempfile.TemporaryDirectory() as d:
            yml = Path(d) / "Default.yml"
            doc = {"Player 1 Input": {"Handler": "SDL", "Device": "old",
                                      "Config": {"Cross": "East", "Square": "South"},
                                      "Buddy Device": "Null"}}
            yml.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
            players = [sd(0, DS5, "g", "DualSense")]
            with patch_sdl(players):
                rpcs3_cfg.assign_devices(players, config_path=str(yml), manage=2)
            out = yaml.safe_load(yml.read_text(encoding="utf-8"))
            self.assertEqual(out["Player 1 Input"]["Config"]["Cross"], "East")   # remap kept
            self.assertEqual(out["Player 1 Input"]["Device"], "DualSense 1")     # device re-pointed
            self.assertEqual(out["Player 1 Input"]["Handler"], "SDL")
            self.assertEqual(out["Player 2 Input"]["Handler"], "Null")

    def test_assign_devices_template_for_null_slot(self):
        with tempfile.TemporaryDirectory() as d:
            yml = Path(d) / "Default.yml"
            doc = {"Player 1 Input": {"Handler": "Null", "Device": "Null", "Config": {},
                                      "Buddy Device": "Null"}}
            yml.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
            players = [sd(0, DS5, "g", "DualSense")]
            with patch_sdl(players):
                rpcs3_cfg.assign_devices(players, config_path=str(yml), manage=2)
            out = yaml.safe_load(yml.read_text(encoding="utf-8"))
            self.assertEqual(out["Player 1 Input"]["Config"]["Cross"], "South")  # template


class Rpcs3SnapshotBindRestore(unittest.TestCase):
    """End-to-end composition of the riskiest path (CRITICAL ARCHITECTURE note): a
    per-button remap on a resting RPCS3 config must BOTH (a) survive the launch-time
    bind that re-points the Device — apply-in-game — AND (b) survive the on-exit
    restore back to the resting config. Mirrors switch_bind.bind()/restore_target()
    minus the hardware pad resolution. Deep-review #3 confirmed this by hand; this
    codifies it (a future change to _player_block / _snapshot / restore_target's
    rpcs3 branch can no longer silently break either half). The PCSX2 fix will need
    the identical harness."""

    def _resting(self):
        # Player 1 + 2 both SDL; Player 2 carries a REMAP (Config[Cross]=East, not the
        # template South). KEEP_ME is a non-pad global setting that must survive.
        return {
            "Player 1 Input": {"Handler": "SDL", "Device": "Steam Deck Controller 1",
                               "Config": {"Cross": "South", "Circle": "East"},
                               "Buddy Device": "Null"},
            "Player 2 Input": {"Handler": "SDL", "Device": "Steam Deck Controller 2",
                               "Config": {"Cross": "East", "Circle": "East"},
                               "Buddy Device": "Null"},
            "KEEP_ME": {"glob": "setting"},
        }

    def test_remap_applies_at_bind_and_survives_restore(self):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, True)
        target = d / "Default.yml"
        target.write_text(yaml.safe_dump(self._resting(), sort_keys=False), encoding="utf-8")

        # 1) snapshot (bind() does this once, before binding) → sidecar
        side = switch_bind._sidecar(target)
        snap = switch_bind._snapshot("rpcs3", target)
        side.write_text(json.dumps({"emu": "rpcs3", "input": snap}), encoding="utf-8")
        self.assertEqual(snap["Player 2 Input"]["Config"]["Cross"], "East")

        # 2) bind 3 pads → assign_devices re-points Devices AND adds a Player 3 block.
        #    The _player_block deep-copy must KEEP Player 2's East (apply-in-game half).
        players = [sd(0, DS5, "ga", "DualSense"),
                   sd(1, DS5, "gb", "DualSense"),
                   sd(2, DS5, "gc", "DualSense")]
        with patch_sdl(players):
            rpcs3_cfg.assign_devices(players, config_path=str(target), manage=7)
        bound = yaml.safe_load(target.read_text(encoding="utf-8"))
        self.assertEqual(bound["Player 2 Input"]["Config"]["Cross"], "East")   # remap in-game
        self.assertEqual(bound["Player 2 Input"]["Device"], "DualSense 2")     # device re-pointed
        self.assertEqual(bound["Player 3 Input"]["Handler"], "SDL")            # bind ADDED P3

        # 3) restore → resting Player 2 East kept; the added Player 3 dropped (switch_bind
        #    .py:238-246); the resting Devices + the global KEEP_ME restored intact.
        switch_bind.restore_target(target)
        rest = yaml.safe_load(target.read_text(encoding="utf-8"))
        self.assertEqual(rest["Player 2 Input"]["Config"]["Cross"], "East")    # survive-restore
        self.assertEqual(rest["Player 2 Input"]["Device"], "Steam Deck Controller 2")
        self.assertNotIn("Player 3 Input", rest)                               # added block gone
        self.assertEqual(rest["KEEP_ME"], {"glob": "setting"})                 # global kept
        self.assertFalse(side.exists())                                        # sidecar consumed


if __name__ == "__main__":
    unittest.main()
