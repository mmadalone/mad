"""Tests for the Daphne/Hypseus backend buffered-editor contract: daphne.* now
reports a REAL buffer-vs-disk dirty (text compare, no sticky flag) and offers
daphne.cancel (reload-from-disk). Daphne keeps its own _state buffer (an
HypInput), not the shared input_buffer.py, so its dirty mechanics are locked
here.

Hermetic: hypinput.load/write are patched onto an in-memory "disk" string so
no real hypinput.ini is touched. _games/_game_names are stubbed so the page
data doesn't depend on ~/ROMs/daphne.

Run:  python3 -m unittest tests.test_daphne_cmds -v
"""
from __future__ import annotations

import unittest
from unittest import mock

from lib import hypinput
from lib.madsrv import daphne_cmds as dp
from lib.madsrv import rpc


def _call(name, params=None):
    return rpc._METHODS[name][0](params or {})


def _bound_action(rows) -> str:
    """A primary row that currently HAS a binding (display isn't the unbound
    placeholder)."""
    for a in ("BUTTON1", "COIN1", "START1", "BUTTON2", "BUTTON3"):
        d = rows[a]["display"]
        if d and "unbound" not in d:
            return a
    raise AssertionError("no bound primary row in the default map")


def _unbound_action(rows):
    for a, row in rows.items():
        if "unbound" in row["display"]:
            return a
    return None


class Registration(unittest.TestCase):
    def test_rpcs_registered(self):
        for m in ("daphne.load", "daphne.clear", "daphne.reset_defaults",
                  "daphne.bind", "daphne.save", "daphne.cancel"):
            self.assertIn(m, rpc._METHODS, m)

    def test_cancel_is_slow(self):
        self.assertTrue(rpc._METHODS["daphne.cancel"][1])  # slow=True (per-game re-read)


class BufferedDirty(unittest.TestCase):
    def setUp(self):
        # In-memory global "disk": starts as the stock default map.
        self._disk = {"text": hypinput.DEFAULT_TEMPLATE}
        p_load = mock.patch.object(
            hypinput, "load",
            side_effect=lambda path=hypinput.GLOBAL_INI: hypinput.parse(self._disk["text"]))
        p_write = mock.patch.object(
            hypinput, "write_global",
            side_effect=lambda hi: self._disk.__setitem__("text", hi.text()))
        p_games = mock.patch.object(dp, "_games", side_effect=lambda: [])
        p_names = mock.patch.object(dp, "_game_names", side_effect=lambda: {})
        for p in (p_load, p_write, p_games, p_names):
            p.start()
            self.addCleanup(p.stop)

    def test_load_is_buffered_and_clean(self):
        r = _call("daphne.load", {"scope": "global"})
        self.assertTrue(r["buffered"])
        self.assertFalse(r["dirty"])

    def test_clearing_a_bound_row_is_dirty(self):
        r = _call("daphne.load", {"scope": "global"})
        c = _call("daphne.clear", {"action": _bound_action(r["rows"])})
        self.assertTrue(c["buffered"])
        self.assertTrue(c["dirty"])

    def test_clearing_an_unbound_row_stays_clean(self):
        # The real buffer-vs-disk compare (unlike the old sticky flag): a no-op
        # edit does NOT mark the page dirty.
        r = _call("daphne.load", {"scope": "global"})
        unbound = _unbound_action(r["rows"])
        if unbound is None:
            self.skipTest("default map has no unbound row to no-op clear")
        c = _call("daphne.clear", {"action": unbound})
        self.assertFalse(c["dirty"])

    def test_cancel_reverts_to_disk(self):
        r = _call("daphne.load", {"scope": "global"})
        _call("daphne.clear", {"action": _bound_action(r["rows"])})
        cc = _call("daphne.cancel", {})
        self.assertFalse(cc["dirty"])
        # the buffer is back to the on-disk map (disk untouched by clear/cancel)
        self.assertEqual(dp._state["hi"].text(), self._disk["text"])

    def test_save_advances_the_baseline(self):
        r = _call("daphne.load", {"scope": "global"})
        _call("daphne.clear", {"action": _bound_action(r["rows"])})
        s = _call("daphne.save", {})
        self.assertFalse(s["dirty"])                    # clean right after save
        # the write hit our in-memory disk; a fresh load of it is clean, not dirty
        r2 = _call("daphne.load", {"scope": "global"})
        self.assertFalse(r2["dirty"])

    def test_reset_defaults_reads_dirty_when_it_differs_from_disk(self):
        # Seed disk with a NON-default map so load_default() diverges from it.
        r = _call("daphne.load", {"scope": "global"})
        _call("daphne.clear", {"action": _bound_action(r["rows"])})
        _call("daphne.save", {})                        # disk now = the cleared map
        _call("daphne.load", {"scope": "global"})       # clean baseline = cleared map
        rd = _call("daphne.reset_defaults", {})
        self.assertTrue(rd["dirty"])                    # defaults != the saved (cleared) map


if __name__ == "__main__":
    unittest.main()
