"""Per-game Dolphin settings (dolphin_pergame_cmds): inherit-aware writes to GameSettings/<ID>.ini.

Verifies: presence-of-key override model (absent = inherit); the real->GameINI section translation
([Video_Settings]/[Video_Enhancements]/...); bool 3-way + enum index-0 inherit + numeric inherit; the
AA composite; that picking Inherit removes the key (+ empties the section); running-guard; exists:true.

Run:  python3 -m unittest tests.test_dolphin_pergame -v
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from lib import dolphin_gameids as gids
from lib import proc_guard
from lib.madsrv import cfgutil
from lib.madsrv import dolphin_pergame_cmds as pg
from lib.madsrv.rpc import RpcError


class PerGame(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._save = (gids._USER_GS, proc_guard.emulator_running)
        gids._USER_GS = self.tmp
        proc_guard.emulator_running = lambda *a, **k: False
        self.gid = "TEST01"

    def tearDown(self):
        gids._USER_GS, proc_guard.emulator_running = self._save
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _text(self) -> str:
        f = self.tmp / f"{self.gid}.ini"
        return f.read_text() if f.is_file() else ""

    def _set(self, groups, key, value):
        return pg._do_set(self.gid, groups, {"key": key, "value": value})

    def test_exists_true_and_inherit_when_empty(self):
        r = pg._do_get(self.gid, pg._GENERAL)
        self.assertTrue(r["exists"])
        for g in r["groups"]:
            for s in g["settings"]:
                if s["type"] == "enum":
                    self.assertEqual((s["value"], s["options"][0]), (0, "Inherit global"))

    def test_bool_inherit_off_on(self):
        self._set(pg._GENERAL, "CPUThread", 2)
        self.assertEqual(cfgutil.ini_read(self._text(), "Core", "CPUThread"), "True")
        self._set(pg._GENERAL, "CPUThread", 1)
        self.assertEqual(cfgutil.ini_read(self._text(), "Core", "CPUThread"), "False")
        self._set(pg._GENERAL, "CPUThread", 0)                        # Inherit -> removed
        self.assertIsNone(cfgutil.ini_read(self._text(), "Core", "CPUThread"))

    def test_enum_index_and_section_translation(self):
        # InternalResolution: GFX [Settings] -> per-game [Video_Settings]; display idx 2 = Native ("1")
        self._set(pg._GFX_ENH, "InternalResolution", 2)
        self.assertEqual(cfgutil.ini_read(self._text(), "Video_Settings", "InternalResolution"), "1")
        # an Enhancements bool -> [Video_Enhancements]
        self._set(pg._GFX_ENH, "ArbitraryMipmapDetection", 2)
        self.assertEqual(cfgutil.ini_read(self._text(), "Video_Enhancements",
                                          "ArbitraryMipmapDetection"), "True")
        # a Hacks bool -> [Video_Hacks]
        self._set(pg._GFX_HACKS, "EFBToTextureEnable", 1)
        self.assertEqual(cfgutil.ini_read(self._text(), "Video_Hacks", "EFBToTextureEnable"), "False")

    def test_enum_inherit_removes_and_empties_section(self):
        self._set(pg._GFX_ENH, "InternalResolution", 2)
        self._set(pg._GFX_ENH, "InternalResolution", 0)              # Inherit
        self.assertIsNone(cfgutil.ini_read(self._text(), "Video_Settings", "InternalResolution"))
        self.assertIsNone(cfgutil._ini_span(self._text(), "Video_Settings"))   # emptied section dropped

    def test_aa_composite(self):
        self._set(pg._GFX_ENH, "_aa", 2)                             # 2x MSAA
        t = self._text()
        self.assertEqual(cfgutil.ini_read(t, "Video_Settings", "MSAA"), "0x00000002")
        self.assertEqual(cfgutil.ini_read(t, "Video_Settings", "SSAA"), "False")
        self._set(pg._GFX_ENH, "_aa", 0)                             # Inherit -> both removed
        self.assertIsNone(cfgutil.ini_read(self._text(), "Video_Settings", "MSAA"))
        self.assertIsNone(cfgutil.ini_read(self._text(), "Video_Settings", "SSAA"))

    def test_int_inherit_and_override(self):
        self._set(pg._GENERAL, "Volume", 50)
        self.assertEqual(cfgutil.ini_read(self._text(), "DSP", "Volume"), "50")
        r = pg._do_get(self.gid, pg._GENERAL)
        vol = next(s for g in r["groups"] for s in g["settings"] if s["key"] == "Volume")
        self.assertEqual((vol["value"], vol["inherited"]), (50, False))
        self._set(pg._GENERAL, "Volume", "inherit")
        self.assertIsNone(cfgutil.ini_read(self._text(), "DSP", "Volume"))

    def test_maxanisotropy_updates_existing_section(self):
        # pre-seeded in the SECONDARY candidate section ([Video_Hardware], as an older Dolphin build
        # wrote): a set must UPDATE it there, not create a duplicate in the primary [Video_Enhancements].
        (self.tmp / f"{self.gid}.ini").write_text("[Video_Hardware]\nMaxAnisotropy = 2\n")
        self._set(pg._GFX_ENH, "MaxAnisotropy", 2)                   # display idx 2 (1x) -> stored "0"
        t = self._text()
        self.assertEqual(cfgutil.ini_read(t, "Video_Hardware", "MaxAnisotropy"), "0")
        self.assertIsNone(cfgutil.ini_read(t, "Video_Enhancements", "MaxAnisotropy"))   # no duplicate

    def test_synthetic_enum_slot_is_noop(self):
        # an out-of-curated on-disk value shows a synthetic "(current: X)" slot; re-selecting it must
        # be a harmless no-op, not EINVAL.
        (self.tmp / f"{self.gid}.ini").write_text("[Video_Settings]\nInternalResolution = 99\n")
        r = pg._do_get(self.gid, pg._GFX_ENH)
        ir = next(s for g in r["groups"] for s in g["settings"] if s["key"] == "InternalResolution")
        self.assertTrue(ir["options"][-1].startswith("(current"))
        pg._do_set(self.gid, pg._GFX_ENH, {"key": "InternalResolution", "value": len(ir["options"]) - 1})
        self.assertEqual(cfgutil.ini_read(self._text(), "Video_Settings", "InternalResolution"), "99")

    def test_running_guard(self):
        proc_guard.emulator_running = lambda *a, **k: True
        with self.assertRaises(RpcError) as cm:
            self._set(pg._GENERAL, "CPUThread", 2)
        self.assertEqual(cm.exception.code, "EBUSY")

    def test_bad_gameid_rejected(self):
        with self.assertRaises(RpcError):
            pg._tid({"titleid": "not-an-id"})

    def test_interface_keys_dropped(self):
        # [Interface] is NOT a per-game section: ConfirmStop must not appear in any per-game page.
        keys = {it["key"] for g in pg._GENERAL for it in g["items"]}
        self.assertNotIn("ConfirmStop", keys)
        self.assertIn("CPUThread", keys)


if __name__ == "__main__":
    unittest.main()
