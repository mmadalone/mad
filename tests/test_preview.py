"""
Tests for the MAD Preview page rendering (lib.standalone_preview +
lib.madsrv.preview_cmds._route_one). Pure config/logic — no real SDL/evdev.

Covers the three on-device bugs fixed 2026-07-01:
  1. PS2 (pcsx2) showed "no PlayStation pad (SDL-N)" — now previews the router's
     real would-bind pads (switch_bind._resolve_pads).
  2. PS3 (rpcs3) showed empty players as 'Null' rows + quoted labels — RPCS3
     serialises Handler/Device single-quoted; the Null-skip + labels now strip quotes.
  3. The clipped "X-Arcade trackball — RA mouse N (red-button hotkey)" row is removed.

Run:  python3 -m unittest tests.test_preview -v
"""
from __future__ import annotations

import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from lib import standalone_preview, switch_bind
from lib.madsrv import pads_cmds, preview_cmds
from tests._fakes import sd

DS5 = "054c:0ce6"
DS4 = "054c:09cc"


class Rpcs3QuoteStrip(unittest.TestCase):
    """PS3 preview: single-quoted 'Null' must skip, and labels must render unquoted."""

    YML = (
        "Player 1 Input:\n"
        "  Handler: DualSense\n"
        "  Device: 'DualSense Pad #1'\n"
        "Player 2 Input:\n"
        "  Handler: DualShock 4\n"
        "  Device: 'DS4 Pad #1'\n"
        "Player 3 Input:\n"
        "  Handler: 'Null'\n"
        "  Device: 'Null'\n"
        "Player 4 Input:\n"
        "  Handler: 'Null'\n"
        "  Device: 'Null'\n"
    )

    def _preview(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "Default.yml"
            p.write_text(self.YML, encoding="utf-8")
            merged = {"backends": {"rpcs3": {"config_file": str(p)}}}
            return standalone_preview.standalone_profile_preview("rpcs3", merged)

    def test_null_players_are_hidden(self):
        kind, rows = self._preview()
        self.assertEqual(kind, "pads")
        slots = [r[0] for r in rows]
        self.assertEqual(slots, ["P1", "P2"])            # P3/P4 (Null) dropped
        self.assertFalse(any("Null" in r[1] for r in rows))

    def test_labels_have_no_quotes(self):
        _, rows = self._preview()
        texts = {r[0]: r[1] for r in rows}
        self.assertEqual(texts["P1"], "DualSense Pad #1")  # not 'DualSense Pad #1'
        self.assertEqual(texts["P2"], "DS4 Pad #1")


class Pcsx2RouterPreview(unittest.TestCase):
    """PS2 preview: renders switch_bind._resolve_pads (the real would-bind), not the
    stale PCSX2.ini SDL index (which produced 'no PlayStation pad')."""

    MERGED = {"systems": {"ps2": {"backend": "pcsx2"}}}

    def _route(self, chosen):
        with mock.patch.object(switch_bind, "_resolve_pads", return_value=chosen):
            return preview_cmds._route_one(
                "ps2", "system", self.MERGED, {}, "1.1", [], [], 0)

    def test_shows_resolved_pads(self):
        chosen = [sd(2, DS5, "", "DualSense Wireless Controller"),
                  sd(3, DS5, "", "DualSense Wireless Controller")]
        r = self._route(chosen)
        self.assertEqual(r["kind"], "pads")
        self.assertEqual([row["slot"] for row in r["rows"]], ["P1", "P2"])
        for row in r["rows"]:
            self.assertNotIn("no PlayStation pad", row["text"])
            self.assertTrue(row["text"])                 # a real pad label

    def test_no_pads_connected(self):
        r = self._route([])
        self.assertEqual(r["kind"], "text")
        self.assertIn("no player pad", r["text"])

    def test_preview_resolution_is_quiet(self):
        """The PS2 preview must NOT write to router.log: _resolve_pads(quiet=True) suppresses
        the _dbg/_log narration (pcsx2 has a handheld_class, so it would otherwise always log)."""
        fake = [sd(0, DS5, "", "DualSense")]
        with mock.patch.object(pads_cmds, "_real_pads", return_value=fake), \
             mock.patch.object(pads_cmds, "_supported", return_value=fake), \
             mock.patch.object(pads_cmds, "_ordered", return_value=fake), \
             mock.patch.object(pads_cmds, "_handheld_class", return_value="28de:1205"), \
             mock.patch.object(pads_cmds, "managed_players", return_value=2), \
             mock.patch.object(switch_bind, "_log") as mlog, \
             mock.patch.object(switch_bind, "_dbg") as mdbg:
            switch_bind._resolve_pads("pcsx2", quiet=True)
            mlog.assert_not_called()
            mdbg.assert_not_called()
            switch_bind._resolve_pads("pcsx2")          # launch path still narrates
            self.assertTrue(mlog.called)

    def _route_045e(self, port, xport="1.1"):
        """Route a 045e:02a1 pad (X-Arcade / Xbox-360 share this vid:pid) whose evdev twin
        sits at `port`; xport is the identified X-Arcade port."""
        pad = [sd(1, "045e:02a1", "", "Xbox 360 Wireless Controller")]
        twin = {1: types.SimpleNamespace(phys="usb-x")}
        with mock.patch.object(switch_bind, "_resolve_pads", return_value=pad), \
             mock.patch.object(preview_cmds, "evdev_by_sdl_index", return_value=twin), \
             mock.patch.object(preview_cmds.dv, "port_of", return_value=port):
            return preview_cmds._route_one(
                "ps2", "system", self.MERGED, {}, xport, ["d"], pad, 0)

    def test_xarcade_named_by_port(self):
        r = self._route_045e(port="1.1", xport="1.1")     # 045e AT the identified port
        self.assertEqual(r["rows"][0]["text"], "X-Arcade")

    def test_real_xbox360_not_relabeled(self):
        r = self._route_045e(port="2.3", xport="1.1")     # 045e at a DIFFERENT port
        self.assertEqual(r["rows"][0]["text"], "Xbox 360")


class Icons(unittest.TestCase):
    """An 'X-Arcade' label must always carry the X-Arcade icon, in any position."""

    @staticmethod
    def _stem(p):
        return (p or "").rsplit("/", 1)[-1]

    def test_xarcade_icon_matches_label_in_any_position(self):
        from lib.madsrv.systems_cmds import device_icon_path
        for label in ("X-Arcade", "X-Arcade P1", "WiiU X-Arcade P6", "X-Arcade P8"):
            self.assertEqual(self._stem(device_icon_path(label)), "xarcade.png", label)

    def test_real_xbox360_does_not_get_xarcade_icon(self):
        from lib.madsrv.systems_cmds import device_icon_path
        self.assertNotEqual(self._stem(device_icon_path("Xbox 360")), "xarcade.png")

    def test_row_icon_prefers_xarcade_label_over_device_hint(self):
        # Eden/Cemu rows carry a device hint of "Xbox 360" for the X-Arcade (045e:02a1);
        # the label must win so the icon matches what the user sees.
        self.assertEqual(preview_cmds._row_icon_name(
            {"text": "X-Arcade P7", "icon": "Xbox 360"}), "X-Arcade P7")
        self.assertEqual(preview_cmds._row_icon_name(
            {"text": "WiiU X-Arcade P6", "icon": "Xbox 360"}), "WiiU X-Arcade P6")

    def test_row_icon_keeps_device_hint_for_non_xarcade(self):
        self.assertEqual(preview_cmds._row_icon_name(
            {"text": "Switch Dualsense P3", "icon": "DualSense"}), "DualSense")
        self.assertEqual(preview_cmds._row_icon_name({"text": "X-Arcade"}), "X-Arcade")


class HotkeyRowRemoved(unittest.TestCase):
    """The clipped 'X-Arcade trackball — RA mouse N (red-button hotkey)' row is gone;
    the Sinden Gun 1/Gun 2 rows for a require_sinden RetroArch system still render."""

    def test_sinden_rows_kept_no_trackball_hotkey_row(self):
        merged = {"systems": {"mamelg": {}}}             # no backend -> RetroArch path
        with mock.patch.object(preview_cmds, "resolve_policy",
                               return_value={"ports": ["x"], "require_sinden": True}), \
             mock.patch.object(preview_cmds, "resolve_pins", return_value=({}, set())), \
             mock.patch.object(preview_cmds, "resolve_ports", return_value={}), \
             mock.patch("lib.devices.detect_sinden_mouse_indices",
                        return_value=(3, 4, True)):
            r = preview_cmds._route_one(
                "mamelg", "system", merged, {}, "1.1", [], [], 0)
        self.assertEqual(r["kind"], "pads")
        texts = [row["text"] for row in r["rows"]]
        self.assertTrue(any("Sinden P1" in t for t in texts))
        self.assertTrue(any("Sinden P2" in t for t in texts))
        self.assertFalse(any("trackball" in t or "hotkey" in t for t in texts))

    def test_source_has_no_trackball_hotkey_row(self):
        src = Path(preview_cmds.__file__).read_text(encoding="utf-8")
        self.assertNotIn("red-button hotkey", src)
        self.assertNotIn("X-Arcade trackball", src)


if __name__ == "__main__":
    unittest.main()
