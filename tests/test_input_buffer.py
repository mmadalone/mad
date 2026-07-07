"""Mechanics tests for the shared buffered-editor state machine (lib/madsrv/input_buffer).

Pure: fake load/apply_edit/flush callbacks over both a dict working-copy and a str
working-copy, so the same InputBuffer is exercised for JSON-dict backends (ryujinx) and
INI/TOML-text backends (eden/pcsx2/xemu). Run with the rest:
    python3 -m unittest discover -s tests -t .
"""
import copy
import unittest

from lib import staterev
from lib.madsrv.input_buffer import InputBuffer

GLOBAL = ()          # a global config file: ctx is the empty tuple
GAME_A = ("gameA",)  # a per-game config: ctx is the titleid tuple
GAME_B = ("gameB",)


class DictBackend:
    """Fake dict-working backend. `store` is 'disk'; apply_edit mutates the working copy
    IN PLACE and returns it, to prove the buffer's disk snapshot is an independent copy."""

    def __init__(self, initial):
        self.store = copy.deepcopy(initial)
        self.loads = 0
        self.flushes = []

    def load(self, ctx):
        self.loads += 1
        return copy.deepcopy(self.store)

    def apply_edit(self, working, edit):
        working[edit["key"]] = edit["value"]           # in-place
        return working, (edit["key"], edit["value"])

    def flush(self, ctx, disk, edits):
        # replay strategy: apply staged edits onto the CURRENT disk, return fresh
        self.flushes.append((ctx, copy.deepcopy(disk), list(edits)))
        for k, v in edits:
            self.store[k] = v
        return copy.deepcopy(self.store)

    def buf(self):
        return InputBuffer(load=self.load, apply_edit=self.apply_edit, flush=self.flush)


class TestSetStaging(unittest.TestCase):
    def test_set_stages_in_memory_disk_untouched(self):
        b = DictBackend({"button_a": "1"})
        buf = b.buf()
        buf.set(GLOBAL, {"key": "button_a", "value": "9"})
        self.assertEqual(buf.working, {"button_a": "9"})   # working shows the edit
        self.assertEqual(b.store, {"button_a": "1"})        # DISK untouched
        self.assertEqual(buf.disk, {"button_a": "1"})       # snapshot untouched
        self.assertTrue(buf.dirty)
        self.assertEqual(b.flushes, [])                     # no write happened

    def test_dirty_snapshot_is_deep_independent(self):
        b = DictBackend({"grp": {"x": 1}})
        buf = b.buf()
        buf.working  # noqa: force nothing; load happens on first verb
        buf.set(GLOBAL, {"key": "grp", "value": {"x": 2}})
        # nested value changed in working; the deepcopy disk snapshot must be unaffected
        self.assertEqual(buf.disk, {"grp": {"x": 1}})
        self.assertEqual(b.store, {"grp": {"x": 1}})
        self.assertTrue(buf.dirty)

    def test_edit_reverting_to_disk_value_is_not_dirty(self):
        b = DictBackend({"button_a": "1"})
        buf = b.buf()
        buf.set(GLOBAL, {"key": "button_a", "value": "9"})
        self.assertTrue(buf.dirty)
        buf.set(GLOBAL, {"key": "button_a", "value": "1"})  # back to the on-disk value
        self.assertFalse(buf.dirty)                          # dirty = working != disk, not edit count
        self.assertEqual(len(buf.edits), 2)                  # both edits still queued for replay


class TestGetReloadRules(unittest.TestCase):
    def test_get_keeps_staged_when_same_ctx_and_dirty(self):
        b = DictBackend({"button_a": "1"})
        buf = b.buf()
        buf.set(GLOBAL, {"key": "button_a", "value": "9"})
        loads_before = b.loads
        got = buf.get(GLOBAL)                                # same ctx + dirty -> keep
        self.assertEqual(got, {"button_a": "9"})
        self.assertEqual(b.loads, loads_before)             # did NOT reload

    def test_player_switch_preserves_staged(self):
        # A "player switch" is a get() with the SAME file-identity ctx. Staged edits
        # (dirty) must survive it, because player is not part of ctx.
        b = DictBackend({"p0_a": "1", "p1_a": "1"})
        buf = b.buf()
        buf.set(GLOBAL, {"key": "p0_a", "value": "9"})      # edit player 0
        got = buf.get(GLOBAL)                                # C++ re-get on player-stepper move
        self.assertEqual(got["p0_a"], "9")                  # staged edit preserved
        self.assertTrue(buf.dirty)

    def test_get_reloads_when_clean(self):
        b = DictBackend({"button_a": "1"})
        buf = b.buf()
        buf.get(GLOBAL)                                      # first load
        loads_after_first = b.loads
        buf.get(GLOBAL)                                      # clean -> reload from disk
        self.assertEqual(b.loads, loads_after_first + 1)

    def test_get_on_new_ctx_drops_other_ctx_staged(self):
        b = DictBackend({"button_a": "1"})
        buf = b.buf()
        buf.set(GAME_A, {"key": "button_a", "value": "9"})  # dirty on game A
        buf.get(GAME_B)                                      # switch file -> reload B
        self.assertFalse(buf.dirty)
        got_a = buf.get(GAME_A)                              # back to A: reloaded fresh, staged gone
        self.assertEqual(got_a, {"button_a": "1"})
        self.assertFalse(buf.dirty)


class TestSaveCancel(unittest.TestCase):
    def test_save_replays_all_edits_and_bumps_once(self):
        b = DictBackend({"a": "1", "b": "1"})
        buf = b.buf()
        buf.set(GLOBAL, {"key": "a", "value": "9"})
        buf.set(GLOBAL, {"key": "b", "value": "8"})
        rev_before = staterev.snapshot(("config",))["config"]
        ok = buf.save(GLOBAL)
        self.assertTrue(ok)
        self.assertEqual(len(b.flushes), 1)                 # one flush call...
        self.assertEqual(b.flushes[0][2], [("a", "9"), ("b", "8")])  # ...replaying both edits
        self.assertEqual(b.store, {"a": "9", "b": "8"})     # disk now has both
        self.assertEqual(buf.disk, {"a": "9", "b": "8"})    # snapshot re-synced
        self.assertEqual(buf.edits, [])
        self.assertFalse(buf.dirty)
        rev_after = staterev.snapshot(("config",))["config"]
        self.assertGreater(rev_after, rev_before)           # staterev bumped

    def test_save_no_edits_is_noop(self):
        b = DictBackend({"a": "1"})
        buf = b.buf()
        buf.get(GLOBAL)
        self.assertFalse(buf.save(GLOBAL))                  # nothing staged
        self.assertEqual(b.flushes, [])

    def test_save_ctx_mismatch_does_not_flush(self):
        b = DictBackend({"a": "1"})
        buf = b.buf()
        buf.set(GAME_A, {"key": "a", "value": "9"})
        self.assertFalse(buf.save(GAME_B))                  # staged edits belong to A, save asks B
        self.assertEqual(b.flushes, [])
        # A's staged edit must SURVIVE the mismatched save (not be silently stranded by a
        # dirty=False on the held buffer) — reopening A still shows its staged value.
        self.assertTrue(buf.dirty)
        self.assertEqual(buf.get(GAME_A), {"a": "9"})

    def test_cancel_reloads_and_clears(self):
        b = DictBackend({"a": "1"})
        buf = b.buf()
        buf.set(GLOBAL, {"key": "a", "value": "9"})
        self.assertTrue(buf.dirty)
        buf.cancel(GLOBAL)
        self.assertFalse(buf.dirty)
        self.assertEqual(buf.edits, [])
        self.assertEqual(buf.working, {"a": "1"})           # reverted to disk
        self.assertEqual(b.flushes, [])                     # cancel never writes


class TestStrWorkingCopy(unittest.TestCase):
    """The same buffer over an INI/TOML text string (eden/pcsx2/xemu representation)."""

    def _backend(self):
        state = {"text": "button_a=1\n", "flushes": 0}

        def load(ctx):
            return state["text"]

        def apply_edit(working, edit):
            new = working.replace(f'{edit["key"]}={edit["old"]}',
                                  f'{edit["key"]}={edit["value"]}')
            return new, (edit["key"], edit["value"])

        def flush(ctx, disk, edits):
            text = state["text"]
            for k, v in edits:
                # naive replay: set k to v regardless of prior
                import re
                text = re.sub(rf'{k}=\S+', f'{k}={v}', text)
            state["text"] = text
            return text

        return state, InputBuffer(load=load, apply_edit=apply_edit, flush=flush)

    def test_str_stage_and_save(self):
        state, buf = self._backend()
        buf.set(GLOBAL, {"key": "button_a", "old": "1", "value": "9"})
        self.assertEqual(buf.working, "button_a=9\n")
        self.assertEqual(state["text"], "button_a=1\n")     # disk text untouched
        self.assertTrue(buf.dirty)
        self.assertTrue(buf.save(GLOBAL))
        self.assertEqual(state["text"], "button_a=9\n")     # committed
        self.assertFalse(buf.dirty)


if __name__ == "__main__":
    unittest.main()
