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
from lib.madsrv import pcsx2_pergame_input_cmds as pgin
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
    """The launch block's decision (switch_bind.bind, pcsx2 branch): resolve the context's
    input-PROFILE pick (per-game -> global -> none), inject its [PadN] bodies, then
    assign_devices. Docked and handheld are independent axes; MAD_FORCE_CONTEXT drives both
    paths headlessly. (The per-button override maps are PS2-legacy: no longer consumed.)"""

    def setUp(self):
        self._env = os.environ.pop("MAD_FORCE_CONTEXT", None)
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.ini = d / "inis" / "PCSX2.ini"
        self.ini.parent.mkdir()
        shutil.copy2(_FIX, self.ini)
        profs = d / "inputprofiles"
        profs.mkdir()
        # Distinct layouts so the bound [Pad1] tells us which context's profile won.
        (profs / "DockedProf.ini").write_text(
            "[Pad1]\nType = DualShock2\nCross = SDL-0/FaceEast\n", encoding="utf-8")
        (profs / "HandProf.ini").write_text(
            "[Pad1]\nType = DualShock2\nCross = SDL-0/FaceNorth\n", encoding="utf-8")
        self.cfg = {"config_file": str(self.ini),
                    "profile_docked": "DockedProf", "profile_handheld": "HandProf"}

    def tearDown(self):
        os.environ.pop("MAD_FORCE_CONTEXT", None)
        if self._env is not None:
            os.environ["MAD_FORCE_CONTEXT"] = self._env
        self.tmp.cleanup()

    def _bound_cross(self, pergame=None):
        # Drives the SAME pieces the launch block composes (pcsx2_profiles.resolve ->
        # profile_path -> pcsx2_cfg.apply_profile_bodies -> assign_devices), so a regression
        # in the real per-game / context selection is caught here.
        from lib import pcsx2_profiles
        ctx = handheld_input.context()
        stem = pcsx2_profiles.resolve(pergame, self.cfg, ctx)
        if stem:
            ppath = pcsx2_profiles.profile_path(pcsx2_profiles.profiles_dir(self.cfg), stem)
            if ppath is not None:
                pcsx2_cfg.apply_profile_bodies(self.ini, ppath, 1)
        pcsx2_cfg.assign_devices([sd(1, _DS5, "g", "DualSense")], ini_path=str(self.ini),
                                 manage=2, overrides=None)
        body = inifile.section_body(self.ini.read_text(encoding="utf-8"), "Pad1") or ""
        m = re.search(r"(?m)^Cross = SDL-\d+/(\S+)$", body)
        return m.group(1) if m else None

    def test_forced_handheld_binds_handheld_profile(self):
        os.environ["MAD_FORCE_CONTEXT"] = "handheld"
        self.assertEqual(self._bound_cross(), "FaceNorth")

    def test_forced_docked_binds_docked_profile(self):
        os.environ["MAD_FORCE_CONTEXT"] = "docked"
        self.assertEqual(self._bound_cross(), "FaceEast")

    def test_handheld_unset_falls_back_to_stock_never_docked(self):
        # No handheld pick anywhere -> the resting layout (baked default), NOT the docked profile.
        del self.cfg["profile_handheld"]
        os.environ["MAD_FORCE_CONTEXT"] = "handheld"
        stock_cross = pcsx2_cfg.baked_default_sources()["Cross"]     # canonical DualShock2 default
        self.assertEqual(self._bound_cross(), stock_cross)
        self.assertNotEqual(stock_cross, "FaceEast")                 # and it is NOT the docked pick

    def test_docked_per_game_does_not_leak_into_handheld(self):
        # A game's DOCKED profile pick must be IGNORED on a handheld launch (invariant C).
        del self.cfg["profile_handheld"]
        os.environ["MAD_FORCE_CONTEXT"] = "handheld"
        pergame = {"profiles": {"docked": "DockedProf"}}
        self.assertEqual(self._bound_cross(pergame),
                         pcsx2_cfg.baked_default_sources()["Cross"])

    def test_handheld_per_game_beats_handheld_global(self):
        os.environ["MAD_FORCE_CONTEXT"] = "handheld"
        pergame = {"profiles": {"handheld": "DockedProf"}}   # per-game handheld = the East layout
        self.assertEqual(self._bound_cross(pergame), "FaceEast")

    def test_missing_profile_file_falls_back_to_resting(self):
        self.cfg["profile_docked"] = "Ghost"                 # picked, then deleted on disk
        os.environ["MAD_FORCE_CONTEXT"] = "docked"
        self.assertEqual(self._bound_cross(), pcsx2_cfg.baked_default_sources()["Cross"])

    def test_legacy_override_helper_is_gone(self):
        # Regression guard: the per-button launch helper must not quietly return.
        self.assertFalse(hasattr(switch_bind, "_pcsx2_launch_overrides"))


class PerGameContext(unittest.TestCase):
    """The per-game store's context axes after the profile switch: profile picks are
    context-keyed and independent; LEGACY binds stay preserved-but-inert."""

    TID = "SLUS-21665_BBE4D862"

    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self._st = pgin._STORE
        pgin._STORE = self.d / "pergame-input.json"

    def tearDown(self):
        pgin._STORE = self._st

    def test_profile_slices_are_independent(self):
        from lib import pcsx2_profiles
        pgin._save({self.TID: {"profiles": {"docked": "A", "handheld": "B"}}})
        e = pgin.load_entry(self.TID)
        self.assertEqual(pcsx2_profiles.pergame_profile(e, "docked"), "A")
        self.assertEqual(pcsx2_profiles.pergame_profile(e, "handheld"), "B")

    def test_docked_only_pick_never_leaks_to_handheld(self):
        from lib import pcsx2_profiles
        pgin._save({self.TID: {"profiles": {"docked": "A"}}})
        e = pgin.load_entry(self.TID)
        self.assertIsNone(pcsx2_profiles.pergame_profile(e, "handheld"))

    def test_legacy_flat_binds_preserved_but_inert(self):
        pgin._save({self.TID: {"binds": {"1": {"Cross": "FaceEast"}}}})       # pre-handheld flat store
        e = pgin.load_entry(self.TID)
        self.assertIsNotNone(e)                              # kept (never destroy user data)
        self.assertFalse(pgin._has_input_override(e))        # but inert -> no badge


if __name__ == "__main__":
    unittest.main()
