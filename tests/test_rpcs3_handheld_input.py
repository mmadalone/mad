"""Docked/handheld context layer for the RPCS3 (PS3) input stores + launch selection. Mirrors
tests/test_handheld_input.py (the PCSX2 slice) for rpcs3: the context-keyed global override
sidecar (lib/rpcs3_cfg — now the PS-button store), the per-game store's new entry shape
(profile picks + inert legacy binds), and the launch rail's context pick (switch_bind rpcs3
branch: ps_button_overrides + rpcs3_profiles.resolve).

Run:  python3 -m unittest tests.test_rpcs3_handheld_input -v
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

import yaml

from lib import handheld_input, rpcs3_cfg, rpcs3_profiles, switch_bind
from lib.madsrv import rpcs3_games
from lib.madsrv import rpcs3_pergame_input_cmds as PGI

_S = "BLES00590"


class ContextKeyedStore(unittest.TestCase):
    """The global override sidecar (.mad-input-overrides.yml) is context-keyed like PCSX2's: a
    legacy flat sidecar reads as docked, saving one context preserves the other + migrates, and an
    unset handheld context reads as {} (=> stock)."""

    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.ovr = self.d / ".mad-input-overrides.yml"
        self._ovf, rpcs3_cfg._OVERRIDES_FILE = rpcs3_cfg._OVERRIDES_FILE, self.ovr

    def tearDown(self):
        rpcs3_cfg._OVERRIDES_FILE = self._ovf
        shutil.rmtree(self.d, ignore_errors=True)

    def _write_side(self, obj):
        self.ovr.write_text(yaml.safe_dump(obj), encoding="utf-8")

    def test_legacy_flat_reads_as_docked(self):
        self._write_side({1: {"Cross": "East"}})                    # pre-handheld flat sidecar
        self.assertEqual(rpcs3_cfg.load_overrides("docked"), {1: {"Cross": "East"}})
        self.assertEqual(rpcs3_cfg.load_overrides("handheld"), {})

    def test_default_context_is_docked(self):
        self._write_side({1: {"Cross": "East"}})
        self.assertEqual(rpcs3_cfg.load_overrides(), {1: {"Cross": "East"}})

    def test_save_handheld_preserves_docked_and_migrates(self):
        self._write_side({1: {"Cross": "East"}})                    # legacy flat = docked
        rpcs3_cfg.save_overrides({1: {"Circle": "South"}}, "handheld")
        disk = yaml.safe_load(self.ovr.read_text())
        self.assertEqual(set(disk), {"docked", "handheld"})         # migrated to context shape
        self.assertEqual(rpcs3_cfg.load_overrides("docked"), {1: {"Cross": "East"}})
        self.assertEqual(rpcs3_cfg.load_overrides("handheld"), {1: {"Circle": "South"}})

    def test_save_docked_preserves_handheld(self):
        self._write_side({"handheld": {1: {"Circle": "South"}}})
        rpcs3_cfg.save_overrides({1: {"Cross": "East"}}, "docked")
        self.assertEqual(rpcs3_cfg.load_overrides("handheld"), {1: {"Circle": "South"}})
        self.assertEqual(rpcs3_cfg.load_overrides("docked"), {1: {"Cross": "East"}})

    def test_clearing_a_context_drops_it(self):
        self._write_side({"docked": {1: {"Cross": "East"}},
                          "handheld": {1: {"Circle": "South"}}})
        rpcs3_cfg.save_overrides({}, "handheld")                    # clear handheld only
        self.assertEqual(set(yaml.safe_load(self.ovr.read_text())), {"docked"})
        self.assertEqual(rpcs3_cfg.load_overrides("handheld"), {})
        self.assertEqual(rpcs3_cfg.load_overrides("docked"), {1: {"Cross": "East"}})

    def test_no_store_is_empty_in_both(self):
        self.assertEqual(rpcs3_cfg.load_overrides("docked"), {})
        self.assertEqual(rpcs3_cfg.load_overrides("handheld"), {})


class LaunchContextSelection(unittest.TestCase):
    """The launch rail's decision (switch_bind rpcs3 branch): the PS-button slice + the
    resolved profile stem for the detected context. Docked/handheld are independent axes;
    the MAD_FORCE_CONTEXT hook drives both paths headlessly. Drives the SAME helpers the
    rail uses (ps_button_overrides, _rpcs3_pergame, rpcs3_profiles.resolve)."""

    ROM = "/roms/ps3/Demons Souls.iso"
    BE = {"profile_docked": "DockProf", "profile_handheld": "HandProf"}

    def setUp(self):
        self._env = os.environ.pop("MAD_FORCE_CONTEXT", None)
        self.d = Path(tempfile.mkdtemp())
        self.ovr = self.d / ".mad-input-overrides.yml"
        self._ovf, rpcs3_cfg._OVERRIDES_FILE = rpcs3_cfg._OVERRIDES_FILE, self.ovr
        self._st, PGI._STORE = PGI._STORE, self.d / "pergame-input.json"
        self._gy, rpcs3_games._GAMES_YML = rpcs3_games._GAMES_YML, self.d / "games.yml"
        (self.d / "games.yml").write_text(f"{_S}: {self.ROM}\n", encoding="utf-8")
        # Distinct chords (+ a gameplay key that must stay inert) per context.
        rpcs3_cfg.save_overrides({1: {"PS Button": "Back&Start", "Cross": "East"}}, "docked")
        rpcs3_cfg.save_overrides({1: {"PS Button": "Guide"}}, "handheld")

    def tearDown(self):
        rpcs3_cfg._OVERRIDES_FILE = self._ovf
        PGI._STORE = self._st
        rpcs3_games._GAMES_YML = self._gy
        os.environ.pop("MAD_FORCE_CONTEXT", None)
        if self._env is not None:
            os.environ["MAD_FORCE_CONTEXT"] = self._env
        shutil.rmtree(self.d, ignore_errors=True)

    def _ps(self):
        return rpcs3_cfg.ps_button_overrides(context=handheld_input.context())

    def _stem(self, be=None):
        entry = switch_bind._rpcs3_pergame(self.ROM)
        return rpcs3_profiles.resolve(entry, self.BE if be is None else be,
                                      handheld_input.context())

    def _write_pergame(self, obj):
        PGI._STORE.write_text(json.dumps(obj), encoding="utf-8")

    def test_forced_handheld_binds_handheld_chord(self):
        os.environ["MAD_FORCE_CONTEXT"] = "handheld"
        self.assertEqual(self._ps(), {1: {"PS Button": "Guide"}})

    def test_forced_docked_binds_docked_chord_and_filters_gameplay(self):
        os.environ["MAD_FORCE_CONTEXT"] = "docked"
        self.assertEqual(self._ps(), {1: {"PS Button": "Back&Start"}})   # Cross=East inert

    def test_handheld_chord_unset_falls_back_to_stock(self):
        rpcs3_cfg.save_overrides({}, "handheld")                    # clear the handheld map
        os.environ["MAD_FORCE_CONTEXT"] = "handheld"
        self.assertEqual(self._ps(), {})                            # stock, NOT docked
        os.environ["MAD_FORCE_CONTEXT"] = "docked"
        self.assertEqual(self._ps(), {1: {"PS Button": "Back&Start"}})

    def test_context_resolves_its_own_global_profile(self):
        os.environ["MAD_FORCE_CONTEXT"] = "docked"
        self.assertEqual(self._stem(), "DockProf")
        os.environ["MAD_FORCE_CONTEXT"] = "handheld"
        self.assertEqual(self._stem(), "HandProf")

    def test_handheld_profile_never_inherits_docked(self):
        os.environ["MAD_FORCE_CONTEXT"] = "handheld"
        self.assertIsNone(self._stem({"profile_docked": "DockProf"}))

    def test_docked_pergame_pick_does_not_leak_into_handheld(self):
        self._write_pergame({_S: {"profiles": {"docked": "RaceWheel"}}})
        os.environ["MAD_FORCE_CONTEXT"] = "docked"
        self.assertEqual(self._stem(), "RaceWheel")                 # per-game beats global
        os.environ["MAD_FORCE_CONTEXT"] = "handheld"
        self.assertEqual(self._stem(), "HandProf")                  # handheld global, not the pick

    def test_pergame_pick_applies_only_to_its_game(self):
        self._write_pergame({_S: {"profiles": {"docked": "RaceWheel"}}})
        os.environ["MAD_FORCE_CONTEXT"] = "docked"
        other = rpcs3_profiles.resolve(switch_bind._rpcs3_pergame("/roms/ps3/Other.iso"),
                                       self.BE, handheld_input.context())
        self.assertEqual(other, "DockProf")                         # unmatched rom -> global

    def test_legacy_binds_only_entry_resolves_no_profile(self):
        # REGRESSION: an old per-button entry (pre-picker shape) must produce NO profile and
        # NO override — the launch rail no longer reads binds.
        self._write_pergame({_S: {"docked": {"1": {"Circle": "West"}}}})
        os.environ["MAD_FORCE_CONTEXT"] = "docked"
        entry = switch_bind._rpcs3_pergame(self.ROM)
        self.assertIsNotNone(entry)                                 # preserved (never pruned)…
        self.assertEqual(rpcs3_profiles.pergame_profile(entry, "docked"), None)
        self.assertEqual(self._stem(), "DockProf")                  # …but inert at launch
        self.assertFalse(PGI._has_input_override(entry))            # and not badged

    def test_absent_store_resolves_global(self):
        os.environ["MAD_FORCE_CONTEXT"] = "docked"
        self.assertIsNone(switch_bind._rpcs3_pergame(self.ROM))     # no store file
        self.assertEqual(self._stem(), "DockProf")


class StoreShape(unittest.TestCase):
    """The per-game store's NEW entry shape ({"binds": ..., "profiles": ...}) + the lossless
    legacy migrations. Pure dict-level (the store file is only touched by load_entry)."""

    def test_new_shape_passthrough(self):
        e = {"binds": {"docked": {"1": {"Cross": "East"}}},
             "profiles": {"docked": "Race", "handheld": "HH"}}
        norm = PGI._normalize_entry(e)
        self.assertEqual(norm["binds"], {"docked": {"1": {"Cross": "East"}}})
        self.assertEqual(norm["profiles"], {"docked": "Race", "handheld": "HH"})

    def test_legacy_context_keyed_folds_under_binds(self):
        norm = PGI._normalize_entry({"docked": {"1": {"Circle": "West"}}})
        self.assertEqual(norm, {"binds": {"docked": {"1": {"Circle": "West"}}}})

    def test_legacy_flat_folds_under_binds_docked(self):
        norm = PGI._normalize_entry({"1": {"Circle": "West"}})
        self.assertEqual(norm, {"binds": {"docked": {"1": {"Circle": "West"}}}})

    def test_profile_husks_healed(self):
        self.assertEqual(PGI._normalize_entry({"profiles": "junk"}), {})
        self.assertEqual(PGI._normalize_entry({"profiles": {"docked": 7, "handheld": " "}}), {})
        self.assertEqual(PGI._normalize_entry({"profiles": {"weird": "X", "docked": "OK"}}),
                         {"profiles": {"docked": "OK"}})

    def test_is_empty_keeps_legacy_binds_alive(self):
        # never destroy user data: a binds-only entry is NOT empty (won't be auto-pruned)
        self.assertFalse(PGI._is_empty({"binds": {"docked": {"1": {"Cross": "East"}}}}))
        self.assertFalse(PGI._is_empty({"profiles": {"docked": "Race"}}))
        self.assertTrue(PGI._is_empty({"binds": {"docked": {"1": {}}}}))
        self.assertTrue(PGI._is_empty({}))

    def test_badge_is_profiles_only(self):
        self.assertTrue(PGI._has_input_override({"profiles": {"handheld": "HH"}}))
        self.assertFalse(PGI._has_input_override({"binds": {"docked": {"1": {"Cross": "E"}}}}))

    def test_load_entry_validates_serial_and_prunes_husks(self):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, True)
        saved, PGI._STORE = PGI._STORE, d / "pergame-input.json"
        self.addCleanup(setattr, PGI, "_STORE", saved)
        PGI._STORE.write_text(json.dumps({_S: {"profiles": {"docked": "Race"}},
                                          "BCES00002": {"profiles": "junk"}}),
                              encoding="utf-8")
        self.assertEqual(PGI.load_entry(_S), {"profiles": {"docked": "Race"}})
        self.assertIsNone(PGI.load_entry("BCES00002"))              # husk -> None
        self.assertIsNone(PGI.load_entry("not-a-serial"))
        self.assertIsNone(PGI.load_entry(None))


if __name__ == "__main__":
    unittest.main()
