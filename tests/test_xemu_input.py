"""Tests for the xemu input-map writer (xemu_cfg gamepad_mappings splice) and the
xemu.input_get / xemu.input_set RPC contract. Pure given-text / temp-copy; no
hardware.

Run:  python3 -m unittest tests.test_xemu_input -v
"""
from __future__ import annotations

import shutil
import tempfile
import tomllib
import unittest
from pathlib import Path

from lib import inifile, xemu_cfg
from lib.madsrv import xemu_input_cmds
from lib.madsrv.rpc import RpcError

FIX = Path(__file__).parent / "fixtures" / "xemu" / "xemu_mappings.toml"
G_A = "0300aaaa0000000000000000000000aa"   # port1 pad (no controller_mapping)
G_B = "0300bbbb0000000000000000000000bb"   # port2 pad (controller_mapping a=1,b=0)
G_C = "0300cccc0000000000000000000000cc"   # third pad

# Deterministic expected [input] body after setting a=1 on the port1 pad (G_A) —
# the whole gamepad_mappings array is re-emitted in xemu's inline-table-array form
# (and the gamecontrollerdb_path scalar is preserved).
EXPECT_A1 = (
    "gamepad_mappings = [\n"
    f"    {{ gamepad_id = '{G_A}', controller_mapping = {{ a = 1 }} }},\n"
    f"    {{ gamepad_id = '{G_B}', controller_mapping = {{ a = 1, b = 0 }} }},\n"
    f"    {{ gamepad_id = '{G_C}' }},\n"
    "    ]\n"
    "gamecontrollerdb_path = '/home/deck/gamecontrollerdb.txt'"
)


class XemuWriter(unittest.TestCase):
    def setUp(self):
        self.text = FIX.read_text()

    def test_set_on_port1_pad_byte_stable(self):
        new = xemu_cfg.set_controller_mapping(self.text, G_A, "a", 1)
        self.assertEqual(inifile.section_body(new, "input"), EXPECT_A1)

    def test_valid_toml_and_target_mapping(self):
        new = xemu_cfg.set_controller_mapping(self.text, G_A, "a", 1)
        data = tomllib.loads(new)              # raises if invalid TOML
        gms = {e["gamepad_id"]: e for e in data["input"]["gamepad_mappings"]}
        self.assertEqual(gms[G_A].get("controller_mapping"), {"a": 1})

    def test_sibling_preserved(self):
        new = xemu_cfg.set_controller_mapping(self.text, G_A, "a", 1)
        gms = {e["gamepad_id"]: e for e in tomllib.loads(new)["input"]["gamepad_mappings"]}
        self.assertEqual(gms[G_B].get("controller_mapping"), {"a": 1, "b": 0})  # untouched
        self.assertNotIn("controller_mapping", gms[G_C])

    def test_merge_into_existing_mapping(self):
        new = xemu_cfg.set_controller_mapping(self.text, G_B, "x", 2)
        gms = {e["gamepad_id"]: e for e in tomllib.loads(new)["input"]["gamepad_mappings"]}
        self.assertEqual(gms[G_B]["controller_mapping"], {"a": 1, "b": 0, "x": 2})

    def test_seed_if_missing(self):
        fake = "ffffffffffffffffffffffffffffffff"
        new = xemu_cfg.set_controller_mapping(self.text, fake, "b", 7)
        gms = {e["gamepad_id"]: e for e in tomllib.loads(new)["input"]["gamepad_mappings"]}
        self.assertIn(fake, gms)
        self.assertEqual(gms[fake]["controller_mapping"], {"b": 7})

    def test_idempotent(self):
        once = xemu_cfg.set_controller_mapping(self.text, G_A, "a", 1)
        twice = xemu_cfg.set_controller_mapping(once, G_A, "a", 1)
        self.assertEqual(once, twice)

    def test_other_sections_byte_identical(self):
        new = xemu_cfg.set_controller_mapping(self.text, G_A, "a", 1)
        for sec in ("general", "input.bindings", "display.window"):
            self.assertEqual(inifile.section_body(self.text, sec),
                             inifile.section_body(new, sec), sec)

    def test_input_scalar_preserved(self):
        new = xemu_cfg.set_controller_mapping(self.text, G_A, "a", 1)
        self.assertEqual(tomllib.loads(new)["input"]["gamecontrollerdb_path"],
                         "/home/deck/gamecontrollerdb.txt")

    def test_read_inline_form(self):
        got = xemu_cfg.read_gamepad_mappings(self.text)
        self.assertEqual([e["gamepad_id"] for e in got], [G_A, G_B, G_C])

    def test_read_block_form(self):
        block = (
            "[input]\n"
            "[[input.gamepad_mappings]]\n"
            f"gamepad_id = '{G_A}'\n"
            "[input.gamepad_mappings.controller_mapping]\n"
            "a = 1\n"
        )
        self.assertEqual(xemu_cfg.read_gamepad_mappings(block),
                         [{"gamepad_id": G_A, "controller_mapping": {"a": 1}}])


class XemuRpc(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()) / "xemu.toml"
        shutil.copy2(FIX, self.tmp)
        self._file = xemu_input_cmds._FILE
        self._supp = xemu_input_cmds._supports_remap
        self._run = xemu_input_cmds.proc_guard.emulator_running
        xemu_input_cmds._FILE = self.tmp
        xemu_input_cmds._supports_remap = lambda: True
        xemu_input_cmds.proc_guard.emulator_running = lambda name: False
        xemu_input_cmds._buf.reset()               # fresh buffer per case (module-level singleton)
        # count staterev bumps so "commit writes exactly once" is provable
        import lib.staterev as sr
        self._bump = sr.bump
        self.bumps = 0
        def _count(_n):
            self.bumps += 1
        sr.bump = _count

    def tearDown(self):
        xemu_input_cmds._FILE = self._file
        xemu_input_cmds._supports_remap = self._supp
        xemu_input_cmds.proc_guard.emulator_running = self._run
        xemu_input_cmds._buf.reset()
        import lib.staterev as sr
        sr.bump = self._bump
        shutil.rmtree(self.tmp.parent, ignore_errors=True)

    def _set(self, **params):
        """Stage a capture then Save. The buffered editor only writes disk on save, so a
        test that asserts on file content must commit first."""
        r = xemu_input_cmds._input_set(params)
        xemu_input_cmds._input_save({})
        return r

    def test_input_get_buttons(self):
        res = xemu_input_cmds._input_get({})
        self.assertFalse(res["running"])
        binds = {b["id"]: b for g in res["groups"] for b in g["binds"]}
        # port1 pad has no controller_mapping → defaults: Xbox A driven by SDL A.
        self.assertEqual(binds["a"]["value"], "A")
        self.assertTrue(binds["a"]["capturable"])
        self.assertTrue(binds["dpad_up"]["capturable"])       # d-pad now remappable (Phase 1)

    def test_input_set_writes_mapping(self):
        # capture physical B (evdev 0x131 → SDL GC index 1) onto Xbox A
        res = self._set(id="a", value=0x131)
        self.assertEqual(res["value"], "B")
        gms = {e["gamepad_id"]: e
               for e in tomllib.loads(self.tmp.read_text())["input"]["gamepad_mappings"]}
        self.assertEqual(gms[G_A]["controller_mapping"], {"a": 1})

    def test_input_set_rejects_unmappable(self):
        with self.assertRaises(RpcError):
            xemu_input_cmds._input_set({"id": "a", "value": 0x2c0})   # not a button code

    def test_input_set_rejects_unknown_key(self):
        with self.assertRaises(RpcError):
            xemu_input_cmds._input_set({"id": "nope", "value": 0x130})

    def test_dpad_hat_writes_index(self):
        res = self._set(id="dpad_up", kind="hat", value="h0up")
        self.assertEqual(res["value"], "D-Up")
        gms = {e["gamepad_id"]: e
               for e in tomllib.loads(self.tmp.read_text())["input"]["gamepad_mappings"]}
        self.assertEqual(gms[G_A]["controller_mapping"], {"dpad_up": 11})

    def test_dpad_row_is_capturable_hat(self):
        res = xemu_input_cmds._input_get({})
        dpad = {b["id"]: b for g in res["groups"] if g["title"] == "D-pad" for b in g["binds"]}
        self.assertEqual(dpad["dpad_up"]["kind"], "hat")
        self.assertTrue(dpad["dpad_up"]["capturable"])

    def test_dpad_rejects_button_code(self):
        with self.assertRaises(RpcError):
            xemu_input_cmds._input_set({"id": "dpad_up", "kind": "hat", "value": "0x130"})

    def _cm(self):
        gms = tomllib.loads(self.tmp.read_text())["input"]["gamepad_mappings"]
        return {e["gamepad_id"]: e for e in gms}[G_A]["controller_mapping"]

    def test_axis_stick_writes_index_and_invert(self):
        # push the left stick LEFT (opposite the "push right" prompt) on axis_left_x
        res = self._set(id="axis_left_x", kind="axis", value="-left_x")
        self.assertEqual(res["value"], "L-stick X")
        self.assertEqual(self._cm(), {"axis_left_x": 0, "invert_axis_left_x": True})

    def test_axis_remap_to_different_physical_axis(self):
        # bind Xbox left-stick-X to the physical RIGHT stick X (push right → no invert)
        self._set(id="axis_left_x", kind="axis", value="+right_x")
        cm = self._cm()
        self.assertEqual(cm["axis_left_x"], 2)
        self.assertFalse(cm["invert_axis_left_x"])

    def test_trigger_has_no_invert_key(self):
        self._set(id="axis_trigger_left", kind="axis", value="+trigger_left")
        self.assertEqual(self._cm(), {"axis_trigger_left": 4})

    def test_axis_row_is_capturable(self):
        res = xemu_input_cmds._input_get({})
        binds = {b["id"]: b for g in res["groups"] for b in g["binds"]}
        self.assertEqual(binds["axis_left_x"]["kind"], "axis")
        self.assertTrue(binds["axis_left_x"]["capturable"])

    def test_axis_rejects_rank_token(self):
        # the OLD rank token must be rejected — EmuInputMap uses the canonical mode now
        with self.assertRaises(RpcError):
            xemu_input_cmds._input_set({"id": "axis_left_x", "kind": "axis", "value": "+0"})

    # ── buffered editor: stage in memory, commit on Save, revert on Cancel ────
    def test_stage_leaves_file_unchanged(self):
        before = self.tmp.read_text()
        res = xemu_input_cmds._input_set({"id": "a", "value": 0x131})   # B onto Xbox A
        self.assertTrue(res["dirty"])                                   # response says it is staged
        self.assertEqual(self.tmp.read_text(), before)                 # NOT written to disk yet
        self.assertEqual(self.bumps, 0)                                # no staterev bump on a stage

    def test_input_get_reports_buffered_and_dirty(self):
        self.assertTrue(xemu_input_cmds._input_get({}).get("buffered"))
        self.assertFalse(xemu_input_cmds._input_get({})["dirty"])       # clean before any stage
        xemu_input_cmds._input_set({"id": "a", "value": 0x131})
        got = xemu_input_cmds._input_get({})
        self.assertTrue(got["buffered"])
        self.assertTrue(got["dirty"])
        # the buffered getter renders the staged (unsaved) value
        binds = {b["id"]: b for g in got["groups"] for b in g["binds"]}
        self.assertEqual(binds["a"]["value"], "B")

    def test_save_commits_once(self):
        xemu_input_cmds._input_set({"id": "a", "value": 0x131})
        saved = xemu_input_cmds._input_save({})
        self.assertTrue(saved["saved"])
        self.assertFalse(saved["dirty"])
        self.assertEqual(self.bumps, 1)                                # exactly one staterev bump
        self.assertEqual(self._cm(), {"a": 1})                        # committed to disk
        # a second save with nothing staged is a no-op (no extra write / bump)
        again = xemu_input_cmds._input_save({})
        self.assertFalse(again["saved"])
        self.assertEqual(self.bumps, 1)

    def test_cancel_reverts(self):
        before = self.tmp.read_text()
        xemu_input_cmds._input_set({"id": "a", "value": 0x131})
        cancelled = xemu_input_cmds._input_cancel({})
        self.assertTrue(cancelled["cancelled"])
        self.assertFalse(cancelled["dirty"])
        self.assertEqual(self.tmp.read_text(), before)                # discard leaves disk untouched
        self.assertEqual(self.bumps, 0)
        # after cancel the getter shows the original (default) value again
        binds = {b["id"]: b for g in xemu_input_cmds._input_get({})["groups"] for b in g["binds"]}
        self.assertEqual(binds["a"]["value"], "A")

    def test_multi_stage_saves_all_at_once(self):
        # two device-scoped edits accumulate in the buffer; Save replays both onto fresh disk
        xemu_input_cmds._input_set({"id": "a", "value": 0x131})        # B → Xbox A
        xemu_input_cmds._input_set({"id": "b", "value": 0x130})        # A → Xbox B
        self.assertEqual(self.tmp.read_text(), FIX.read_text())       # nothing on disk yet
        xemu_input_cmds._input_save({})
        self.assertEqual(self._cm(), {"a": 1, "b": 0})
        self.assertEqual(self.bumps, 1)

    def test_running_guard_blocks_stage_and_leaves_buffer_clean(self):
        xemu_input_cmds.proc_guard.emulator_running = lambda name: True
        with self.assertRaises(RpcError) as cm:
            xemu_input_cmds._input_set({"id": "a", "value": 0x131})
        self.assertEqual(cm.exception.code, "EBUSY")
        self.assertFalse(xemu_input_cmds._buf.dirty)                   # a rejected stage buffers nothing

    def test_version_gate_blocks_save(self):
        # stage while supported, then xemu is downgraded before Save → the gate fires on replay
        xemu_input_cmds._input_set({"id": "a", "value": 0x131})
        xemu_input_cmds._supports_remap = lambda: False
        with self.assertRaises(RpcError) as cm:
            xemu_input_cmds._input_save({})
        self.assertEqual(cm.exception.code, "EINVAL")
        self.assertEqual(self.tmp.read_text(), FIX.read_text())       # nothing written


if __name__ == "__main__":
    unittest.main()
