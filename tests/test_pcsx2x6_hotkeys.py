"""pcsx2x6 per-member Hotkeys: buffered X=Save / Y=Cancel editor. Reuses the pcsx2hk logic
pointed at each fork ini + the pcsx2x6 process guard. Namespaces x6a_hk / x6r_hk.

A stage (input_set / input_clear) writes NOTHING to disk; input_save commits (once);
input_cancel reverts. KEYING landmine: arcade (x6a) and retail (x6r) write DIFFERENT inis and
own SEPARATE buffers, so staging one never touches the other.

Run:  python3 -m unittest tests.test_pcsx2x6_hotkeys -v
"""
import shutil
import tempfile
import unittest
from pathlib import Path

import evdev.ecodes as e

from lib import proc_guard
from lib.madsrv import cfgutil
from lib.madsrv import pcsx2x6_hotkeys_cmds as hk
from lib.madsrv import rpc

FIX = "[Hotkeys]\nToggleFullscreen = Keyboard/F11\nZoomIn = Keyboard/Plus\n"


class ForkHotkeys(unittest.TestCase):
    PFX = "x6a"

    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.ini = self.d / "PCSX2.ini"
        self.ini.write_text(FIX, newline="")
        self._orig = dict(hk._INIS)
        hk._INIS[self.PFX] = self.ini
        for b in hk._BUFS.values():
            b.reset()                   # fresh buffers per case (module-level singletons)
        self._run = proc_guard.emulator_running
        proc_guard.emulator_running = lambda name: False
        import lib.staterev as sr
        self._bump = sr.bump
        sr.bump = lambda n: None

    def tearDown(self):
        hk._INIS.clear()
        hk._INIS.update(self._orig)
        proc_guard.emulator_running = self._run
        import lib.staterev as sr
        sr.bump = self._bump
        for b in hk._BUFS.values():
            b.reset()
        shutil.rmtree(self.d, ignore_errors=True)

    def _call(self, verb, **params):
        return rpc._METHODS[f"{self.PFX}_hk.{verb}"][0](params)

    def _disk(self, key):
        return cfgutil.ini_read(self.ini.read_text(newline=""), "Hotkeys", key)

    def _read(self):
        return self.ini.read_text(newline="")

    def _row(self, payload, key):
        for g in payload["groups"]:
            for b in g["binds"]:
                if b["id"] == key:
                    return b["value"]
        return None

    # ── registration ─────────────────────────────────────────────────────────
    def test_registered_both_members(self):
        for pfx in ("x6a", "x6r"):
            for v in ("input_get", "input_set", "input_clear", "input_save", "input_cancel"):
                self.assertIn(f"{pfx}_hk.{v}", rpc._METHODS)

    def test_getters_not_config_cached(self):
        # buffered getters must NOT declare cache=("config",) — the buffer is the cache.
        for pfx in ("x6a", "x6r"):
            self.assertEqual(rpc._METHODS[f"{pfx}_hk.input_get"][2], ())

    def test_targets_fork_inis(self):
        self.assertTrue(str(self._orig["x6a"]).endswith("pcsx2x6/PCSX2x6/inis/PCSX2.ini"))
        self.assertTrue(str(self._orig["x6r"]).endswith("pcsx2x6-retail/PCSX2x6/inis/PCSX2.ini"))

    # ── render ────────────────────────────────────────────────────────────────
    def test_get_renders_actions_and_unknown(self):
        pay = self._call("input_get")
        self.assertIn("Navigation", [g["title"] for g in pay["groups"]])
        self.assertTrue(pay["clearable"])
        self.assertTrue(pay["buffered"])
        self.assertFalse(pay["dirty"])
        self.assertEqual(self._row(pay, "ToggleFullscreen"), "F11")   # known action, its binding
        self.assertEqual(self._row(pay, "ZoomIn"), "Plus")            # unknown live key preserved

    # ── buffered stage / save / cancel ───────────────────────────────────────
    def test_stage_leaves_disk_unchanged(self):
        r = self._call("input_set", id="TogglePause", codes=[e.KEY_SPACE])
        self.assertTrue(r["dirty"])
        self.assertEqual(self._read(), FIX)                          # DISK byte-identical
        self.assertTrue(self._call("input_get")["dirty"])            # get reports dirty over buffer

    def test_save_commits_once(self):
        self._call("input_set", id="TogglePause", codes=[e.KEY_SPACE])
        saved = self._call("input_save")
        self.assertTrue(saved["saved"])
        self.assertFalse(saved["dirty"])
        self.assertEqual(self._disk("TogglePause"), "Keyboard/Space")   # committed
        self.assertEqual(self._disk("ToggleFullscreen"), "Keyboard/F11")  # foreign key preserved
        self.assertEqual(self._disk("ZoomIn"), "Keyboard/Plus")           # unknown key preserved
        self.assertFalse(self._call("input_save")["saved"])               # nothing left to save

    def test_cancel_reverts(self):
        self._call("input_set", id="TogglePause", codes=[e.KEY_SPACE])
        self.assertEqual(self._read(), FIX)                          # unchanged while staged
        c = self._call("input_cancel")
        self.assertTrue(c["cancelled"])
        self.assertFalse(c["dirty"])
        self.assertEqual(self._read(), FIX)                          # discard leaves disk untouched
        self.assertFalse(self._call("input_get")["dirty"])

    def test_clear_stage_then_save(self):
        self._call("input_clear", id="ToggleFullscreen")
        self.assertEqual(self._disk("ToggleFullscreen"), "Keyboard/F11")  # staged, not removed
        self._call("input_save")
        self.assertIsNone(self._disk("ToggleFullscreen"))                 # removed on save
        self.assertEqual(self._disk("ZoomIn"), "Keyboard/Plus")           # sibling untouched

    # ── guards ────────────────────────────────────────────────────────────────
    def test_ebusy_guard_at_stage(self):
        proc_guard.emulator_running = lambda name: True
        with self.assertRaises(rpc.RpcError):
            self._call("input_set", id="TogglePause", codes=[e.KEY_SPACE])
        self.assertEqual(self._read(), FIX)

    def test_ebusy_guard_at_save(self):
        # stage while idle, emulator starts before save -> flush replay re-runs the guard.
        self._call("input_set", id="TogglePause", codes=[e.KEY_SPACE])
        proc_guard.emulator_running = lambda name: True
        with self.assertRaises(rpc.RpcError):
            self._call("input_save")
        self.assertEqual(self._read(), FIX)

    def test_reject_unknown_action(self):
        with self.assertRaises(rpc.RpcError):
            self._call("input_set", id="NotAnAction", codes=[e.KEY_SPACE])

    # ── the KEYING landmine: arcade + retail never share a buffer ─────────────
    def test_arcade_and_retail_are_isolated(self):
        retail = self.d / "retail.ini"
        retail.write_text(FIX, newline="")
        hk._INIS["x6r"] = retail
        hk._BUFS["x6r"].reset()
        # stage an edit on ARCADE (x6a)
        rpc._METHODS["x6a_hk.input_set"][0]({"id": "TogglePause", "codes": [e.KEY_SPACE]})
        self.assertTrue(hk._BUFS["x6a"].dirty)
        self.assertFalse(hk._BUFS["x6r"].dirty)                      # retail buffer never saw it
        self.assertFalse(rpc._METHODS["x6r_hk.input_get"][0]({})["dirty"])
        self.assertEqual(retail.read_text(newline=""), FIX)         # retail disk untouched
        # saving arcade must not write retail
        rpc._METHODS["x6a_hk.input_save"][0]({})
        self.assertEqual(self._disk("TogglePause"), "Keyboard/Space")   # arcade committed
        self.assertEqual(retail.read_text(newline=""), FIX)            # retail still pristine


if __name__ == "__main__":
    unittest.main()
