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

    def test_kb_out_of_range_refused_not_spliced(self):
        # An unchecked kb value would land in the joystick space (binding a
        # phantom pad control) or poison the cfg so every later launch refuses
        # it. Both must degrade to "unmapped" instead.
        self.assertEqual(M.keycode("kb:700", 0), M.UNMAPPED)        # joy space
        self.assertEqual(M.keycode("kb:512", 0), M.UNMAPPED)        # == limit
        self.assertEqual(M.keycode("kb:1073741881", 0), M.UNMAPPED)  # SDLK_*
        self.assertEqual(M.keycode("kb:-5", 0), M.UNMAPPED)
        self.assertEqual(M.keycode("kb:511", 0), 511)               # valid edge

    def test_non_str_token_refused(self):
        self.assertEqual(M.keycode(None, 0), M.UNMAPPED)
        self.assertEqual(M.keycode(42, 0), M.UNMAPPED)

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

    def test_default_map_matches_the_proven_on_device_map(self):
        # Transcription guard: this is the map verified working on-device
        # 2026-07-16 (MIW, Deck pad). A typo here mis-binds every game.
        self.assertEqual(M.DEFAULT_MAP, {
            "up": "hat:up", "down": "hat:down",
            "left": "hat:left", "right": "hat:right",
            "atk1": "btn:x", "atk2": "btn:rb", "atk3": "btn:lb",
            "atk4": "btn:y", "jump": "btn:a", "special": "ax:rt",
            "start": "btn:start", "sshot": "none", "esc": "kb:0",
        })

    def test_ps_and_xpad_face_buttons_are_swapped(self):
        # The kernel drivers disagree: xpad maps X->0x133/Y->0x134; the
        # positional hid-playstation gives Triangle(north)=0x133,
        # Square(west)=0x134. A single table would mis-bind one family.
        self.assertEqual(M.EVDEV_BTN["xpad"][0x133], "btn:x")
        self.assertEqual(M.EVDEV_BTN["xpad"][0x134], "btn:y")
        self.assertEqual(M.EVDEV_BTN["ps"][0x133], "btn:y")
        self.assertEqual(M.EVDEV_BTN["ps"][0x134], "btn:x")


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
