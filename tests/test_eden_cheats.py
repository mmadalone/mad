"""eden_cheats.* — enumerate [Cheat Name] headers from load/<TID>/<mod>/cheats/<buildid>.txt,
resolve the build-id key against existing [DisabledCheats] entries (Eden stores a full 40-hex
build id lowercase; the cheat file is the 16-hex truncation), toggle in the array, empty state.
Mirrors tests/test_citron_cheats.py for Eden (eden_cheats_cmds / eden_cheats)."""
import shutil
import tempfile
import unittest
from pathlib import Path

from lib import proc_guard
from lib.madsrv import eden_cheats_cmds as cc
from lib.madsrv import rpc

_TID = "0100F2C0115B6000"
_BID16 = "1234567890abcdef"                                  # cheat-file basename (u64 truncation)
_FULL = _BID16 + "aabbccddeeff001122334455" + "0" * 24       # 40 sig hex + 24 zeros = a real config key


class EdenCheats(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.ini = self.d / "qt-config.ini"
        self.ini.write_text("[System]\nuse_docked_mode=1\n", newline="")
        self.load = self.d / "load"
        cheatdir = self.load / _TID / "MyMod" / "cheats"
        cheatdir.mkdir(parents=True)
        (cheatdir / f"{_BID16}.txt").write_text(
            "[Infinite HP]\n040000000 1\n[Max Money]\n040000001 2\n", encoding="utf-8")
        self._of, self._ol = cc._FILE, cc._LOAD
        cc._FILE, cc._LOAD = self.ini, self.load
        self._run = proc_guard.emulator_running
        proc_guard.emulator_running = lambda name: False
        import lib.staterev as sr
        self._bump = sr.bump
        sr.bump = lambda n: None

    def tearDown(self):
        cc._FILE, cc._LOAD = self._of, self._ol
        proc_guard.emulator_running = self._run
        import lib.staterev as sr
        sr.bump = self._bump
        shutil.rmtree(self.d, ignore_errors=True)

    def _rows(self, tid=_TID):
        return {s["label"]: s for s in
                rpc._METHODS["eden_cheats.get"][0]({"titleid": tid})["groups"][0]["settings"]}

    def _set(self, key, value, tid=_TID):
        return rpc._METHODS["eden_cheats.set"][0]({"titleid": tid, "key": key, "value": value})

    def test_registered(self):
        self.assertIn("eden_cheats.get", rpc._METHODS)
        self.assertIn("eden_cheats.set", rpc._METHODS)

    def test_enumerate_headers(self):
        rows = self._rows()
        self.assertEqual(set(rows), {"Infinite HP", "Max Money"})
        self.assertTrue(rows["Infinite HP"]["value"])       # enabled by default

    def test_disable_toggle_round_trip(self):
        key = self._rows()["Infinite HP"]["key"]
        self._set(key, False)
        self.assertFalse(self._rows()["Infinite HP"]["value"])   # now shows disabled (key matched!)
        bid = key.split(":", 2)[1]
        self.assertEqual(cc._parse(self.ini.read_text(newline=""))[bid], ["Infinite HP"])
        self._set(key, True)
        self.assertTrue(self._rows()["Infinite HP"]["value"])

    def test_key_is_lowercase_padded_when_no_config(self):
        key = self._rows()["Infinite HP"]["key"]
        bid = key.split(":", 2)[1]
        self.assertEqual(bid, (_BID16 + "0" * 48))               # lowercase, padded to 64
        self.assertEqual(bid, bid.lower())

    def test_prefix_match_existing_full_build_id(self):
        # a real [DisabledCheats] entry (full 40-hex + zeros) sharing the cheat file's 16-hex prefix
        self.ini.write_text(
            "[DisabledCheats]\nsize=1\n1\\build_id\\default=false\n"
            f"1\\build_id={_FULL}\n1\\disabled\\size=0\n", newline="")
        bid = self._rows()["Infinite HP"]["key"].split(":", 2)[1]
        self.assertEqual(bid, _FULL)                            # resolved to the config key, not padded

    def test_empty_state(self):
        p = rpc._METHODS["eden_cheats.get"][0]({"titleid": "0100152000022000"})
        self.assertEqual(p["groups"][0]["settings"], [])

    def test_round_trip(self):
        m = {_FULL: ["Infinite HP", "Max Money"]}
        self.assertEqual(cc._parse("[DisabledCheats]\n" + cc._serialize(m)), m)


if __name__ == "__main__":
    unittest.main()
