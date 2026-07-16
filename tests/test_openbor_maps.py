"""openbor_maps: token vocabulary, keycode math, default map, JSON store."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lib import openbor_maps as M


class TokenMath(unittest.TestCase):
    def test_button_offsets_xinput_order(self):
        # Wine XInput order, proven via the banked keycode 610 (ThumbR = 9).
        self.assertEqual(M.token_offset("btn:a"), 0)
        self.assertEqual(M.token_offset("btn:thumbr"), 9)
        self.assertEqual(M.token_offset("btn:guide"), 10)

    def test_axis_and_hat_offsets(self):
        self.assertEqual(M.token_offset("ax:lt"), 16)
        self.assertEqual(M.token_offset("ax:rt"), 22)
        self.assertEqual(M.token_offset("hat:up"), 23)
        self.assertEqual(M.token_offset("hat:left"), 26)

    def test_keycode_stride_64(self):
        self.assertEqual(M.keycode("hat:up", 0), 624)          # 601 + 23
        self.assertEqual(M.keycode("hat:up", 1), 688)          # 601 + 64 + 23
        self.assertEqual(M.keycode("btn:a", 3, stride=64), 601 + 192)

    def test_keycode_stride_32(self):
        # The pre-2018 generation: port bases 601/633/665/697.
        self.assertEqual(M.keycode("btn:a", 1, stride=32), 633)
        self.assertEqual(M.keycode("hat:up", 3, stride=32), 601 + 96 + 23)

    def test_keycode_port0_stride_independent(self):
        for tok in ("btn:x", "ax:rt", "hat:down"):
            self.assertEqual(M.keycode(tok, 0, 32), M.keycode(tok, 0, 64))

    def test_keyboard_none_unknown(self):
        self.assertEqual(M.keycode("kb:27", 2), 27)            # port-independent
        self.assertEqual(M.keycode("none", 0), M.UNMAPPED)
        self.assertEqual(M.keycode("", 0), M.UNMAPPED)
        self.assertEqual(M.keycode("btn:nope", 0), M.UNMAPPED)
        self.assertEqual(M.keycode("kb:zzz", 0), M.UNMAPPED)

    def test_all_canonical_offsets_fit_stride_32(self):
        # Every token must be expressible in the old 32-input generation.
        for table in (M._BTN_OFFSET, M._AX_OFFSET, M._HAT_OFFSET):
            for off in table.values():
                self.assertLess(off, 32)

    def test_labels(self):
        self.assertEqual(M.token_label("btn:a"), "A")
        self.assertEqual(M.token_label("hat:up"), "D-pad up")
        self.assertEqual(M.token_label("none"), "—")


class DefaultMap(unittest.TestCase):
    def test_covers_all_slots_with_valid_tokens(self):
        self.assertEqual(set(M.DEFAULT_MAP), set(M.SLOTS))
        for slot, tok in M.DEFAULT_MAP.items():
            self.assertNotEqual(
                (M.keycode(tok, 0), tok != "none"), (M.UNMAPPED, True),
                f"{slot}={tok} does not resolve")

    def test_map_to_keys_shape_and_bases(self):
        keys = M.map_to_keys({}, stride=64)
        self.assertEqual(len(keys), 4)
        self.assertEqual([len(r) for r in keys], [13] * 4)
        self.assertEqual(keys[0][0], 624)                       # up = hat:up
        self.assertEqual(keys[1][0], 688)
        self.assertEqual(keys[0][11], M.UNMAPPED)               # sshot = none
        self.assertEqual(keys[0][12], 0)                        # esc = kb:0


class Store(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Path(self.tmp.name) / "input-maps.json"
        self.patch = mock.patch.object(M, "_STORE", self.store)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_effective_map_defaults_when_empty(self):
        self.assertEqual(M.effective_map("AnyGame"), M.DEFAULT_MAP)

    def test_override_roundtrip_and_inherit(self):
        M.set_game_override("MIW", {"atk1": "btn:b", "junk": "x", "up": "hat:up"})
        eff = M.effective_map("MIW")
        self.assertEqual(eff["atk1"], "btn:b")
        self.assertEqual(eff["jump"], M.DEFAULT_MAP["jump"])    # inherited
        self.assertNotIn("junk", M.game_override("MIW"))        # unknown slot dropped
        M.set_game_override("MIW", None)                        # clear -> inherit
        self.assertEqual(M.effective_map("MIW"), M.DEFAULT_MAP)
        self.assertEqual(M.game_override("MIW"), {})

    def test_corrupt_store_backed_up_not_clobbered(self):
        self.store.write_text("{ not json !!!")
        self.assertEqual(M.effective_map("X"), M.DEFAULT_MAP)   # survives
        M.set_game_override("X", {"atk1": "btn:b"})             # fresh store
        bad = self.store.with_name(self.store.name + ".bad")
        self.assertTrue(bad.exists())
        self.assertEqual(bad.read_text(), "{ not json !!!")


if __name__ == "__main__":
    unittest.main()
