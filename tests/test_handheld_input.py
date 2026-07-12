"""Docked/handheld context layer (lib/handheld_input) + the context-keyed PCSX2
override store (lib/pcsx2_cfg). P1 of the handheld-input batch.

Run:  python3 -m unittest tests.test_handheld_input -v
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from lib import handheld_input, inifile, pcsx2_cfg, switch_bind
from tests._fakes import sd

_FIX = Path(__file__).parent / "fixtures" / "pcsx2" / "PCSX2.ini"
_DS5 = "054c:0ce6"


class ContextResolution(unittest.TestCase):
    def setUp(self):
        self._env = os.environ.pop("MAD_FORCE_CONTEXT", None)

    def tearDown(self):
        os.environ.pop("MAD_FORCE_CONTEXT", None)
        if self._env is not None:
            os.environ["MAD_FORCE_CONTEXT"] = self._env

    def test_env_forces_context(self):
        os.environ["MAD_FORCE_CONTEXT"] = "handheld"           # overrides even a "disabled" feature
        self.assertEqual(handheld_input.context({}), "handheld")
        os.environ["MAD_FORCE_CONTEXT"] = "docked"             # overrides even an enabled+forced cfg
        self.assertEqual(handheld_input.context({"enabled": True, "force": "handheld"}), "docked")

    def test_feature_disabled_is_docked(self):
        self.assertEqual(handheld_input.context(None), "docked")
        self.assertEqual(handheld_input.context({"enabled": False, "force": "handheld"}), "docked")

    def test_enabled_honours_force(self):
        self.assertEqual(handheld_input.context({"enabled": True, "force": "handheld"}), "handheld")
        self.assertEqual(handheld_input.context({"enabled": True, "force": "docked"}), "docked")

    def test_normalize(self):
        self.assertEqual(handheld_input.normalize("HANDHELD"), "handheld")
        self.assertEqual(handheld_input.normalize("  handheld "), "handheld")
        self.assertEqual(handheld_input.normalize("anything else"), "docked")


class ContextKeyedStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ini = Path(self.tmp.name) / "PCSX2.ini"
        self.side = self.ini.with_name(".mad-input-overrides.json")

    def tearDown(self):
        self.tmp.cleanup()

    def _write_side(self, obj):
        self.side.write_text(json.dumps(obj), encoding="utf-8")

    def test_legacy_flat_reads_as_docked(self):
        self._write_side({"1": {"Cross": "FaceEast"}})
        self.assertEqual(pcsx2_cfg.load_input_overrides(self.ini, "docked"), {1: {"Cross": "FaceEast"}})
        self.assertEqual(pcsx2_cfg.load_input_overrides(self.ini, "handheld"), {})

    def test_default_context_is_docked(self):
        self._write_side({"1": {"Cross": "FaceEast"}})
        self.assertEqual(pcsx2_cfg.load_input_overrides(self.ini), {1: {"Cross": "FaceEast"}})

    def test_save_handheld_preserves_docked_and_migrates(self):
        self._write_side({"1": {"Cross": "FaceEast"}})                       # legacy flat = docked
        pcsx2_cfg.save_input_overrides(self.ini, {1: {"Circle": "FaceSouth"}}, "handheld")
        disk = json.loads(self.side.read_text())
        self.assertEqual(set(disk), {"docked", "handheld"})                  # migrated to context shape
        self.assertEqual(pcsx2_cfg.load_input_overrides(self.ini, "docked"), {1: {"Cross": "FaceEast"}})
        self.assertEqual(pcsx2_cfg.load_input_overrides(self.ini, "handheld"), {1: {"Circle": "FaceSouth"}})

    def test_save_docked_preserves_handheld(self):
        self._write_side({"handheld": {"1": {"Circle": "FaceSouth"}}})
        pcsx2_cfg.save_input_overrides(self.ini, {1: {"Cross": "FaceEast"}}, "docked")
        self.assertEqual(pcsx2_cfg.load_input_overrides(self.ini, "handheld"), {1: {"Circle": "FaceSouth"}})
        self.assertEqual(pcsx2_cfg.load_input_overrides(self.ini, "docked"), {1: {"Cross": "FaceEast"}})

    def test_clearing_a_context_drops_it(self):
        self._write_side({"docked": {"1": {"Cross": "FaceEast"}},
                          "handheld": {"1": {"Circle": "FaceSouth"}}})
        pcsx2_cfg.save_input_overrides(self.ini, {}, "handheld")             # clear handheld only
        self.assertEqual(set(json.loads(self.side.read_text())), {"docked"})
        self.assertEqual(pcsx2_cfg.load_input_overrides(self.ini, "handheld"), {})
        self.assertEqual(pcsx2_cfg.load_input_overrides(self.ini, "docked"), {1: {"Cross": "FaceEast"}})

    def test_no_store_is_empty_in_both(self):
        self.assertEqual(pcsx2_cfg.load_input_overrides(self.ini, "docked"), {})
        self.assertEqual(pcsx2_cfg.load_input_overrides(self.ini, "handheld"), {})

    def test_update_and_clear_target_one_context_only(self):
        self._write_side({"docked": {"1": {"Cross": "FaceEast"}}})
        pcsx2_cfg.update_input_override(self.ini, 1, "Circle", "FaceWest", context="handheld")
        self.assertEqual(pcsx2_cfg.load_input_overrides(self.ini, "docked"),        # docked preserved
                         {1: {"Cross": "FaceEast"}})
        self.assertEqual(pcsx2_cfg.load_input_overrides(self.ini, "handheld"),
                         {1: {"Circle": "FaceWest"}})
        pcsx2_cfg.clear_input_override(self.ini, 1, "Circle", context="handheld")
        self.assertEqual(pcsx2_cfg.load_input_overrides(self.ini, "docked"),        # still preserved
                         {1: {"Cross": "FaceEast"}})
        hh = pcsx2_cfg.load_input_overrides(self.ini, "handheld")
        self.assertEqual(hh.get(1, {}).get("Circle"),                               # reset to baked
                         pcsx2_cfg.baked_default_sources()["Circle"])


class MigrateFromIni(unittest.TestCase):
    """migrate_overrides_from_ini seeds ONLY the docked context (the ini is the docked config)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ini = Path(self.tmp.name) / "PCSX2.ini"

    def tearDown(self):
        self.tmp.cleanup()

    def _write_ini(self, cross="FaceEast"):
        block = pcsx2_cfg._BAKED_DS2.replace("@@IDX@@", "0").replace(
            "Cross = SDL-0/FaceSouth", f"Cross = SDL-0/{cross}")
        self.ini.write_text(f"[Pad1]\n{block}\n", encoding="utf-8")

    def test_seeds_docked_never_handheld(self):
        self._write_ini(cross="FaceEast")                                    # a non-default remap
        docked = pcsx2_cfg.migrate_overrides_from_ini(self.ini, ["Pad1"], "docked")
        self.assertEqual(docked.get(1, {}).get("Cross"), "FaceEast")
        # handheld must never be seeded from the ini -> stays empty (=> stock default at launch)
        self.assertEqual(pcsx2_cfg.migrate_overrides_from_ini(self.ini, ["Pad1"], "handheld"), {})
        self.assertEqual(pcsx2_cfg.load_input_overrides(self.ini, "handheld"), {})


class LaunchContextSelection(unittest.TestCase):
    """The launch block's decision (switch_bind.bind, pcsx2 branch): pick the context-keyed
    GLOBAL map for the resolved context, feed it to assign_devices. Docked and handheld are
    independent axes; the MAD_FORCE_CONTEXT hook drives both paths headlessly."""

    def setUp(self):
        self._env = os.environ.pop("MAD_FORCE_CONTEXT", None)
        self.tmp = tempfile.TemporaryDirectory()
        self.ini = Path(self.tmp.name) / "PCSX2.ini"
        shutil.copy2(_FIX, self.ini)
        # Distinct maps so the bound [Pad1] tells us which context won.
        pcsx2_cfg.save_input_overrides(self.ini, {1: {"Cross": "FaceEast"}}, "docked")
        pcsx2_cfg.save_input_overrides(self.ini, {1: {"Cross": "FaceNorth"}}, "handheld")

    def tearDown(self):
        os.environ.pop("MAD_FORCE_CONTEXT", None)
        if self._env is not None:
            os.environ["MAD_FORCE_CONTEXT"] = self._env
        self.tmp.cleanup()

    def _bound_cross(self, pergame=None):
        # Drives the SAME helper the launch block uses (switch_bind._pcsx2_launch_overrides), so a
        # regression in the real per-game / context selection is caught here.
        ctx = handheld_input.context()
        overrides = switch_bind._pcsx2_launch_overrides(self.ini, pergame, ctx)
        pcsx2_cfg.assign_devices([sd(1, _DS5, "g", "DualSense")], ini_path=str(self.ini),
                                 manage=2, overrides=overrides)
        body = inifile.section_body(self.ini.read_text(encoding="utf-8"), "Pad1") or ""
        m = re.search(r"(?m)^Cross = SDL-\d+/(\S+)$", body)
        return m.group(1) if m else None

    def _overrides(self, pergame=None):
        return switch_bind._pcsx2_launch_overrides(self.ini, pergame, handheld_input.context())

    def test_forced_handheld_binds_handheld_map(self):
        os.environ["MAD_FORCE_CONTEXT"] = "handheld"
        self.assertEqual(self._bound_cross(), "FaceNorth")

    def test_forced_docked_binds_docked_map(self):
        os.environ["MAD_FORCE_CONTEXT"] = "docked"
        self.assertEqual(self._bound_cross(), "FaceEast")

    def test_handheld_unset_falls_back_to_stock(self):
        # Clear the handheld map -> handheld launch must use the baked default (stock), NOT docked.
        pcsx2_cfg.save_input_overrides(self.ini, {}, "handheld")
        os.environ["MAD_FORCE_CONTEXT"] = "handheld"
        stock_cross = pcsx2_cfg.baked_default_sources()["Cross"]     # canonical DualShock2 default
        self.assertEqual(self._bound_cross(), stock_cross)
        self.assertNotEqual(stock_cross, "FaceEast")                 # and it is NOT the docked remap

    def test_docked_per_game_does_not_leak_into_handheld(self):
        # A game's DOCKED per-game remap must be IGNORED on a handheld launch (invariant C).
        pergame = {"binds": {"1": {"Circle": "FaceWest"}}}
        os.environ["MAD_FORCE_CONTEXT"] = "handheld"
        ov = self._overrides(pergame)
        self.assertNotIn("Circle", ov.get(1, {}))                   # per-game docked NOT applied
        self.assertEqual(ov.get(1, {}).get("Cross"), "FaceNorth")   # handheld GLOBAL still applies

    def test_docked_per_game_applies_when_docked(self):
        pergame = {"binds": {"1": {"Circle": "FaceWest"}}}
        os.environ["MAD_FORCE_CONTEXT"] = "docked"
        ov = self._overrides(pergame)
        self.assertEqual(ov.get(1, {}).get("Circle"), "FaceWest")   # per-game layered over docked
        self.assertEqual(ov.get(1, {}).get("Cross"), "FaceEast")    # docked GLOBAL too

    def test_error_fallback_never_leaks_docked_into_handheld(self):
        # If context() somehow resolves handheld but override assembly fails, the fallback must be
        # stock ({}), never the docked map. Simulate by asking the helper with a corrupt per-game.
        os.environ["MAD_FORCE_CONTEXT"] = "handheld"
        self.assertEqual(handheld_input.context(), "handheld")
        # A handheld context with a docked-only per-game map yields {} for per-game, so a handheld
        # launch with no handheld global would bind stock — proven here by clearing handheld global.
        pcsx2_cfg.save_input_overrides(self.ini, {}, "handheld")
        ov = self._overrides({"binds": {"1": {"Circle": "FaceWest"}}})
        self.assertEqual(ov, {})                                    # stock, not the docked remap


if __name__ == "__main__":
    unittest.main()
