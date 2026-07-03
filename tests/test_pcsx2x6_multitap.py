"""preserve_multitap: the Namco tiles (pcsx2x6 / ps2guncon) let the user own [Pad]
MultitapPort1/2 via the Global settings page, so the launch router must NOT derive+overwrite
it. Standard PCSX2 still derives multitap from the pad count (guarded by the pad goldens)."""
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from lib import pcsx2_cfg, switch_bind


def _dev(i):
    return SimpleNamespace(index=i, vidpid="054c:0ce6", name="DualSense", guid="g")


def _mt(ini):
    t = ini.read_text()
    return (bool(re.search(r'(?m)^\s*MultitapPort1\s*=\s*true\b', t)),
            bool(re.search(r'(?m)^\s*MultitapPort2\s*=\s*true\b', t)))


BASE = ("[Pad]\nMultitapPort1 = true\nMultitapPort2 = false\n\n"
        "[Pad1]\nType = DualShock2\nCross = SDL-0/Cross\n\n[Pad2]\nType = None\n")


class PreserveMultitap(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.ini = self.d / "PCSX2.ini"
        self.ini.write_text(BASE)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_preserve_keeps_user_multitap_and_still_binds(self):
        res = pcsx2_cfg.assign_devices([_dev(3), _dev(4)], ini_path=str(self.ini),
                                       manage=2, overrides={}, preserve_multitap=True)
        self.assertEqual(_mt(self.ini), (True, False))         # user's value survived the bind
        self.assertIn("DualShock2", self.ini.read_text())      # Pad1 still bound
        self.assertEqual(res["multitap"], (True, False))       # summary logs the preserved state

    def test_default_derives_multitap(self):
        pcsx2_cfg.assign_devices([_dev(3), _dev(4)], ini_path=str(self.ini),
                                 manage=2, overrides={})       # preserve_multitap defaults False
        self.assertEqual(_mt(self.ini), (False, False))        # standard PCSX2: derived for 2 pads

    def test_switch_bind_pcsx2x6_branch_preserves(self):
        # the real launch path for the arcade tile must not clobber the user's multitap
        switch_bind._write("pcsx2x6", self.ini, [_dev(3), _dev(4)])
        self.assertEqual(_mt(self.ini), (True, False))

    def test_switch_bind_ps2guncon_branch_preserves(self):
        switch_bind._write("ps2guncon", self.ini, [_dev(3), _dev(4)])
        self.assertEqual(_mt(self.ini), (True, False))


if __name__ == "__main__":
    unittest.main()
