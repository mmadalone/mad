"""Controller-config backup category (P12b Build B): controllers_map enumerates the live controller targets
(mad_config.backup_targets + controller-policy.local.toml), granular_backup.plan/backup_controllers writes a
dated set, restore_selection(category='controllers') restores under rule-5 bounded by controllers_map's
allowlist, and cloud.push_controllers streams push-controllers to a SEPARATE remote base.

Run:  python3 -m unittest tests.test_controllers -v
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import sys
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib import controllers_map as cm             # noqa: E402
from lib import granular_backup as gb             # noqa: E402
from lib import mad_config, policy                # noqa: E402
from lib.madsrv import cloud_cmds as cc           # noqa: E402
from lib.madsrv.rpc import RpcError               # noqa: E402


class _FakeHome:
    """A temp $HOME with an eden qt-config (a FILE target), a Cemu controllerProfiles (a DIR target), and the
    controller-policy.local.toml, with backup_targets + policy.LOCAL monkeypatched to point at them."""
    def __enter__(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        (self.home / ".config/eden").mkdir(parents=True)
        (self.home / ".config/eden/qt-config.ini").write_text("[bind]\npad=A\n")
        (self.home / ".config/Cemu/controllerProfiles").mkdir(parents=True)
        (self.home / ".config/Cemu/controllerProfiles/controller0.xml").write_text("<pad/>\n")
        (self.home / "Emulation/tools/launchers").mkdir(parents=True)
        self.local = self.home / "Emulation/tools/launchers/controller-policy.local.toml"
        self.local.write_text("routing=1\n")
        self._env = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        self._env.start()
        self._targets = {"eden": self.home / ".config/eden/qt-config.ini",
                         "cemu": self.home / ".config/Cemu/controllerProfiles"}
        self._p1 = mock.patch.object(mad_config, "backup_targets", lambda merged=None: self._targets)
        self._p2 = mock.patch.object(policy, "LOCAL", self.local)
        self._p1.start()
        self._p2.start()
        return self

    def __exit__(self, *a):
        self._p2.stop()
        self._p1.stop()
        self._env.stop()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def back_items(self):
        return [{"group": g["key"], "rel": f["rel"], "name": f["name"]}
                for g in cm.list_groups(home=self.home) for f in g["files"]]

    def rest_items(self):
        return [{"system": g["key"], "id": f["rel"]}
                for g in cm.list_groups(home=self.home) for f in g["files"]]


class ControllersMap(unittest.TestCase):
    def test_enumerates_targets_and_override(self):
        with _FakeHome() as h:
            groups = {g["key"]: g for g in cm.list_groups(home=h.home)}
            self.assertEqual(set(groups), {"emulator-configs", "routing-overrides"})
            rels = {f["rel"] for f in groups["emulator-configs"]["files"]}
            self.assertIn("controllers/.config/eden/qt-config.ini", rels)
            self.assertIn("controllers/.config/Cemu/controllerProfiles", rels)  # a DIR target
            kinds = {f["rel"]: f["kind"] for f in groups["emulator-configs"]["files"]}
            self.assertEqual(kinds["controllers/.config/Cemu/controllerProfiles"], "folder")
            self.assertEqual([f["rel"] for f in groups["routing-overrides"]["files"]],
                             ["controllers/Emulation/tools/launchers/controller-policy.local.toml"])

    def test_allowlist_accepts_targets_rejects_others(self):
        with _FakeHome() as h:
            self.assertTrue(cm.rel_allowed("controllers/.config/eden/qt-config.ini", home=h.home))
            self.assertTrue(cm.rel_allowed("controllers/.config/Cemu/controllerProfiles/controller0.xml",
                                           home=h.home))  # a subpath of the dir target
            self.assertTrue(cm.rel_allowed(
                "controllers/Emulation/tools/launchers/controller-policy.local.toml", home=h.home))
            for bad in ("controllers/.ssh/id_rsa", "controllers/.config/Cemu",  # parent of the dir target
                        "controllers/../secret", "controllers/Emulation/tools/smb.conf"):
                self.assertFalse(cm.rel_allowed(bad, home=h.home), bad)


class ControllersRoundTrip(unittest.TestCase):
    def test_backup_restore_with_rule5(self):
        with _FakeHome() as h:
            dest = h.tmp / "dest"
            res = gb.backup_controllers(h.back_items(), str(dest), "20260729T010101",
                                        emit=lambda e: None, is_stopped=lambda: False)
            self.assertEqual(res["copied"], 3)  # eden file + cemu dir + override
            self.assertTrue(os.path.basename(res["path"]).startswith("deck-granular-controllers-"))
            orig = (h.home / ".config/eden/qt-config.ini").read_text()
            (h.home / ".config/eden/qt-config.ini").write_text("CORRUPT\n")
            rr = gb.restore_selection(res["path"], h.rest_items(), "controllers", "20260729T010102",
                                      emit=lambda e: None, is_stopped=lambda: False)
            self.assertEqual(rr["restored"], 3)
            self.assertGreaterEqual(len(rr.get("snapshots") or []), 1)  # rule-5 snapshot of the replaced file
            self.assertEqual((h.home / ".config/eden/qt-config.ini").read_text(), orig)

    def test_forged_rel_writes_nothing(self):
        with _FakeHome() as h:
            dest = h.tmp / "dest"
            res = gb.backup_controllers(h.back_items(), str(dest), "20260729T010101",
                                        emit=lambda e: None, is_stopped=lambda: False)
            (h.home / ".ssh").mkdir()
            (h.home / ".ssh/id_rsa").write_text("SECRET")
            gb.restore_selection(res["path"], [{"system": "x", "id": "controllers/.ssh/id_rsa"}],
                                 "controllers", "20260729T010103", emit=lambda e: None, is_stopped=lambda: False)
            self.assertEqual((h.home / ".ssh/id_rsa").read_text(), "SECRET")  # never overwritten


class CloudPushControllers(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        os.environ["DECK_CLOUD_STATE_DIR"] = str(self.tmp / "state")
        self.calls = []

        def fake_stream_op(argv):
            self.calls.append(list(argv))
            return {"stream": "tok"}
        self._p = mock.patch.object(cc, "_stream_op", fake_stream_op)
        self._p.start()

    def tearDown(self):
        self._p.stop()
        os.environ.pop("DECK_CLOUD_STATE_DIR", None)
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_persists_plan_and_streams_push_controllers(self):
        runs = []

        def fake_run(*a, **k):
            runs.append(a)
            return (0, "", "")

        with _FakeHome() as h, mock.patch.object(cc, "_run", fake_run):
            r = cc._cloud_push_controllers({"items": h.back_items()})
            self.assertEqual(r, {"stream": "tok"})
            argv = self.calls[0]
            self.assertIn("push-controllers", argv)
            # fixed merged set: token is "controllers" (not a dated ts) + the remote manifest merge ran
            self.assertEqual(argv[2], "controllers")
            self.assertEqual(runs[0][0], ["cat-controllers-manifest", "controllers"])
            plandir = Path(argv[-1])
            self.assertTrue((plandir / "plan").is_file())
            self.assertTrue((plandir / "mad-manifest.json").is_file())

    def test_empty_selection_is_einval(self):
        with self.assertRaises(RpcError) as e:
            cc._cloud_push_controllers({"items": []})
        self.assertEqual(e.exception.code, "EINVAL")


if __name__ == "__main__":
    unittest.main()
