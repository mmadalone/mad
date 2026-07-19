"""mugen_res - on-the-go handheld resolution downshift.

CI-safe: effective() is tested with a mocked policy + handheld state; the apply/restore
mechanics run on a temp config.ini (aspect-preserving downshift, snapshot/restore round-trip,
byte-preservation, and the crash-sweep).
"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lib import mugen_res as R
from lib.madsrv import cfgutil

_INI = """\
; header
[Video]
GameWidth               = 1280
GameHeight               = 720
Fullscreen              = 1
[Sound]
MasterVolume         = 100
"""


def _pol(enabled=True, general=None, pergame=None):
    p = {"handheld": {"enabled": enabled}}
    if general is not None:
        p["systems"] = {"mugen": {"handheld": {"res": general}}}
    if pergame is not None:
        p["backends"] = {"mugen": {"pergame": pergame}}
    return p


class MugenRes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ini = Path(self.tmp.name) / "config.ini"
        self.ini.write_text(_INI)

    def tearDown(self):
        self.tmp.cleanup()

    def _gwh(self):
        t = self.ini.read_text()
        return cfgutil.ini_read(t, "Video", "GameWidth"), cfgutil.ini_read(t, "Video", "GameHeight")

    # -- effective() logic --------------------------------------------------
    def test_feature_off_is_100(self):
        with mock.patch.object(R, "load_merged", return_value=_pol(enabled=False, general="low")):
            self.assertEqual(R.effective("AvX"), 100)

    def test_docked_is_100(self):
        with mock.patch.object(R, "load_merged", return_value=_pol(general="low")), \
             mock.patch.object(R.deck_state, "is_handheld", return_value=False):
            self.assertEqual(R.effective("AvX"), 100)

    def test_handheld_general(self):
        with mock.patch.object(R, "load_merged", return_value=_pol(general="medium")), \
             mock.patch.object(R.deck_state, "is_handheld", return_value=True):
            self.assertEqual(R.effective("AvX"), 65)

    def test_pergame_overrides_general(self):
        pol = _pol(general="medium", pergame={"AvX": {"hhres": "low"}})
        with mock.patch.object(R, "load_merged", return_value=pol), \
             mock.patch.object(R.deck_state, "is_handheld", return_value=True):
            self.assertEqual(R.effective("AvX"), 50)      # per-game low wins
            self.assertEqual(R.effective("Other"), 65)    # falls back to general medium

    # -- apply / restore mechanics -----------------------------------------
    def test_apply_restore_byte_preserving(self):
        with mock.patch.object(R, "effective", return_value=70):
            self.assertIn("downshift", R.apply("AvX", self.ini))
        self.assertEqual(self._gwh(), ("896", "504"))          # 16:9 preserved
        self.assertTrue((self.ini.parent / R._SIDE).is_file())
        self.assertIn("restored", R.restore(self.ini))
        self.assertEqual(self._gwh(), ("1280", "720"))
        self.assertFalse((self.ini.parent / R._SIDE).is_file())
        self.assertEqual(self.ini.read_text(), _INI)           # full cycle byte-identical

    def test_apply_noop_when_full(self):
        with mock.patch.object(R, "effective", return_value=100):
            self.assertIn("no downshift", R.apply("AvX", self.ini))
        self.assertEqual(self._gwh(), ("1280", "720"))
        self.assertFalse((self.ini.parent / R._SIDE).is_file())

    # -- scale_dims / resting_dims (the picker-label helpers) ---------------
    def test_scale_dims_even_aspect_preserved(self):
        self.assertEqual(R.scale_dims(1280, 720, 100), (1280, 720))   # full = exact
        self.assertEqual(R.scale_dims(1280, 720, 80), (1024, 576))
        self.assertEqual(R.scale_dims(1280, 720, 65), (832, 468))
        self.assertEqual(R.scale_dims(1280, 720, 50), (640, 360))
        w, h = R.scale_dims(1920, 1080, 65)                           # a 1080p game differs
        self.assertEqual((w, h), (1248, 702))
        self.assertEqual((w % 2, h % 2), (0, 0))                      # always even

    def test_scale_dims_matches_what_apply_writes(self):
        with mock.patch.object(R, "effective", return_value=65):
            R.apply("AvX", self.ini)
        self.assertEqual(self._gwh(), tuple(str(v) for v in R.scale_dims(1280, 720, 65)))

    def test_resting_dims_reads_config(self):
        self.assertEqual(R.resting_dims(self.ini), (1280, 720))
        self.assertIsNone(R.resting_dims(self.ini.parent / "nope.ini"))

    def test_crash_sweep_restores_before_next_downshift(self):
        with mock.patch.object(R, "effective", return_value=50):
            R.apply("AvX", self.ini)                            # 640x360, sidecar left
        self.assertEqual(self._gwh(), ("640", "360"))
        # next launch, docked: apply() sweeps the orphan -> resting restored, no new downshift
        with mock.patch.object(R, "effective", return_value=100):
            R.apply("AvX", self.ini)
        self.assertEqual(self._gwh(), ("1280", "720"))
        self.assertFalse((self.ini.parent / R._SIDE).is_file())


if __name__ == "__main__":
    unittest.main()
