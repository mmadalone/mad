"""Switch dock auto-detect (Citron / Eden / Ryujinx).

Covers:
  * the three Dock-detection TOGGLE pages (ryujinx_dock / eden_dock / citron_dock): exists:true
    single-bool payload + set -> local-policy write -> get round-trip (default on from the shipped
    policy), each writing its OWN [backends.<emu>] table,
  * the CONTROLLER-based heuristic switch_bind._switch_dock_state: external pad -> docked; only the
    Deck built-in (or nothing) -> handheld,
  * the TRANSIENT snapshot/restore contract -- the launch-time docked write reverts on exit, per
    config format: Ryujinx JSON docked_mode; Citron/Eden Yuzu ini use_docked_mode + \\default twin,
  * dock state is recorded in the snapshot ONLY when auto-detect is on (so an in-emulator change
    during play is not clobbered).

Pure logic + temp config files -- no hardware. Run: python3 -m unittest tests.test_switch_dock -v
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from lib import fsutil, switch_bind
from lib.madsrv import cfgutil, rpc, ryujinx_json
from lib.madsrv import eden_dock_cmds, ryujinx_dock_cmds  # noqa: F401  (register the toggle methods)
from tests._fakes import sd

DECK = "28de:1205"
DS5 = "054c:0ce6"
XARCADE = "045e:02a1"


# ── the toggle pages ──────────────────────────────────────────────────────────
class _DockToggle:
    NS = BACKEND = None      # subclasses set

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

    def _row(self):
        return rpc._METHODS[f"{self.NS}.get"][0]({})["groups"][0]["settings"][0]

    def _set(self, on):
        return rpc._METHODS[f"{self.NS}.set"][0]({"key": "dock_autodetect", "value": on})

    def test_registered_and_shape(self):
        p = rpc._METHODS[f"{self.NS}.get"][0]({})
        self.assertTrue(p["exists"])
        self.assertEqual(self._row()["type"], "bool")

    def test_default_on_from_shipped_policy(self):
        self.assertTrue(self._row()["value"])          # shipped [backends.<emu>].dock_autodetect = true

    def test_toggle_round_trip_writes_own_backend(self):
        import lib.policy as policy
        self.assertEqual(self._set(False)["value"], False)
        self.assertFalse(self._row()["value"])
        txt = policy.LOCAL.read_text()
        self.assertIn(f"[backends.{self.BACKEND}]", txt)
        self.assertIn("dock_autodetect = false", txt)
        self.assertEqual(self._set(True)["value"], True)
        self.assertTrue(self._row()["value"])


class RyujinxDockToggle(_DockToggle, unittest.TestCase):
    NS, BACKEND = "ryujinx_dock", "ryujinx"


class EdenDockToggle(_DockToggle, unittest.TestCase):
    NS, BACKEND = "eden_dock", "eden"


# ── the controller heuristic ──────────────────────────────────────────────────
class Heuristic(unittest.TestCase):
    def setUp(self):
        import lib.madsrv.pads_cmds as pc
        self._hh = pc._handheld_class
        pc._handheld_class = lambda emu: DECK          # hermetic (don't depend on live policy)
        # These exercise the LEGACY controller heuristic, which _switch_dock_state uses only as the
        # fallback when the on-the-go feature is DISABLED; the enabled default is display-based
        # (class DisplayDock below). Force the fallback path here.
        import lib.policy as policy
        self._lm = policy.load_merged
        policy.load_merged = lambda: {"handheld": {"enabled": False}}

    def tearDown(self):
        import lib.madsrv.pads_cmds as pc
        pc._handheld_class = self._hh
        import lib.policy as policy
        policy.load_merged = self._lm

    def test_external_pad_is_docked(self):
        for emu in ("citron", "eden", "ryujinx"):
            self.assertTrue(switch_bind._switch_dock_state(emu, [sd(0, DS5, "g", "DualSense")]), emu)

    def test_deck_only_is_handheld(self):
        for emu in ("citron", "eden", "ryujinx"):
            self.assertFalse(switch_bind._switch_dock_state(emu, [sd(0, DECK, "g", "Deck")]), emu)

    def test_no_pads_is_handheld(self):
        for emu in ("citron", "eden", "ryujinx"):
            self.assertFalse(switch_bind._switch_dock_state(emu, []), emu)

    def test_external_presence_wins(self):
        pads = [sd(0, XARCADE, "g", "X-Arcade"), sd(1, DECK, "g", "Deck")]
        self.assertTrue(switch_bind._switch_dock_state("ryujinx", pads))


class DisplayDock(unittest.TestCase):
    """On-the-go ENABLED (the default): _switch_dock_state follows the physical display / force
    override (deck_state), NOT the attached pads -- fixing the old bug where a Bluetooth pad
    handheld wrongly forced docked mode."""

    def setUp(self):
        import lib.policy as policy
        self._lm = policy.load_merged
        policy.load_merged = lambda: {"handheld": {"enabled": True}}
        os.environ.pop("MAD_FORCE_CONTEXT", None)

    def tearDown(self):
        import lib.policy as policy
        policy.load_merged = self._lm
        os.environ.pop("MAD_FORCE_CONTEXT", None)

    def test_force_handheld_ignores_external_pad(self):
        os.environ["MAD_FORCE_CONTEXT"] = "handheld"
        self.assertFalse(switch_bind._switch_dock_state("eden", [sd(0, DS5, "g", "DualSense")]))

    def test_force_docked_ignores_deck_only(self):
        os.environ["MAD_FORCE_CONTEXT"] = "docked"
        self.assertTrue(switch_bind._switch_dock_state("eden", [sd(0, DECK, "g", "Deck")]))


# ── the transient snapshot/restore contract ───────────────────────────────────
class _Transient(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        import lib.staterev as sr
        self._bump = sr.bump
        sr.bump = lambda n: None
        self._da = switch_bind._dock_autodetect
        switch_bind._dock_autodetect = lambda emu: self.autodetect
        self.autodetect = True

    def tearDown(self):
        switch_bind._dock_autodetect = self._da
        import lib.staterev as sr
        sr.bump = self._bump
        shutil.rmtree(self.d, ignore_errors=True)


class RyujinxTransient(_Transient):
    def _cfg(self):
        p = self.d / "Config.json"
        p.write_text(json.dumps({"docked_mode": True, "input_config": [{"player": 1}], "keep": 9}))
        ryujinx_json.CONFIG = p
        return p

    def test_dock_write_reverts_and_preserves_input(self):
        p = self._cfg()
        snap = switch_bind._snapshot("ryujinx", p)
        self.assertEqual(snap.get("docked"), True)      # resting value recorded
        switch_bind._apply_ryujinx_dock(p, False)       # write handheld
        self.assertFalse(json.loads(p.read_text())["docked_mode"])
        fsutil.atomic_write_text(switch_bind._sidecar(p), json.dumps({"emu": "ryujinx", "input": snap}))
        switch_bind.restore_target(p)
        after = json.loads(p.read_text())
        self.assertTrue(after["docked_mode"])           # reverted
        self.assertEqual(after["input_config"], [{"player": 1}])
        self.assertFalse(switch_bind._sidecar(p).exists())

    def test_snapshot_omits_dock_when_autodetect_off(self):
        self.autodetect = False
        snap = switch_bind._snapshot("ryujinx", self._cfg())
        self.assertNotIn("dock_managed", snap)
        self.assertIn("input_config", snap)

    def test_legacy_list_sidecar_still_restores_input(self):
        # A sidecar written by the pre-dock code (a bare input_config LIST) must still restore.
        p = self._cfg()
        fsutil.atomic_write_text(switch_bind._sidecar(p),
                                 json.dumps({"emu": "ryujinx", "input": [{"player": 5}]}))
        switch_bind.restore_target(p)
        self.assertEqual(json.loads(p.read_text())["input_config"], [{"player": 5}])

    def test_pia_write_reverts_on_exit(self):
        # ryujinx_cfg.assign_devices writes player_input_assignments at launch; the transient bind
        # must revert it to the resting state on exit (dual-context: Steam-UI launch stays clean).
        p = self.d / "Config.json"
        p.write_text(json.dumps({"docked_mode": True, "input_config": [{"player_index": "Player1"}],
                                 "player_input_assignments": [{"player_index": "Player1",
                                                               "devices": [{"id": "RESTING"}]}]}))
        ryujinx_json.CONFIG = p
        snap = switch_bind._snapshot("ryujinx", p)
        d = json.loads(p.read_text())               # simulate the launch-time PIA rewrite
        d["player_input_assignments"][0]["devices"][0]["id"] = "LAUNCH"
        ryujinx_json.write(d, p)
        fsutil.atomic_write_text(switch_bind._sidecar(p), json.dumps({"emu": "ryujinx", "input": snap}))
        switch_bind.restore_target(p)
        after = json.loads(p.read_text())
        self.assertEqual(after["player_input_assignments"][0]["devices"][0]["id"], "RESTING")  # reverted


class YuzuTransient(_Transient):
    _INI = ("[System]\nuse_docked_mode=1\nuse_docked_mode\\default=true\n"
            "[Controls]\nplayer_0_type=0\n")

    def test_yuzu_dock_write_reverts(self):
        for emu in ("citron", "eden"):
            ini = self.d / f"{emu}.ini"
            ini.write_text(self._INI)
            snap = switch_bind._snapshot(emu, ini)
            self.assertEqual(snap["docked"], "1")
            switch_bind._apply_yuzu_dock(ini, False)     # write handheld
            self.assertEqual(cfgutil.ini_read(ini.read_text(), "System", "use_docked_mode"), "0", emu)
            fsutil.atomic_write_text(switch_bind._sidecar(ini), json.dumps({"emu": emu, "input": snap}))
            switch_bind.restore_target(ini)
            text = ini.read_text()
            self.assertEqual(cfgutil.ini_read(text, "System", "use_docked_mode"), "1", emu)     # reverted
            self.assertEqual(cfgutil.ini_read(text, "System", "use_docked_mode\\default"), "true", emu)
            self.assertIn("player_0_type=0", text, emu)  # [Controls] preserved
            self.assertFalse(switch_bind._sidecar(ini).exists(), emu)


if __name__ == "__main__":
    unittest.main()
