"""Docked/handheld context layer for the RPCS3 (PS3) input stores + launch selection. Mirrors
tests/test_handheld_input.py (the PCSX2 slice) for rpcs3: the context-keyed global override
sidecar (lib/rpcs3_cfg), the context-keyed per-game store (rpcs3_pergame_input_cmds), and the
launch block's context pick (switch_bind rpcs3 branch). P4 Tier-A.

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

from lib import handheld_input, rpcs3_cfg, switch_bind
from lib.madsrv import rpcs3_games
from lib.madsrv import rpcs3_input_cmds as R
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
    """The launch block's decision (switch_bind rpcs3 branch): pick the context-keyed GLOBAL +
    per-game maps for the resolved context and merge (per-game wins). Docked/handheld are
    independent axes; the MAD_FORCE_CONTEXT hook drives both paths headlessly."""

    ROM = "/roms/ps3/Demons Souls.iso"

    def setUp(self):
        self._env = os.environ.pop("MAD_FORCE_CONTEXT", None)
        self.d = Path(tempfile.mkdtemp())
        self.ovr = self.d / ".mad-input-overrides.yml"
        self._ovf, rpcs3_cfg._OVERRIDES_FILE = rpcs3_cfg._OVERRIDES_FILE, self.ovr
        self._st, PGI._STORE = PGI._STORE, self.d / "pergame-input.json"
        self._gy, rpcs3_games._GAMES_YML = rpcs3_games._GAMES_YML, self.d / "games.yml"
        (self.d / "games.yml").write_text(f"{_S}: {self.ROM}\n", encoding="utf-8")
        # Distinct global maps so the merged Cross tells us which context won.
        rpcs3_cfg.save_overrides({1: {"Cross": "South"}}, "docked")
        rpcs3_cfg.save_overrides({1: {"Cross": "North"}}, "handheld")

    def tearDown(self):
        rpcs3_cfg._OVERRIDES_FILE = self._ovf
        PGI._STORE = self._st
        rpcs3_games._GAMES_YML = self._gy
        os.environ.pop("MAD_FORCE_CONTEXT", None)
        if self._env is not None:
            os.environ["MAD_FORCE_CONTEXT"] = self._env
        shutil.rmtree(self.d, ignore_errors=True)

    def _overrides(self):
        # Drives the SAME helper the launch block uses (switch_bind._rpcs3_launch_overrides), so a
        # regression in the real per-game / context selection is caught here.
        return switch_bind._rpcs3_launch_overrides(self.ROM, handheld_input.context())

    def _write_pergame(self, obj):
        PGI._STORE.write_text(json.dumps(obj), encoding="utf-8")

    def test_forced_handheld_binds_handheld_map(self):
        os.environ["MAD_FORCE_CONTEXT"] = "handheld"
        self.assertEqual(self._overrides().get(1, {}).get("Cross"), "North")

    def test_forced_docked_binds_docked_map(self):
        os.environ["MAD_FORCE_CONTEXT"] = "docked"
        self.assertEqual(self._overrides().get(1, {}).get("Cross"), "South")

    def test_handheld_unset_falls_back_to_stock(self):
        rpcs3_cfg.save_overrides({}, "handheld")                    # clear the handheld map
        os.environ["MAD_FORCE_CONTEXT"] = "handheld"
        self.assertEqual(self._overrides(), {})                     # stock (no overrides), NOT docked
        os.environ["MAD_FORCE_CONTEXT"] = "docked"
        self.assertEqual(self._overrides().get(1, {}).get("Cross"), "South")   # docked map still there

    def test_docked_per_game_does_not_leak_into_handheld(self):
        self._write_pergame({_S: {"docked": {"1": {"Circle": "West"}}}})
        os.environ["MAD_FORCE_CONTEXT"] = "handheld"
        ov = self._overrides()
        self.assertNotIn("Circle", ov.get(1, {}))                   # per-game docked NOT applied
        self.assertEqual(ov.get(1, {}).get("Cross"), "North")       # handheld GLOBAL still applies

    def test_docked_per_game_applies_when_docked(self):
        self._write_pergame({_S: {"docked": {"1": {"Circle": "West"}}}})
        os.environ["MAD_FORCE_CONTEXT"] = "docked"
        ov = self._overrides()
        self.assertEqual(ov.get(1, {}).get("Circle"), "West")       # per-game layered over docked
        self.assertEqual(ov.get(1, {}).get("Cross"), "South")       # docked GLOBAL too

    def test_handheld_per_game_applies_on_handheld(self):
        self._write_pergame({_S: {"handheld": {"1": {"Circle": "East"}}}})
        os.environ["MAD_FORCE_CONTEXT"] = "handheld"
        ov = self._overrides()
        self.assertEqual(ov.get(1, {}).get("Circle"), "East")       # handheld per-game applied
        self.assertEqual(ov.get(1, {}).get("Cross"), "North")       # handheld GLOBAL still applies

    def test_legacy_flat_per_game_reads_docked_never_handheld(self):
        self._write_pergame({_S: {"1": {"Circle": "West"}}})        # pre-handheld flat entry
        os.environ["MAD_FORCE_CONTEXT"] = "docked"
        self.assertEqual(self._overrides().get(1, {}).get("Circle"), "West")   # applies docked
        os.environ["MAD_FORCE_CONTEXT"] = "handheld"
        self.assertNotIn("Circle", self._overrides().get(1, {}))    # never leaks to handheld


class PerGameContext(unittest.TestCase):
    """The per-game store (pergame-input.json) is context-keyed: the editor writes the slice for
    params["context"], the other context is preserved, and a legacy flat entry reads as docked and
    never leaks into handheld. Value codes: 0x131 (305) -> East, 0x130 (304) -> South."""

    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self._st, PGI._STORE = PGI._STORE, self.d / "pergame-input.json"
        self.ovr = self.d / ".mad-input-overrides.yml"          # absent -> global source = template default
        self._ovf, rpcs3_cfg._OVERRIDES_FILE = rpcs3_cfg._OVERRIDES_FILE, self.ovr
        PGI._buf.reset()
        import lib.staterev as sr
        self._bump, sr.bump = sr.bump, lambda name: None

    def tearDown(self):
        PGI._STORE = self._st
        rpcs3_cfg._OVERRIDES_FILE = self._ovf
        PGI._buf.reset()
        import lib.staterev as sr
        sr.bump = self._bump
        shutil.rmtree(self.d, ignore_errors=True)

    def _store(self):
        return json.loads(PGI._STORE.read_text()) if PGI._STORE.exists() else {}

    def _set(self, **params):                                   # stage + save one remap for this game
        PGI._input_set({"titleid": _S, **params})
        PGI._input_save({"titleid": _S, **params})

    def test_docked_edit_lands_in_docked_slice(self):
        self._set(id="Cross", kind="btn", value="305", context="docked")   # East
        self.assertEqual(self._store(), {_S: {"docked": {"1": {"Cross": "East"}}}})

    def test_handheld_edit_preserves_docked(self):
        self._set(id="Cross", kind="btn", value="305", context="docked")   # East docked
        self._set(id="Cross", kind="btn", value="304", context="handheld") # South handheld
        entry = self._store()[_S]
        self.assertEqual(entry["docked"]["1"]["Cross"], "East")
        self.assertEqual(entry["handheld"]["1"]["Cross"], "South")

    def test_binds_for_reads_the_context_slice(self):
        self._set(id="Cross", kind="btn", value="305", context="docked")   # East docked
        self._set(id="Circle", kind="btn", value="304", context="handheld") # South handheld
        self.assertEqual(PGI.binds_for(_S, "docked"), {"1": {"Cross": "East"}})
        self.assertEqual(PGI.binds_for(_S, "handheld"), {"1": {"Circle": "South"}})

    def test_legacy_flat_entry_reads_docked_never_handheld(self):
        PGI._STORE.write_text(json.dumps({_S: {"1": {"Cross": "East"}}}))  # pre-handheld flat entry
        self.assertEqual(PGI.binds_for(_S, "docked"), {"1": {"Cross": "East"}})
        self.assertEqual(PGI.binds_for(_S, "handheld"), {})                # never leaks to handheld

    def test_clearing_one_context_preserves_the_sibling(self):
        # DATA-LOSS guard: with BOTH contexts populated, clearing the last docked bind must drop only
        # the docked slice and KEEP the handheld per-game map (a regression to data.pop(serial) here
        # would silently destroy the surviving context).
        PGI._STORE.write_text(json.dumps({_S: {"docked": {"1": {"Cross": "East"}},
                                               "handheld": {"1": {"Circle": "South"}}}}))
        PGI._buf.reset()
        PGI._input_clear({"titleid": _S, "player": "1", "id": "Cross", "context": "docked"})
        PGI._input_save({"titleid": _S, "context": "docked"})
        entry = self._store()[_S]
        self.assertNotIn("docked", entry)                         # emptied docked slice dropped
        self.assertEqual(entry["handheld"]["1"]["Circle"], "South")   # sibling survives


class GlobalEditorContext(unittest.TestCase):
    """Drive the REAL global editor RPC (rpcs3.input_set / input_save) with a context, proving the
    changed surface (_ctx -> buffer key -> _flush -> save_overrides(context=)) routes to the right
    slice and never clobbers the sibling context (the store/launch tests exercise it only
    indirectly). Value codes: 0x131 (305) -> East, 0x130 (304) -> South."""

    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.default = self.d / "Default.yml"
        self.default.write_text(yaml.safe_dump(
            {"Player 1 Input": {"Handler": "DualSense", "Device": "DualSense Pad #1",
                                "Config": {}, "Buddy Device": "Null"}}, sort_keys=False), encoding="utf-8")
        self.ovr = self.d / ".mad-input-overrides.yml"
        self._def, R._DEFAULT = R._DEFAULT, self.default
        self._ovf, rpcs3_cfg._OVERRIDES_FILE = rpcs3_cfg._OVERRIDES_FILE, self.ovr
        R._buf.reset()
        import lib.staterev as sr
        self._bump, sr.bump = sr.bump, lambda name: None

    def tearDown(self):
        R._DEFAULT = self._def
        rpcs3_cfg._OVERRIDES_FILE = self._ovf
        R._buf.reset()
        import lib.staterev as sr
        sr.bump = self._bump
        shutil.rmtree(self.d, ignore_errors=True)

    def _set(self, **params):                                     # stage + save one remap via the RPC
        R._input_set(params)
        R._input_save(params)

    def test_handheld_edit_writes_handheld_slice_preserving_docked(self):
        self._set(id="Cross", kind="btn", value="305", player="1", context="docked")     # East docked
        self._set(id="Cross", kind="btn", value="304", player="1", context="handheld")   # South handheld
        self.assertEqual(rpcs3_cfg.load_overrides("docked"), {1: {"Cross": "East"}})
        self.assertEqual(rpcs3_cfg.load_overrides("handheld"), {1: {"Cross": "South"}})

    def test_docked_edit_default_context_preserves_handheld(self):
        self._set(id="Cross", kind="btn", value="304", player="1", context="handheld")   # South handheld
        self._set(id="Cross", kind="btn", value="305", player="1")                        # no context -> docked
        self.assertEqual(rpcs3_cfg.load_overrides("handheld"), {1: {"Cross": "South"}})
        self.assertEqual(rpcs3_cfg.load_overrides("docked"), {1: {"Cross": "East"}})


if __name__ == "__main__":
    unittest.main()
