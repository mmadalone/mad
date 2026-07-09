"""Per-game AR / Gecko codes (dolphin_codes_cmds): union of bundled DB + user codes, toggle on/off.

Verifies: enumerating the union of bundled + user codes; multi-file union (the concat-bug regression -
codes in a LATER bundled file must not be dropped); toggle ON adds the name to [<Section>_Enabled] and
copies the body into the user [<Section>]; toggle OFF removes the name; empty game -> empty list;
has_codes; running-guard.

Run:  python3 -m unittest tests.test_dolphin_codes -v
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from lib import dolphin_gameids as gids
from lib import proc_guard
from lib.madsrv import dolphin_codes_cmds as codes
from lib.madsrv.dolphin_codes_cmds import _enabled, _names
from lib.madsrv.rpc import RpcError

_BUNDLED = ("[ActionReplay]\n$Code A [x]\n041CD8A8 4E800020\n$Code B [y]\n041E1390 40800430\n"
            "[Gecko]\n$G1\n041bfa20 38600002\n")


class Codes(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._save = (gids.bundled_chain, gids.user_ini, proc_guard.emulator_running)
        proc_guard.emulator_running = lambda *a, **k: False
        self.bundled = self.tmp / "bundled.ini"
        self.bundled.write_text(_BUNDLED)
        self.user = self.tmp / "TEST01.ini"
        gids.bundled_chain = lambda gid: [self.bundled]
        gids.user_ini = lambda gid: self.user

    def tearDown(self):
        gids.bundled_chain, gids.user_ini, proc_guard.emulator_running = self._save
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_enumerate_union_none_enabled(self):
        r = codes._get("TEST01", "ActionReplay", "AR")
        rows = r["groups"][0]["settings"]
        self.assertEqual([s["label"] for s in rows], ["Code A [x]", "Code B [y]"])
        self.assertTrue(all(s["value"] is False for s in rows))       # nothing enabled by default

    def test_multi_file_union(self):
        # regression: a SECOND bundled file with its own [ActionReplay] must not be dropped
        b2 = self.tmp / "b2.ini"
        b2.write_text("[ActionReplay]\n$Code C [z]\n00000000 00000000\n")
        gids.bundled_chain = lambda gid: [self.bundled, b2]
        rows = codes._get("TEST01", "ActionReplay", "AR")["groups"][0]["settings"]
        self.assertEqual([s["label"] for s in rows], ["Code A [x]", "Code B [y]", "Code C [z]"])

    def test_toggle_on_enables_without_copying_body(self):
        # Dolphin never copies a Sys code's body into the user file -- only the $Name goes to _Enabled.
        codes._set("TEST01", "ActionReplay", {"key": "code:Code A [x]", "value": True})
        t = self.user.read_text()
        self.assertEqual(_enabled(t, "ActionReplay"), ["Code A [x]"])   # name -> [ActionReplay_Enabled]
        self.assertNotIn("041CD8A8 4E800020", t)                       # body NOT copied (stays in Sys)
        self.assertEqual(_names(t, "ActionReplay"), [])                # no [ActionReplay] body in user file
        row = next(s for s in codes._get("TEST01", "ActionReplay", "AR")["groups"][0]["settings"]
                   if s["label"] == "Code A [x]")
        self.assertTrue(row["value"])                                  # shows enabled (union with Sys)

    def test_toggle_off_a_default_off_code_writes_neither_list(self):
        from lib.madsrv.dolphin_codes_cmds import _disabled
        codes._set("TEST01", "ActionReplay", {"key": "code:Code A [x]", "value": True})
        codes._set("TEST01", "ActionReplay", {"key": "code:Code A [x]", "value": False})
        t = self.user.read_text()
        self.assertEqual(_enabled(t, "ActionReplay"), [])              # default is off -> matches default
        self.assertEqual(_disabled(t, "ActionReplay"), [])            # ... so it's in NEITHER list

    def test_has_codes_and_empty(self):
        self.assertTrue(codes.has_codes("TEST01", "ActionReplay"))
        self.assertTrue(codes.has_codes("TEST01", "Gecko"))
        gids.bundled_chain = lambda gid: []                            # no bundled, empty user
        self.assertFalse(codes.has_codes("TEST01", "ActionReplay"))
        self.assertEqual(codes._get("TEST01", "ActionReplay", "AR")["groups"], [])

    def test_unknown_code_rejected(self):
        with self.assertRaises(RpcError):
            codes._set("TEST01", "ActionReplay", {"key": "code:Nope", "value": True})

    def test_net_empty_toggle_is_byte_stable(self):
        # a toggle whose net _Enabled/_Disabled lists are empty, on a user file with NO such sections,
        # must be a true no-op (no stray trailing newline, no spurious rewrite).
        self.user.write_text("[Gecko]\n$MyCode\n1234ABCD 00000001\n")
        before = self.user.read_bytes()
        self.bundled.write_text("")                                    # so MyCode is a user code, default-off
        codes._set("TEST01", "Gecko", {"key": "code:MyCode", "value": False})   # off == default -> neither
        self.assertEqual(self.user.read_bytes(), before)               # byte-identical

    def test_running_guard(self):
        proc_guard.emulator_running = lambda *a, **k: True
        with self.assertRaises(RpcError) as cm:
            codes._set("TEST01", "ActionReplay", {"key": "code:Code A [x]", "value": True})
        self.assertEqual(cm.exception.code, "EBUSY")

    def test_gecko_canonical_name_drops_creator(self):
        # Gecko identity = text before '['; the _Enabled list stores the canonical name, the [Gecko]
        # block body keeps the FULL "$Name [creator]" header (matches Dolphin).
        self.bundled.write_text("[Gecko]\n$SSL Patch [Palapeli]\n041bfa20 38600002\n")
        rows = codes._get("TEST01", "Gecko", "Gecko")["groups"][0]["settings"]
        self.assertEqual([s["label"] for s in rows], ["SSL Patch"])
        codes._set("TEST01", "Gecko", {"key": "code:SSL Patch", "value": True})
        t = self.user.read_text()
        self.assertEqual(_enabled(t, "Gecko"), ["SSL Patch"])          # canonical name -> _Enabled
        self.assertNotIn("Palapeli", t)                                # Sys body NOT copied into user file

    def test_bundled_default_enabled_shown_on_and_disable_via_disabled(self):
        from lib.madsrv.dolphin_codes_cmds import _disabled
        self.bundled.write_text("[Gecko]\n$X\n00000000 00000000\n[Gecko_Enabled]\n$X\n")
        row = next(s for s in codes._get("TEST01", "Gecko", "Gecko")["groups"][0]["settings"]
                   if s["label"] == "X")
        self.assertTrue(row["value"])                                  # bundled default -> shown ON
        codes._set("TEST01", "Gecko", {"key": "code:X", "value": False})
        self.assertIn("X", _disabled(self.user.read_text(), "Gecko"))  # OFF writes [Gecko_Disabled]
        row2 = next(s for s in codes._get("TEST01", "Gecko", "Gecko")["groups"][0]["settings"]
                    if s["label"] == "X")
        self.assertFalse(row2["value"])                                # now shown OFF


if __name__ == "__main__":
    unittest.main()
