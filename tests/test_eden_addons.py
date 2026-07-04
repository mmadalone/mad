"""eden_addons.* — the [DisabledAddOns] counted-array editor: parse the (second-pass-ordered)
array by key name, enumerate available mods from load/<HEX>/ unioned with the persistent disabled
list, toggle enable/disable, and re-serialize preserving OTHER titles + correct 1-based sizes."""
import shutil
import tempfile
import unittest
from pathlib import Path

from lib import proc_guard
from lib.madsrv import cfgutil
from lib.madsrv import eden_addons_cmds as ac
from lib.madsrv import rpc

_A = "0100000000010000"          # Odyssey
_B = "0100152000022000"          # Mario Kart
_DA, _DB = str(int(_A, 16)), str(int(_B, 16))

# [DisabledAddOns] with the \d entries DEFERRED (as the live file writes them) to prove the parser
# reads by key name, not by line order. Plus a [System] section to prove it's preserved.
FIX = (
    "[System]\nuse_docked_mode=1\n\n"
    "[DisabledAddOns]\n"
    "size=2\n"
    f"1\\title_id\\default=false\n1\\title_id={_DA}\n1\\disabled\\size=1\n"
    f"2\\title_id\\default=false\n2\\title_id={_DB}\n2\\disabled\\size=0\n"
    '1\\disabled\\1\\d\\default=false\n1\\disabled\\1\\d="Old Mod/Sub Option"\n'
)


class EdenAddons(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.ini = self.d / "qt-config.ini"
        self.ini.write_text(FIX, newline="")
        self.load = self.d / "load"
        (self.load / _A / "60 FPS" / "exefs").mkdir(parents=True)
        (self.load / _A / "TOTK Optimizer" / "romfs").mkdir(parents=True)
        self._of, self._ol = ac._FILE, ac._LOAD
        ac._FILE, ac._LOAD = self.ini, self.load
        self._run = proc_guard.emulator_running
        proc_guard.emulator_running = lambda name: False
        import lib.staterev as sr
        self._bump = sr.bump
        sr.bump = lambda n: None

    def tearDown(self):
        ac._FILE, ac._LOAD = self._of, self._ol
        proc_guard.emulator_running = self._run
        import lib.staterev as sr
        sr.bump = self._bump
        shutil.rmtree(self.d, ignore_errors=True)

    def _get(self, tid):
        return rpc._METHODS["eden_addons.get"][0]({"titleid": tid})

    def _set(self, tid, key, value):
        return rpc._METHODS["eden_addons.set"][0]({"titleid": tid, "key": key, "value": value})

    def _model(self):
        return ac._parse(self.ini.read_text(newline=""))

    def test_registered(self):
        self.assertIn("eden_addons.get", rpc._METHODS)
        self.assertIn("eden_addons.set", rpc._METHODS)

    def test_parse_reads_deferred_entries(self):
        m = self._model()
        self.assertEqual(m[_DA], ["Old Mod/Sub Option"])   # read despite deferred \d order
        self.assertEqual(m[_DB], [])

    def test_get_union_of_mods_and_disabled(self):
        rows = {s["label"]: s for s in self._get(_A)["groups"][0]["settings"]}
        # available mods (enabled) + the persistent disabled entry
        self.assertIn("60 FPS", rows)
        self.assertIn("TOTK Optimizer", rows)
        self.assertTrue(rows["60 FPS"]["value"])           # enabled
        self.assertIn("Old Mod/Sub Option", rows)
        self.assertFalse(rows["Old Mod/Sub Option"]["value"])   # disabled

    def test_disable_adds_and_preserves_other_title(self):
        self._set(_A, "addon:60 FPS", False)
        m = self._model()
        self.assertIn("60 FPS", m[_DA])
        self.assertIn("Old Mod/Sub Option", m[_DA])         # existing disabled kept
        self.assertEqual(m[_DB], [])                        # other title untouched
        # sizes correct on disk
        self.assertEqual(cfgutil.ini_read(self.ini.read_text(newline=""),
                                          "DisabledAddOns", "1\\disabled\\size"), "2")

    def test_enable_removes(self):
        self._set(_A, "addon:Old Mod/Sub Option", True)
        self.assertNotIn("Old Mod/Sub Option", self._model()[_DA])

    def test_other_sections_preserved(self):
        self._set(_A, "addon:60 FPS", False)
        self.assertEqual(cfgutil.ini_read(self.ini.read_text(newline=""),
                                          "System", "use_docked_mode"), "1")

    def test_serialize_round_trips(self):
        m = self._model()
        m[_DA].append("Another Mod")
        again = ac._parse("[DisabledAddOns]\n" + ac._serialize(m))
        self.assertEqual(again, m)


if __name__ == "__main__":
    unittest.main()
