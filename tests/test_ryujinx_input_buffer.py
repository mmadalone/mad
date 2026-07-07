"""Buffered X=Save / Y=Cancel editor for the GLOBAL Ryujinx input page
(ryujinx.input_*). Proves the deferred-write contract against a REAL temp
Config.json (JSON dict store): a stage leaves the file untouched, input_get
advertises buffered/dirty and renders the staged value, input_save commits the
config exactly once, input_cancel reverts, and the running-guard fires at both
stage and save. Also covers the landmines: the selector_set profile-bake stages
like any edit, and the whole-dict buffer spans every player (Player1..Player8/
Handheld) so several players commit in one write.

Run:  python3 -m unittest tests.test_ryujinx_input_buffer -v
"""
from __future__ import annotations

import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from lib import proc_guard
from lib.madsrv import ryujinx_input_cmds as rc
from lib.madsrv import ryujinx_json
from lib.madsrv.rpc import RpcError

# evdev codes -> the GamepadButtonInputId tokens input_translate.ryujinx_button emits.
A_CODE, B_CODE, Y_CODE = 0x130, 0x131, 0x133   # -> "A", "B", "Y"
BAD_CODE = 0x2c0                                # outside the mappable digital-button set


def _config() -> dict:
    return {
        "version": 1,
        "some_global_only": "keepme",           # a foreign key the editor must never touch
        "input_config": [{
            "player_index": "Player1",
            "controller_type": "ProController",
            "id": "0-00000003-054c-0000-cc09-000000006800",
            "backend": "GamepadSDL2",
            "left_joycon": {
                "button_l": "LeftShoulder", "button_zl": "LeftTrigger", "button_minus": "Minus",
                "dpad_up": "DpadUp", "dpad_down": "DpadDown",
                "dpad_left": "DpadLeft", "dpad_right": "DpadRight",
            },
            "right_joycon": {
                "button_a": "A", "button_b": "B", "button_x": "X", "button_y": "Y",
                "button_r": "RightShoulder", "button_zr": "RightTrigger", "button_plus": "Plus",
            },
            "left_joycon_stick": {"joystick": "Left", "invert_stick_x": False, "invert_stick_y": False},
            "right_joycon_stick": {"joystick": "Right", "invert_stick_x": False, "invert_stick_y": False},
        }],
    }


class _Base(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.cfg = self.d / "Config.json"
        self.cfg.write_text(json.dumps(_config(), indent=2) + "\n")
        self._cfg = ryujinx_json.CONFIG
        ryujinx_json.CONFIG = self.cfg
        # count real writes (still perform them) to prove "commit exactly once"
        self._real_write = ryujinx_json.write
        self.writes = 0

        def _counting_write(data, path=None):
            self.writes += 1
            self._real_write(data, path)

        ryujinx_json.write = _counting_write
        self._run = proc_guard.emulator_running
        proc_guard.emulator_running = lambda name: False
        rc._buf.reset()                          # module-level singleton — fresh per case
        import lib.staterev as sr                # save() + fsutil both bump; silence them
        self._bump = sr.bump
        sr.bump = lambda n: None

    def tearDown(self):
        ryujinx_json.CONFIG = self._cfg
        ryujinx_json.write = self._real_write
        proc_guard.emulator_running = self._run
        import lib.staterev as sr
        sr.bump = self._bump
        shutil.rmtree(self.d, ignore_errors=True)

    # -- helpers ---------------------------------------------------------------
    def _disk(self) -> dict:
        return json.loads(self.cfg.read_text())

    def _entry(self, data: dict, pidx: str = "Player1") -> dict | None:
        return next((e for e in data["input_config"] if e.get("player_index") == pidx), None)

    def _row(self, payload, key):
        for g in payload["groups"]:
            for b in g["binds"]:
                if b["id"] == key:
                    return b["value"]
        return None


class Staging(_Base):
    # (a) a stage leaves the real file UNCHANGED (and writes nothing)
    def test_stage_leaves_file_unchanged(self):
        before = self.cfg.read_text()
        r = rc._input_set({"id": "button_a", "kind": "btn", "value": B_CODE, "player": "Player1"})
        self.assertTrue(r["dirty"])                        # response says it is staged
        self.assertEqual(r["value"], "B")                  # staged token echoed back
        self.assertEqual(self.cfg.read_text(), before)     # DISK byte-identical
        self.assertEqual(self.writes, 0)                   # nothing written
        self.assertEqual(self._entry(self._disk())["right_joycon"]["button_a"], "A")  # still old

    # (b) input_get reports buffered + dirty after a stage, and renders the staged value
    def test_input_get_reports_buffered_and_dirty(self):
        clean = rc._input_get({"player": "Player1"})
        self.assertTrue(clean["buffered"])
        self.assertFalse(clean["dirty"])
        self.assertEqual(self._row(clean, "button_a"), "A")
        rc._input_set({"id": "button_a", "kind": "btn", "value": B_CODE, "player": "Player1"})
        dirty = rc._input_get({"player": "Player1"})
        self.assertTrue(dirty["buffered"])
        self.assertTrue(dirty["dirty"])
        self.assertEqual(self._row(dirty, "button_a"), "B")   # page reflects the unsaved edit
        self.assertEqual(self.writes, 0)                       # get never writes

    # (c) input_save commits (the store changes, exactly once)
    def test_save_commits_once(self):
        rc._input_set({"id": "button_a", "kind": "btn", "value": B_CODE, "player": "Player1"})
        res = rc._input_save({})
        self.assertTrue(res["saved"])
        self.assertFalse(res["dirty"])
        self.assertEqual(self.writes, 1)                       # one write only
        self.assertEqual(self._entry(self._disk())["right_joycon"]["button_a"], "B")  # committed
        self.assertFalse(rc._input_get({"player": "Player1"})["dirty"])  # clean after save

    # (d) input_cancel reverts (disk untouched, buffer clean)
    def test_cancel_reverts(self):
        before = self.cfg.read_text()
        rc._input_set({"id": "button_a", "kind": "btn", "value": B_CODE, "player": "Player1"})
        res = rc._input_cancel({})
        self.assertTrue(res["cancelled"])
        self.assertFalse(res["dirty"])
        self.assertEqual(self.cfg.read_text(), before)         # never written
        self.assertEqual(self.writes, 0)
        self.assertEqual(self._row(rc._input_get({"player": "Player1"}), "button_a"), "A")

    # (e) value/format assertions: token is a valid GamepadButtonInputId string;
    #     the slot's identity + foreign keys survive the write.
    def test_committed_value_format_and_preservation(self):
        rc._input_set({"id": "dpad_left", "kind": "hat", "value": "h0up", "player": "Player1"})
        rc._input_save({})
        e = self._entry(self._disk())
        self.assertEqual(e["left_joycon"]["dpad_left"], "DpadUp")          # hat -> enum name
        self.assertEqual(e["id"], "0-00000003-054c-0000-cc09-000000006800")  # device identity kept
        self.assertEqual(e["controller_type"], "ProController")
        self.assertEqual(e["player_index"], "Player1")
        self.assertEqual(self._disk()["some_global_only"], "keepme")       # foreign key untouched


class ClearAndSelectors(_Base):
    def test_clear_stage_then_save(self):
        before = self.cfg.read_text()
        r = rc._input_clear({"id": "button_a", "player": "Player1"})
        self.assertTrue(r["dirty"])
        self.assertEqual(self.cfg.read_text(), before)                     # staged only
        rc._input_save({})
        self.assertEqual(self._entry(self._disk())["right_joycon"]["button_a"], "Unbound")

    def test_selector_stage_then_save(self):
        r = rc._selector_set({"key": "controller_type", "value": "Handheld", "player": "Player1"})
        self.assertEqual(r["value"], "Handheld")
        self.assertTrue(r["dirty"])
        self.assertEqual(self.writes, 0)
        self.assertEqual(self._entry(self._disk())["controller_type"], "ProController")  # unsaved
        rc._input_save({})
        self.assertEqual(self._entry(self._disk())["controller_type"], "Handheld")

    # LANDMINE: selector_set profile-bake copies a subtree — it stages like any edit
    # (its apply mutates the dict) and only lands on Save.
    def test_profile_bake_stages_like_any_edit(self):
        prof_dir = self.cfg.parent / "profiles" / "controller"
        prof_dir.mkdir(parents=True)
        (prof_dir / "MyPad.json").write_text(json.dumps({
            "left_joycon": {"button_l": "ZZMARKER"},
            "right_joycon": {"button_a": "QQMARKER"},
            "deadzone_left": 0.42,
        }))
        before = self.cfg.read_text()
        r = rc._selector_set({"key": "profile", "value": "MyPad", "player": "Player1"})
        self.assertEqual(r["value"], "Default")           # picker does not track the baked name
        self.assertTrue(r["dirty"])
        self.assertEqual(self.cfg.read_text(), before)    # bake is staged, not written
        self.assertEqual(self.writes, 0)
        rc._input_save({})
        e = self._entry(self._disk())
        self.assertEqual(e["left_joycon"]["button_l"], "ZZMARKER")   # subtree baked in
        self.assertEqual(e["right_joycon"]["button_a"], "QQMARKER")
        self.assertEqual(e["deadzone_left"], 0.42)
        self.assertEqual(e["id"], "0-00000003-054c-0000-cc09-000000006800")  # identity preserved
        self.assertEqual(e["controller_type"], "ProController")

    def test_unknown_profile_rejected_at_stage(self):
        with self.assertRaises(RpcError):
            rc._selector_set({"key": "profile", "value": "Nope", "player": "Player1"})
        self.assertEqual(self.writes, 0)


class SpansAllPlayers(_Base):
    # LANDMINE: the whole-dict buffer spans EVERY player; player is only a render
    # filter. Several players' edits accumulate and commit in ONE write; a new
    # player's slot is (re)created from Player 1 on the fresh-disk replay.
    def test_multiple_players_one_write(self):
        rc._input_set({"id": "button_a", "kind": "btn", "value": B_CODE, "player": "Player1"})
        rc._input_set({"id": "button_x", "kind": "btn", "value": Y_CODE, "player": "Player2"})
        self.assertTrue(rc._buf.dirty)
        self.assertEqual(self.writes, 0)
        rc._input_save({})
        self.assertEqual(self.writes, 1)                   # BOTH players in one commit
        data = self._disk()
        self.assertEqual(self._entry(data, "Player1")["right_joycon"]["button_a"], "B")
        p2 = self._entry(data, "Player2")
        self.assertIsNotNone(p2)                           # slot re-created on the replay
        self.assertEqual(p2["right_joycon"]["button_x"], "Y")
        self.assertEqual(p2["id"], rc._UNBOUND_ID)         # device left UNBOUND (wrapper assigns)


class RunningGuard(_Base):
    def test_running_blocks_stage(self):
        proc_guard.emulator_running = lambda name: True
        with self.assertRaises(RpcError) as cm:
            rc._input_set({"id": "button_a", "kind": "btn", "value": B_CODE, "player": "Player1"})
        self.assertEqual(cm.exception.code, "EBUSY")
        self.assertEqual(self.writes, 0)

    # The EBUSY guard lives at the top of _apply, so it also fires on the save replay:
    # stage while stopped, then Ryujinx starts -> Save must refuse (and not write).
    def test_running_blocks_save_replay(self):
        rc._input_set({"id": "button_a", "kind": "btn", "value": B_CODE, "player": "Player1"})
        proc_guard.emulator_running = lambda name: True
        with self.assertRaises(RpcError) as cm:
            rc._input_save({})
        self.assertEqual(cm.exception.code, "EBUSY")
        self.assertEqual(self.writes, 0)                   # nothing committed


class BadInput(_Base):
    def test_unmappable_button_rejected_at_stage(self):
        with self.assertRaises(RpcError):
            rc._input_set({"id": "button_a", "kind": "btn", "value": BAD_CODE, "player": "Player1"})
        self.assertEqual(self.writes, 0)
        self.assertFalse(rc._buf.dirty)                    # nothing staged

    def test_unknown_player_rejected(self):
        with self.assertRaises(RpcError):
            rc._input_set({"id": "button_a", "kind": "btn", "value": B_CODE, "player": "PlayerX"})


if __name__ == "__main__":
    unittest.main()
