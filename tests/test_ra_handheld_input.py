"""On-the-go handheld RetroArch hotkey COMBOS (lib/ra_handheld_input.py).

Handheld apply binds the Deck-pad hotkey combos (R3 modifier + L1/R1/Select/R2), clears the
X-Arcade pad-hotkey buttons + the Start+Select menu combo, and snapshots the resting values;
restore reverts them exactly; docked/disabled are no-ops; a crash orphan self-heals. Temp
retroarch.cfg + MAD_FORCE_CONTEXT. Run: python3 -m unittest tests.test_ra_handheld_input -v
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lib import ra_handheld_input as rhi
from lib import retroarch_cfg

_RESTING = """\
input_enable_hotkey_btn = "6"
input_rewind_btn = "6"
input_hold_fast_forward_btn = "5"
input_menu_toggle_btn = "nul"
input_toggle_slowmotion_axis = "nul"
input_toggle_slowmotion_btn = "7"
input_menu_toggle_gamepad_combo = "4"
input_rewind = "nul"
input_hold_fast_forward = "nul"
input_toggle_slowmotion = "nul"
input_player1_a_btn = "0"
input_player1_b_btn = "1"
input_player1_up_btn = "13"
input_player1_down_btn = "14"
input_player1_left_btn = "11"
input_player1_right_btn = "12"
input_player1_start_btn = "11"
input_player1_select_btn = "10"
input_player1_l_btn = "6"
input_player1_r_btn = "7"
input_player1_x_btn = "3"
input_player1_y_btn = "4"
input_player1_l3_btn = "nul"
input_player1_r3_btn = "nul"
input_player1_l_x_plus_axis = "nul"
input_player1_l_x_minus_axis = "nul"
input_player1_l_y_plus_axis = "nul"
input_player1_l_y_minus_axis = "nul"
input_player1_r_x_plus_axis = "nul"
input_player1_r_x_minus_axis = "nul"
input_player1_r_y_plus_axis = "nul"
input_player1_r_y_minus_axis = "nul"
input_player1_l2_axis = "nul"
input_player1_r2_axis = "nul"
"""

_HANDHELD = {  # what apply() should write (combos + corrected sdl2 gameplay binds)
    "input_enable_hotkey_btn": "8", "input_rewind_btn": "9",
    "input_hold_fast_forward_btn": "10", "input_menu_toggle_btn": "4",
    "input_toggle_slowmotion_axis": "+5", "input_toggle_slowmotion_btn": "nul",
    "input_menu_toggle_gamepad_combo": "0",
    "input_rewind": "nul", "input_hold_fast_forward": "nul", "input_toggle_slowmotion": "nul",
    # corrected gameplay binds = RetroArch's own Set-All-Controls capture (SDL GameController order)
    "input_player1_a_btn": "0", "input_player1_b_btn": "1",
    "input_player1_x_btn": "2", "input_player1_y_btn": "3",
    "input_player1_up_btn": "11", "input_player1_down_btn": "12",
    "input_player1_left_btn": "13", "input_player1_right_btn": "14",
    "input_player1_start_btn": "6", "input_player1_select_btn": "4",
    "input_player1_l_btn": "9", "input_player1_r_btn": "10",
    "input_player1_l3_btn": "7", "input_player1_r3_btn": "8",
    "input_player1_r_x_plus_axis": "+2", "input_player1_r_y_plus_axis": "+3",
}


def _pol(enabled=True):
    return {"handheld": {"enabled": enabled, "retroarch": {
        "modifier_btn": 8, "rewind_btn": 9, "fast_forward_btn": 10,
        "menu_btn": 4, "slowmotion_axis": "+5"}}}


class RaHandheldInput(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.cfg = self.d / "retroarch.cfg"
        self.cfg.write_text(_RESTING)
        self.sidecar = self.d / ".mad-ra-hotkeys-restore"
        self.bak = self.d / "retroarch.cfg.mad-bak"          # absent unless a test creates it
        self._p1 = mock.patch.object(retroarch_cfg, "RA_GLOBAL_CFG", self.cfg)
        self._p2 = mock.patch.object(rhi, "SIDECAR", self.sidecar)
        self._p3 = mock.patch.object(retroarch_cfg, "_GLOBAL_BAK", self.bak)
        # PAD_OVERRIDES is computed at import from the real path; isolate it so a stray real
        # sidecar can't perturb the default-binds tests, and the WS-C override tests stay hermetic.
        self._p4 = mock.patch.object(rhi, "PAD_OVERRIDES",
                                     self.d / ".mad-ra-handheld-pad-overrides.json")
        self._p1.start(); self._p2.start(); self._p3.start(); self._p4.start()
        os.environ["MAD_FORCE_CONTEXT"] = "handheld"

    def tearDown(self):
        self._p1.stop(); self._p2.stop(); self._p3.stop(); self._p4.stop()
        os.environ.pop("MAD_FORCE_CONTEXT", None)
        shutil.rmtree(self.d, ignore_errors=True)

    def _v(self, key):
        return retroarch_cfg.get_global_option(key)

    def _apply(self, policy):
        with mock.patch.object(rhi, "_load_policy", lambda: policy):
            return rhi.apply()

    def _restore(self, policy):
        with mock.patch.object(rhi, "_load_policy", lambda: policy):
            return rhi.restore()

    # ── apply / restore ─────────────────────────────────────────────────────
    def test_apply_sets_all_and_snapshots(self):
        self._apply(_pol())
        for k, v in _HANDHELD.items():
            self.assertEqual(self._v(k), v, f"{k} should be handheld value")
        self.assertTrue(self.sidecar.is_file())
        snap = json.loads(self.sidecar.read_text())
        self.assertEqual(snap["input_enable_hotkey_btn"], "6")      # resting captured
        self.assertEqual(snap["input_hold_fast_forward_btn"], "5")
        self.assertEqual(snap["input_menu_toggle_gamepad_combo"], "4")

    # --- WS-C: editable gameplay-pad overrides ---
    def test_pad_override_wins(self):
        rhi.save_pad_overrides({"input_player1_a_btn": "9"})     # RetroPad A <- Deck L1
        self._apply(_pol())
        self.assertEqual(self._v("input_player1_a_btn"), "9")    # override applied
        self.assertEqual(self._v("input_player1_b_btn"), "1")    # untouched key = shipped default

    def test_pad_override_restore_agnostic(self):
        rhi.save_pad_overrides({"input_player1_a_btn": "9"})
        self._apply(_pol())
        self.assertTrue(self._restore(_pol()))
        self.assertEqual(self._v("input_player1_a_btn"), "0")    # reverted to resting, not the override

    def test_override_out_of_domain_value_dropped(self):
        # a hand-edited sidecar value that is not a real Deck-control token (out-of-range index,
        # bool, garbage) must be DROPPED -> reverts to the shipped default, never silently applied
        # (an out-of-range sdl2 index would leave that button UNBOUND handheld).
        rhi.PAD_OVERRIDES.write_text(
            '{"input_player1_a_btn": "99", "input_player1_b_btn": true, "input_player1_x_btn": "9"}')
        self.assertEqual(rhi.load_pad_overrides(), {"input_player1_x_btn": "9"})   # only the valid one
        self._apply(_pol())
        self.assertEqual(self._v("input_player1_a_btn"), "0")   # shipped default, not "99"
        self.assertEqual(self._v("input_player1_x_btn"), "9")   # valid override applied

    def test_override_bad_key_cannot_crash(self):
        # a hand-corrupted sidecar with a key outside _GAMEPAD must be filtered, never reach
        # apply()'s _SAFE_RESTING[k] index (which would KeyError and break the launch).
        rhi.PAD_OVERRIDES.write_text('{"input_player1_bogus_btn": "9", "input_player1_a_btn": "2"}')
        self.assertEqual(rhi.load_pad_overrides(), {"input_player1_a_btn": "2"})
        self._apply(_pol())                                      # must not raise
        self.assertEqual(self._v("input_player1_a_btn"), "2")

    def test_restore_exact_revert(self):
        before = self.cfg.read_text()
        self._apply(_pol())
        self.assertTrue(self._restore(_pol()))
        self.assertEqual(self._v("input_enable_hotkey_btn"), "6")
        self.assertEqual(self._v("input_hold_fast_forward_btn"), "5")
        self.assertEqual(self._v("input_toggle_slowmotion_btn"), "7")
        self.assertEqual(self._v("input_menu_toggle_gamepad_combo"), "4")
        self.assertFalse(self.sidecar.exists())
        self.assertEqual(self.cfg.read_text(), before)              # byte-identical resting cfg

    # ── no-ops ──────────────────────────────────────────────────────────────
    def test_docked_noop(self):
        os.environ["MAD_FORCE_CONTEXT"] = "docked"
        before = self.cfg.read_text()
        self.assertEqual(self._apply(_pol()), "docked -> no RA hotkey combos")
        self.assertEqual(self.cfg.read_text(), before)
        self.assertFalse(self.sidecar.exists())

    def test_feature_disabled_noop(self):
        before = self.cfg.read_text()
        self._apply(_pol(enabled=False))
        self.assertEqual(self.cfg.read_text(), before)
        self.assertFalse(self.sidecar.exists())

    def test_already_applied_noop(self):
        self._apply(_pol())
        self.sidecar.unlink()                        # cfg already handheld, no sidecar
        msg = self._apply(_pol())
        self.assertIn("already applied", msg)
        self.assertFalse(self.sidecar.exists())

    # ── crash-orphan self-heal ──────────────────────────────────────────────
    def test_orphan_reapply_keeps_resting_snapshot(self):
        self._apply(_pol())                          # cfg=handheld, sidecar=resting
        self._apply(_pol())                          # crash relaunch (still handheld): sweep + re-apply
        self.assertEqual(self._v("input_enable_hotkey_btn"), "8")   # re-applied
        snap = json.loads(self.sidecar.read_text())
        self.assertEqual(snap["input_enable_hotkey_btn"], "6")      # snapshot is RESTING, not 8

    def test_orphan_swept_on_docked_relaunch(self):
        self._apply(_pol())                          # cfg=handheld, sidecar=resting
        os.environ["MAD_FORCE_CONTEXT"] = "docked"
        self._apply(_pol())                          # restore-first sweeps the orphan
        self.assertEqual(self._v("input_enable_hotkey_btn"), "6")
        self.assertEqual(self._v("input_menu_toggle_gamepad_combo"), "4")
        self.assertFalse(self.sidecar.exists())

    def test_corrupt_sidecar_restores_from_bak(self):
        # apply() writes binds -> retroarch_cfg makes a one-time .mad-bak of the RESTING cfg. A
        # corrupt sidecar must recover the resting values FROM that backup, NOT nul the real binds.
        self._apply(_pol())
        self.assertTrue(self.bak.is_file())          # .mad-bak captured on the first write
        self.sidecar.write_text("{ not json")        # corrupt
        self.assertFalse(self._restore(_pol()))
        self.assertEqual(self._v("input_enable_hotkey_btn"), "6")    # resting hotkey (from bak)
        self.assertEqual(self._v("input_player1_a_btn"), "0")        # resting gameplay bind
        self.assertEqual(self._v("input_player1_up_btn"), "13")      # X-Arcade d-pad NOT nul'd
        self.assertFalse(self.sidecar.exists())

    def test_corrupt_sidecar_no_bak_spares_gameplay(self):
        # No .mad-bak (impossible in the real flow, but defend it): reset only the hotkey/combo keys
        # to safe defaults; leave the gameplay binds UNTOUCHED so they can never be nul'd blind.
        self.assertFalse(self.bak.exists())
        self.sidecar.write_text("{ not json")        # corrupt, and no prior apply -> no bak
        self.assertFalse(self._restore(_pol()))
        self.assertEqual(self._v("input_enable_hotkey_btn"), "nul")  # hotkey reset to safe default
        self.assertEqual(self._v("input_menu_toggle_gamepad_combo"), "0")
        self.assertEqual(self._v("input_player1_a_btn"), "0")        # gameplay left as-is
        self.assertEqual(self._v("input_player1_up_btn"), "13")
        self.assertFalse(self.sidecar.exists())

    def test_absent_key_reverts_to_safe_default(self):
        # a resting cfg MISSING input_menu_toggle_btn: apply APPENDS it, restore must still revert.
        self.cfg.write_text('input_enable_hotkey_btn = "6"\ninput_rewind_btn = "6"\n'
                             'input_hold_fast_forward_btn = "5"\ninput_toggle_slowmotion_btn = "7"\n'
                             'input_menu_toggle_gamepad_combo = "4"\n')   # NO input_menu_toggle_btn
        self._apply(_pol())
        self.assertEqual(self._v("input_menu_toggle_btn"), "4")     # appended by apply
        self.assertTrue(self._restore(_pol()))
        self.assertEqual(self._v("input_menu_toggle_btn"), "nul")   # reverted to the safe default


if __name__ == "__main__":
    unittest.main()
