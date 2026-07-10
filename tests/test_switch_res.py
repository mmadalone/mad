"""On-the-go internal-resolution rail for PS2/PS3 (lib/switch_bind.py res transient).

Byte-stable apply/revert (PCSX2 ini + RPCS3 yaml), native/2x/inherit targets, only-ever-LOWER,
the revert-if-unchanged guard, and per-game file resolution. Temp configs + MAD_FORCE_CONTEXT;
no hardware. Run: python3 -m unittest tests.test_switch_res -v
"""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lib import switch_bind
from lib.madsrv import cfgutil

PCSX2 = "[EmuCore]\r\nEnableCheats = false\r\n[EmuCore/GS]\r\n# noise\r\nupscale_multiplier = 3\r\nAspectRatio = Auto 4:3\r\n"
RPCS3 = "Video:\n  Renderer: Vulkan\n  Resolution Scale: 200\n  VSync: false\nAudio:\n  x: 1\n"


def _pol(sys, res="native", enabled=True):
    return {"handheld": {"enabled": True},
            "systems": {sys: {"handheld": {"enabled": enabled, "res": res}}}}


class SwitchRes(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.res_dir = self.d / "res"
        self._p_dir = mock.patch.object(switch_bind, "_RES_DIR", self.res_dir)
        self._p_dir.start()
        os.environ["MAD_FORCE_CONTEXT"] = "handheld"

    def tearDown(self):
        self._p_dir.stop()
        os.environ.pop("MAD_FORCE_CONTEXT", None)
        shutil.rmtree(self.d, ignore_errors=True)

    def _apply(self, emu, cfgfile, policy):
        with mock.patch("lib.policy.load_merged", lambda: policy), \
             mock.patch.object(switch_bind, "_res_config_file", lambda e, r: cfgfile):
            switch_bind._res_apply(emu, "dummy.iso")

    def _ini(self, f, k="upscale_multiplier"):
        return cfgutil.ini_read(cfgutil.read_text(f), "EmuCore/GS", k)

    def _markers(self):
        return list(self.res_dir.glob("*.json"))

    def test_pcsx2_byte_stable(self):
        f = self.d / "PCSX2.ini"
        f.write_bytes(PCSX2.encode())
        before = f.read_bytes()
        self._apply("pcsx2", f, _pol("ps2", "native"))
        self.assertEqual(self._ini(f), "1")
        switch_bind._res_sweep_all()
        self.assertEqual(f.read_bytes(), before)          # byte-identical: CRLF + comment preserved
        self.assertFalse(self._markers())

    def test_rpcs3_yaml_byte_stable(self):
        f = self.d / "config.yml"
        f.write_bytes(RPCS3.encode())
        before = f.read_bytes()
        self._apply("rpcs3", f, _pol("ps3", "native"))
        self.assertEqual(cfgutil.yaml_read(cfgutil.read_text(f), "Video", "Resolution Scale"), "100")
        switch_bind._res_sweep_all()
        self.assertEqual(f.read_bytes(), before)

    def test_2x_and_inherit_and_only_lower(self):
        f = self.d / "PCSX2.ini"
        f.write_text("[EmuCore/GS]\nupscale_multiplier = 4\n")
        self._apply("pcsx2", f, _pol("ps2", "2x"))
        self.assertEqual(self._ini(f), "2")               # 2x target
        switch_bind._res_sweep_all()
        f.write_text("[EmuCore/GS]\nupscale_multiplier = 3\n")
        self._apply("pcsx2", f, _pol("ps2", "inherit"))
        self.assertEqual(self._ini(f), "3")               # inherit = untouched
        self.assertFalse(self._markers())
        f.write_text("[EmuCore/GS]\nupscale_multiplier = 1\n")
        self._apply("pcsx2", f, _pol("ps2", "native"))
        self.assertEqual(self._ini(f), "1")               # already native -> no downshift
        self.assertFalse(self._markers())

    def test_revert_if_unchanged_guard(self):
        f = self.d / "PCSX2.ini"
        f.write_text("[EmuCore/GS]\nupscale_multiplier = 3\n")
        self._apply("pcsx2", f, _pol("ps2", "native"))    # -> 1 + marker
        cfgutil.atomic_write(f, cfgutil.ini_replace(cfgutil.read_text(f), "EmuCore/GS",
                                                    "upscale_multiplier", "5"))  # user changes in-session
        switch_bind._res_sweep_all()
        self.assertEqual(self._ini(f), "5")               # user edit preserved (not reverted to 3)
        self.assertFalse(self._markers())

    def test_docked_noop(self):
        os.environ["MAD_FORCE_CONTEXT"] = "docked"
        f = self.d / "PCSX2.ini"
        f.write_text("[EmuCore/GS]\nupscale_multiplier = 3\n")
        self._apply("pcsx2", f, _pol("ps2", "native"))
        self.assertEqual(self._ini(f), "3")
        self.assertFalse(self._markers())

    def test_pcsx2_pergame_file_resolution(self):
        gs = self.d / "gamesettings"
        gs.mkdir()
        pg = gs / "SLUS-123_ABC.ini"
        pg.write_text("[EmuCore/GS]\nupscale_multiplier = 4\n")
        with mock.patch("lib.madsrv.pcsx2_games.path_to_key", lambda r: "SLUS-123_ABC"), \
             mock.patch("lib.madsrv.pcsx2_pergame_cmds._GS_DIR", gs):
            self.assertEqual(switch_bind._pcsx2_res_file("x.iso"), pg)     # per-game (sets the key)
        pg.write_text("[Core]\nx = 1\n")                                    # per-game exists but no res key
        glob = self.d / "global.ini"
        with mock.patch("lib.madsrv.pcsx2_games.path_to_key", lambda r: "SLUS-123_ABC"), \
             mock.patch("lib.madsrv.pcsx2_pergame_cmds._GS_DIR", gs), \
             mock.patch.object(switch_bind, "_PCSX2_INI", glob):
            self.assertEqual(switch_bind._pcsx2_res_file("x.iso"), glob)   # falls back to global

    def test_crash_orphan_self_heal(self):
        # a marker left by a crashed launch is reverted on the next sweep even with no policy
        f = self.d / "PCSX2.ini"
        f.write_text("[EmuCore/GS]\nupscale_multiplier = 1\n")             # currently at the applied 'low'
        self.res_dir.mkdir(parents=True, exist_ok=True)
        (self.res_dir / "m.json").write_text(
            '{"path": "%s", "fmt": "ini", "section": "EmuCore/GS", "key": "upscale_multiplier", "prev": "3", "low": "1"}'
            % f)
        switch_bind._res_sweep_all()
        self.assertEqual(self._ini(f), "3")
        self.assertFalse(self._markers())


if __name__ == "__main__":
    unittest.main()
