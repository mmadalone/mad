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
input_exit_emulator_btn = "nul"
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
    "input_exit_emulator_btn": "6",   # WS-G: + modifier -> quit
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


def _pol_dev(device=None, adp=None, enabled=True):
    """A policy that also carries the optional P1 device-type / analog-to-D-pad handheld settings."""
    ra = {"modifier_btn": 8, "rewind_btn": 9, "fast_forward_btn": 10,
          "menu_btn": 4, "slowmotion_axis": "+5"}
    if device is not None:
        ra["device_p1"] = device
    if adp is not None:
        ra["analog_dpad_p1"] = adp
    return {"handheld": {"enabled": enabled, "retroarch": ra}}


class RaHandheldInput(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.cfg = self.d / "retroarch.cfg"
        self.cfg.write_text(_RESTING)
        self.sidecar = self.d / ".mad-ra-hotkeys-restore"
        # NOTE: the backup path is DERIVED from RA_GLOBAL_CFG (retroarch_cfg._global_bak),
        # so patching the cfg into this tmpdir redirects the backup with it. There is
        # deliberately nothing to patch for it -- see test_the_backup_cannot_escape_a_patched_cfg.
        self.bak = self.d / "retroarch.cfg.mad-bak"          # absent unless a test creates it
        self.baseline = self.d / ".mad-ra-resting-baseline"  # refreshed by apply()
        self._patches = [
            mock.patch.object(retroarch_cfg, "RA_GLOBAL_CFG", self.cfg),
            mock.patch.object(rhi, "SIDECAR", self.sidecar),
            mock.patch.object(rhi, "BASELINE", self.baseline),
            # PAD_OVERRIDES is computed at import from the real path; isolate it so a stray real
            # sidecar can't perturb the default-binds tests, and the WS-C override tests stay hermetic.
            mock.patch.object(rhi, "PAD_OVERRIDES",
                              self.d / ".mad-ra-handheld-pad-overrides.json"),
        ]
        for p in self._patches:
            p.start()
        os.environ["MAD_FORCE_CONTEXT"] = "handheld"

    def tearDown(self):
        for p in self._patches:
            p.stop()
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
        # corrupt sidecar must recover the resting values, NOT nul the real binds.
        self._apply(_pol())
        self.assertTrue(self.bak.is_file())          # .mad-bak captured on the first write
        self.sidecar.write_text("{ not json")        # corrupt
        self.assertFalse(self._restore(_pol()))
        self.assertEqual(self._v("input_enable_hotkey_btn"), "6")    # resting hotkey
        self.assertEqual(self._v("input_player1_a_btn"), "0")        # resting gameplay bind
        self.assertEqual(self._v("input_player1_up_btn"), "13")      # X-Arcade d-pad NOT nul'd
        self.assertFalse(self.sidecar.exists())

    def test_the_backup_cannot_escape_a_patched_cfg(self):
        # THE LEAK (found 2026-07-17, on the user's live rig). The backup path used to
        # be its own module global, so it did NOT follow RA_GLOBAL_CFG: a test pointing
        # the config at a tmpdir still backed up to the REAL
        # ~/.var/app/org.libretro.RetroArch/.../retroarch.cfg.mad-bak, and with the
        # user's backup absent one suite run wrote a 28-byte fixture over it.
        # Deriving the path makes it structurally impossible. Assert the DERIVED path
        # lands beside the patched cfg and the real one is never named.
        self._apply(_pol())
        self.assertTrue(self.bak.is_file(), "the backup did not follow the patched cfg")
        real = Path.home() / ".var/app/org.libretro.RetroArch/config/retroarch/retroarch.cfg.mad-bak"
        self.assertEqual(retroarch_cfg._global_bak(), self.bak)
        self.assertNotEqual(retroarch_cfg._global_bak(), real,
                            "a test can still reach the user's real RetroArch backup")

    def test_apply_refreshes_the_resting_baseline(self):
        self.assertFalse(self.baseline.exists())
        self._apply(_pol())
        self.assertTrue(self.baseline.is_file(), "apply() left no resting baseline")
        snap = json.loads(self.baseline.read_text())
        # The DOCKED resting values, captured before the handheld writes -- not the handheld ones.
        self.assertEqual(snap["input_player1_up_btn"], "13")
        self.assertEqual(snap["input_enable_hotkey_btn"], "6")
        self.assertNotEqual(snap["input_player1_up_btn"], rhi._GAMEPAD["input_player1_up_btn"],
                            "the baseline captured the handheld values, not the resting ones")

    def test_the_baseline_survives_game_end(self):
        # It is the recovery net for the NEXT launch: deleting it with the sidecar would leave
        # nothing but the frozen pre-MAD .mad-bak to fall back on.
        self._apply(_pol())
        self.assertTrue(self._restore(_pol()))
        self.assertFalse(self.sidecar.exists())
        self.assertTrue(self.baseline.is_file(), "game-end dropped the resting baseline")

    def test_a_stale_mad_bak_can_no_longer_resurrect_old_binds(self):
        # THE LANDMINE (2026-07-17). retroarch.cfg.mad-bak is frozen at MAD's FIRST edit and never
        # refreshed, so it drifts: by 2026-07-17 it still held June's pre-6.16 d-pad (up=13), which
        # a kernel change had since turned into "left". Recovering from it would have restored a
        # ROTATED stick, silently, months after those values stopped being true. The refreshed
        # baseline must win over it.
        self._apply(_pol())                          # writes the baseline (up=13, today's truth)
        self.assertTrue(self._restore(_pol()))
        # The user rebinds in RetroArch: up now means 11. Re-apply so the baseline tracks it.
        retroarch_cfg.set_global_option("input_player1_up_btn", "11")
        self._apply(_pol())
        # ...while the frozen .mad-bak still says 13 -- exactly the real-world drift.
        self.assertEqual(retroarch_cfg.read_global_bak_options(
            ["input_player1_up_btn"])["input_player1_up_btn"], "13")
        self.sidecar.write_text("{ not json")        # corrupt
        self.assertFalse(self._restore(_pol()))
        self.assertEqual(self._v("input_player1_up_btn"), "11",
                         "recovery resurrected a stale bind from the frozen pre-MAD backup")

    def test_a_corrupt_baseline_falls_back_and_never_crashes(self):
        self._apply(_pol())
        self.baseline.write_text("{ not json either")
        self.sidecar.write_text("{ not json")
        self.assertFalse(self._restore(_pol()))
        self.assertEqual(self._v("input_player1_up_btn"), "13",   # from the .mad-bak
                         "a corrupt baseline must fall back, not nul a gameplay bind")

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

    # ── optional P1 device-type / analog-to-D-pad globals ────────────────────
    def test_device_settings_applied_and_reverted(self):
        # set -> apply writes the two globals; the snapshot records RA's own defaults (both keys are
        # ABSENT in the resting cfg), so restore reverts them to those defaults, not to a stale value.
        self._apply(_pol_dev(device="5", adp="1"))
        self.assertEqual(self._v("input_libretro_device_p1"), "5")
        self.assertEqual(self._v("input_player1_analog_dpad_mode"), "1")
        snap = json.loads(self.sidecar.read_text())
        self.assertEqual(snap["input_libretro_device_p1"], "1")           # RA default (absent at rest)
        self.assertEqual(snap["input_player1_analog_dpad_mode"], "0")
        self.assertTrue(self._restore(_pol_dev(device="5", adp="1")))
        self.assertEqual(self._v("input_libretro_device_p1"), "1")
        self.assertEqual(self._v("input_player1_analog_dpad_mode"), "0")

    def test_device_settings_omitted_on_inherit(self):
        # no device_p1 / analog_dpad_p1 in policy = inherit -> the globals are never written.
        self._apply(_pol())
        self.assertIsNone(self._v("input_libretro_device_p1"))
        self.assertIsNone(self._v("input_player1_analog_dpad_mode"))

    def test_device_settings_garbage_dropped(self):
        # an out-of-domain hand-edit is dropped -> treated as inherit (never binds a bogus device id).
        self._apply(_pol_dev(device="99", adp="nope"))
        self.assertIsNone(self._v("input_libretro_device_p1"))
        self.assertIsNone(self._v("input_player1_analog_dpad_mode"))

    def test_device_lightgun_mouse_not_applied(self):
        # Light gun (4) / Mouse (2) are nonsense for the Deck's built-in gamepad and the editor no
        # longer offers them; a stale stored id must NOT be applied globally at launch.
        for stale in ("4", "2"):
            self._apply(_pol_dev(device=stale))
            self.assertIsNone(self._v("input_libretro_device_p1"), f"device={stale} must be dropped")
            self._restore(_pol_dev(device=stale))     # clean up the sidecar between iterations

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
