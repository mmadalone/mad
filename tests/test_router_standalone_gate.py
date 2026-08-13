"""controller-router._standalone's early no-op gate (A4, audit 2026-08-12 phase 5).

MEASURED on this Deck: every standalone-emulator launch (Switch/PS2/PS3/Xbox/...) spent
370 ms in enumerate_devices() plus 46 ms importing the device machinery via _load_heavy(),
then threw it all away when the system was router_skip (hands-off) or had no backend, and
no X-Arcade warning could fire either. _standalone() now computes _xarcade_warn_possible()
and a router_skip/backend check BEFORE any of that, and returns immediately when neither
could do anything.

Companion to tests/test_xarcade_warn_handheld.py, which covers _xarcade_warn's own
device-level behaviour, and tests/test_router_lazy_imports.py, which covers the read-only
query modes' import diet.

Loads the hyphenated top-level script via spec_from_file_location (main() stays unrun) --
same pattern as test_xarcade_warn_handheld.py, reused here rather than inventing a second
loader.

Run: python3 -m unittest tests.test_router_standalone_gate -v
"""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    modu = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modu)
    return modu


cr = _load("controller_router_standalone_gate", "controller-router.py")
cr._load_heavy()      # materialize the lazy device/RA names once so patch.object finds
                       # them below (mirrors test_xarcade_warn_handheld.py); every test that
                       # must prove the gate skips this path re-patches _load_heavy itself.


def _fail(*_a, **_kw):
    raise AssertionError("heavy device path was reached; the gate should have "
                          "short-circuited before it")


def _ctx(system: str) -> "cr.GameContext":
    return cr.GameContext(
        rom_path=f"/home/deck/ROMs/{system}/game.zip",
        name="Game",
        system=system,
        fullname=system,
        collection=None,
        policy_key=system,
    )


class StandaloneGateNoOp(unittest.TestCase):
    """router_skip (or no backend) + no warning configured => _standalone returns 0
    without ever calling enumerate_devices or _load_heavy."""

    def setUp(self):
        self.log = mock.MagicMock()

    def test_router_skip_no_warn_never_touches_heavy_path(self):
        # No category (so neither X-Arcade default fires) + router_skip => a pure no-op.
        sys_entry = {"router_skip": True}
        with mock.patch.object(cr, "load_policy", return_value={"defaults": {}}), \
             mock.patch.object(cr, "resolve_policy", return_value=sys_entry), \
             mock.patch.object(cr, "xarcade_port", return_value=""), \
             mock.patch.object(cr, "_handheld_active", return_value=False), \
             mock.patch.object(cr, "_load_heavy", side_effect=_fail), \
             mock.patch.object(cr, "enumerate_devices", side_effect=_fail):
            rc = cr._standalone(_ctx("switch"), self.log)
        self.assertEqual(rc, 0)

    def test_no_backend_no_warn_never_touches_heavy_path(self):
        # No router_skip, no backend, and no category so neither X-Arcade default fires.
        sys_entry = {"category": None}
        with mock.patch.object(cr, "load_policy", return_value={"defaults": {}}), \
             mock.patch.object(cr, "resolve_policy", return_value=sys_entry), \
             mock.patch.object(cr, "xarcade_port", return_value=""), \
             mock.patch.object(cr, "_handheld_active", return_value=False), \
             mock.patch.object(cr, "_load_heavy", side_effect=_fail), \
             mock.patch.object(cr, "enumerate_devices", side_effect=_fail):
            rc = cr._standalone(_ctx("daphne"), self.log)
        self.assertEqual(rc, 0)


class StandaloneGateStillRoutes(unittest.TestCase):
    """A system WITH a configured backend still reaches the heavy path -- the gate must
    never silently disable real routing."""

    def test_backend_present_reaches_enumerate_devices(self):
        self.log = mock.MagicMock()
        # No category (so warn_possible is False and the warn block is skipped entirely,
        # keeping this test focused on the routing path) + a real backend => route_needed.
        sys_entry = {"router_skip": False, "backend": "cemu", "ports": []}
        calls = {"enumerate": 0}

        def fake_enumerate():
            calls["enumerate"] += 1
            return []

        with mock.patch.object(cr, "load_policy",
                               return_value={"defaults": {}, "backends": {"cemu": {}}}), \
             mock.patch.object(cr, "resolve_policy", return_value=sys_entry), \
             mock.patch.object(cr, "xarcade_port", return_value=""), \
             mock.patch.object(cr, "_handheld_active", return_value=False), \
             mock.patch.object(cr, "enumerate_devices", side_effect=fake_enumerate), \
             mock.patch("lib.cemu_cfg.assign", return_value=0) as assign:
            rc = cr._standalone(_ctx("cemu"), self.log)
        self.assertGreater(calls["enumerate"], 0,
                           "enumerate_devices was never called; routing was silently skipped")
        assign.assert_called_once()
        self.assertEqual(rc, 0)


class XarcadeWarnPossible(unittest.TestCase):
    """_xarcade_warn_possible reproduces _xarcade_warn's own gating without touching
    any device."""

    def test_true_when_warn_only_set(self):
        self.assertTrue(cr._xarcade_warn_possible(
            {"warn_when_only_xarcade": True}, {}, "", handheld=False))

    def test_true_when_warn_no_set_and_xport_nonempty(self):
        self.assertTrue(cr._xarcade_warn_possible(
            {"warn_when_no_xarcade": True}, {}, "1-1", handheld=False))

    def test_false_when_warn_no_set_but_xport_empty(self):
        self.assertFalse(cr._xarcade_warn_possible(
            {"warn_when_no_xarcade": True}, {}, "", handheld=False))

    def test_false_whenever_handheld(self):
        # handheld beats every other flag, including warn_when_only_xarcade.
        self.assertFalse(cr._xarcade_warn_possible(
            {"warn_when_only_xarcade": True, "warn_when_no_xarcade": True},
            {}, "1-1", handheld=True))

    def test_false_when_neither_flag_set(self):
        # No category (so neither the console/arcade default fires), no explicit flags.
        self.assertFalse(cr._xarcade_warn_possible({}, {}, "", handheld=False))


class XarcadeWarnUsesPredicate(unittest.TestCase):
    """_xarcade_warn itself calls _xarcade_warn_possible as its own early return, so a
    system the predicate rules out never touches the device list."""

    def test_predicate_false_returns_0_without_touching_devices(self):
        log = mock.MagicMock()
        sys_entry = {"warn_when_no_xarcade": True}   # xport empty below => predicate False
        with mock.patch.object(cr, "_show_warning_blocking", side_effect=_fail):
            # devs=None: any attempt to index/iterate it would raise, proving the
            # only_xarcade_present/xarcade_present device checks were never reached.
            rc = cr._xarcade_warn(sys_entry, None, log, "", {}, handheld=False)
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
