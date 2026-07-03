"""citron_dock.* — the Dock-detection toggle: exists:true single-bool payload, and a
set -> local-policy write -> get round-trip (default on from the shipped policy, overridable
to off in controller-policy.local.toml)."""
import shutil
import tempfile
import unittest
from pathlib import Path

from lib.madsrv import citron_dock_cmds as dc
from lib.madsrv import rpc


class CitronDock(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        import lib.policy as policy
        self._orig_local = policy.LOCAL
        policy.LOCAL = self.d / "controller-policy.local.toml"
        import lib.staterev as sr
        self._bump = sr.bump
        sr.bump = lambda n: None

    def tearDown(self):
        import lib.policy as policy
        policy.LOCAL = self._orig_local
        import lib.staterev as sr
        sr.bump = self._bump
        shutil.rmtree(self.d, ignore_errors=True)

    def _get(self):
        return rpc._METHODS["citron_dock.get"][0]({})

    def _set(self, on):
        return rpc._METHODS["citron_dock.set"][0]({"key": "dock_autodetect", "value": on})

    def _row(self):
        return self._get()["groups"][0]["settings"][0]

    def test_registered(self):
        self.assertIn("citron_dock.get", rpc._METHODS)
        self.assertIn("citron_dock.set", rpc._METHODS)

    def test_payload_shape(self):
        p = self._get()
        self.assertTrue(p["exists"])                     # GuiMadPageEmuSettings needs this
        self.assertEqual(self._row()["type"], "bool")

    def test_default_on_from_shipped_policy(self):
        # No local override -> the shipped [backends.citron].dock_autodetect = true wins.
        self.assertTrue(self._row()["value"])

    def test_toggle_round_trip(self):
        self._set(False)
        self.assertFalse(self._row()["value"])
        # persisted to the local toml
        import lib.policy as policy
        self.assertTrue(policy.LOCAL.is_file())
        self.assertIn("dock_autodetect = false", policy.LOCAL.read_text())
        self._set(True)
        self.assertTrue(self._row()["value"])

    def test_set_returns_bool(self):
        self.assertEqual(self._set(False)["value"], False)
        self.assertEqual(self._set(True)["value"], True)


if __name__ == "__main__":
    unittest.main()
