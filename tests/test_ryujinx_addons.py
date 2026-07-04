"""ryujinx_addons.* — per-game Mods / Update / DLC over the Ryujinx per-title JSON stores
(games/<tid-lower>/{mods.json,updates.json,dlc.json}). get renders bool toggles + the single-select
update enum; set flips them. DLC is offered ONLY when a dlc.json already exists. Writes refuse while
Ryujinx runs. Run: python3 -m unittest tests.test_ryujinx_addons -v
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from lib import proc_guard, staterev
from lib.madsrv import rpc, ryujinx_addons_cmds, ryujinx_json  # noqa: F401  (registers the methods)
from lib.madsrv.rpc import RpcError

TID = "0100ABCD0000F000"


class RyujinxAddons(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.gdir = self.d / "games" / TID.lower()
        self.gdir.mkdir(parents=True)
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

    def _write(self, name, data):
        (self.gdir / name).write_text(json.dumps(data))

    def _read(self, name):
        return json.loads((self.gdir / name).read_text())

    def _get(self):
        return rpc._METHODS["ryujinx_addons.get"][0]({"titleid": TID})

    def _set(self, key, value):
        return rpc._METHODS["ryujinx_addons.set"][0]({"titleid": TID, "key": key, "value": value})

    def _grp(self, title):
        return next((g for g in self._get()["groups"] if g["title"] == title), None)

    def test_mods_toggle(self):
        self._write("mods.json", {"mods": [{"name": "TOTK Opt", "path": "/x", "enabled": True}]})
        row = self._grp("Mods")["settings"][0]
        self.assertEqual(row["key"], "mod:0")
        self.assertTrue(row["value"])
        self._set("mod:0", "0")
        self.assertFalse(self._read("mods.json")["mods"][0]["enabled"])
        self._set("mod:0", "1")
        self.assertTrue(self._read("mods.json")["mods"][0]["enabled"])

    def test_update_single_select(self):
        self._write("updates.json", {"selected": "/u/A.nsp", "paths": ["/u/A.nsp", "/u/B.nsp"]})
        row = self._grp("Update")["settings"][0]
        self.assertEqual(row["type"], "enum")
        self.assertEqual(row["options"][0], "None (base game)")
        self.assertEqual(row["value"], 1)                    # A.nsp selected (index 1)
        self._set("update", "2")                             # pick B.nsp
        self.assertEqual(self._read("updates.json")["selected"], "/u/B.nsp")
        self._set("update", "0")                             # None -> base game
        self.assertEqual(self._read("updates.json")["selected"], "")

    def test_dlc_only_when_file_exists(self):
        self.assertIsNone(self._grp("DLC"))                  # no dlc.json -> no DLC group
        self._write("dlc.json", [{"path": "/d/Foo.nsp",
                                  "dlc_nca_list": [{"path": "/a.nca", "title_id": 1, "is_enabled": True}]}])
        row = self._grp("DLC")["settings"][0]
        self.assertEqual(row["key"], "dlc:0:0")
        self.assertTrue(row["value"])
        self._set("dlc:0:0", "0")
        self.assertFalse(self._read("dlc.json")[0]["dlc_nca_list"][0]["is_enabled"])

    def test_empty_game_note(self):
        p = self._get()
        self.assertTrue(p["exists"])
        self.assertIn("No add-ons", p["note"])

    def test_refuses_while_running(self):
        self._write("mods.json", {"mods": [{"name": "M", "path": "/x", "enabled": True}]})
        proc_guard.emulator_running = lambda n: True
        with self.assertRaises(RpcError):
            self._set("mod:0", "0")

    def test_bad_titleid(self):
        with self.assertRaises(RpcError):
            rpc._METHODS["ryujinx_addons.get"][0]({"titleid": "notahex"})


if __name__ == "__main__":
    unittest.main()
