"""ryujinx_cheats.* — per-game cheat enable/disable via the enabled.txt whitelist under
mods/contents/<TitleId-UPPER>/cheats/. get parses [Cheat Name] headers from the *.txt files and
marks each enabled iff a "<BUILDID-UPPER>-<name>" line is in enabled.txt (absent file = all off);
set writes those lines. Writes refuse while Ryujinx runs.
Run: python3 -m unittest tests.test_ryujinx_cheats -v
"""
import shutil
import tempfile
import unittest
from pathlib import Path

from lib import proc_guard, staterev
from lib.madsrv import rpc, ryujinx_cheats_cmds, ryujinx_json  # noqa: F401  (registers the methods)
from lib.madsrv.rpc import RpcError

TID = "0100ABCD0000F000"
BID = "ABCDEF0123456789"


class RyujinxCheats(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.cdir = self.d / "mods" / "contents" / TID.upper() / "cheats"
        self.cdir.mkdir(parents=True)
        self._c = ryujinx_json.CONFIG
        ryujinx_json.CONFIG = self.d / "Config.json"
        self._run = proc_guard.emulator_running
        proc_guard.emulator_running = lambda n: False
        self._bump = staterev.bump
        staterev.bump = lambda n: None

    def tearDown(self):
        ryujinx_json.CONFIG = self._c
        proc_guard.emulator_running = self._run
        staterev.bump = self._bump
        shutil.rmtree(self.d, ignore_errors=True)

    def _cheat_file(self, names, stem=BID):
        (self.cdir / f"{stem}.txt").write_text("".join(f"[{n}]\n04000000 0 0\n" for n in names))

    def _get(self):
        return rpc._METHODS["ryujinx_cheats.get"][0]({"titleid": TID})

    def _set(self, name, value):
        return rpc._METHODS["ryujinx_cheats.set"][0](
            {"titleid": TID, "key": f"cheat:{BID}:{name}", "value": value})

    def _rows(self):
        return {s["label"]: s["value"] for s in self._get()["groups"][0]["settings"]}

    def _enabled(self):
        p = self.cdir / "enabled.txt"
        return p.read_text() if p.is_file() else ""

    def test_parses_headers_all_off_by_default(self):
        self._cheat_file(["60 FPS", "Infinite HP"])
        self.assertEqual(self._rows(), {"60 FPS": False, "Infinite HP": False})   # no enabled.txt -> inert

    def test_enable_writes_whitelist_line(self):
        self._cheat_file(["60 FPS", "Infinite HP"])
        self._set("60 FPS", "1")
        self.assertEqual(self._enabled(), f"{BID}-60 FPS\n")
        self.assertEqual(self._rows(), {"60 FPS": True, "Infinite HP": False})

    def test_disable_removes_line(self):
        self._cheat_file(["60 FPS"])
        self._set("60 FPS", "1")
        self._set("60 FPS", "0")
        self.assertEqual(self._rows(), {"60 FPS": False})
        self.assertNotIn("60 FPS", self._enabled())

    def test_no_cheats_note(self):
        p = self._get()
        self.assertTrue(p["exists"])
        self.assertEqual(p["groups"][0]["settings"], [])
        self.assertIn("No cheats", p["note"])

    def test_uppercases_build_id_from_stem(self):
        # a lowercase-stem cheat file -> BUILDID uppercased in the key (matches Ryujinx's ToUpper).
        (self.cdir / f"{BID}.txt").unlink(missing_ok=True)
        self._cheat_file(["X"], stem=BID.lower())
        row = self._get()["groups"][0]["settings"][0]
        self.assertEqual(row["key"], f"cheat:{BID}:X")

    def test_padded_cheat_name_round_trips_verbatim(self):
        # Ryujinx keeps inner-bracket whitespace (line[1..^1], no inner trim) and matches the
        # whitelist key EXACTLY, so " Moon Jump " must round-trip verbatim -- not be stripped.
        self._cheat_file([" Moon Jump "])
        self._set(" Moon Jump ", "1")
        self.assertEqual(self._enabled(), f"{BID}- Moon Jump \n")   # written verbatim
        self.assertTrue(self._rows()[" Moon Jump "])                # and read back as enabled

    def test_refuses_while_running(self):
        self._cheat_file(["60 FPS"])
        proc_guard.emulator_running = lambda n: True
        with self.assertRaises(RpcError):
            self._set("60 FPS", "1")


if __name__ == "__main__":
    unittest.main()
