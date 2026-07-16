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

    def test_all_offsets_fit_stride_32_under_both_geometries(self):
        # Every expressible token must fit the old 32-input-per-port generation
        # under either engine view, or it would spill into the next port.
        for geom in (M.GEOM_XINPUT, (10, 5)):
            for name, off in M.offsets_for(*geom).items():
                self.assertLess(off, 32, f"{name} under {geom}")

    def test_offsets_track_the_engine_view(self):
        # The SAME pad seen two ways: SDL2 engines report 11 btn/6 axes, the
        # pre-SDL2 ones route it through Wine's joystick driver -> 10/5. Every
        # offset shifts; hardcoding one base mis-binds the other generation.
        new = M.offsets_for(*M.GEOM_XINPUT)
        old = M.offsets_for(10, 5)
        self.assertEqual(new["hat:up"], 23)
        self.assertEqual(old["hat:up"], 20)     # == the on-device truth
        self.assertEqual(new["rt"], 22)
        self.assertNotIn("rt", old)             # a 5-axis view has no axis 5
        self.assertEqual(old["lt"], 15)         # matches Miquel's atk2=615
        for b in ("a", "x", "y", "rb", "start"):
            self.assertEqual(new[b], old[b])    # buttons 0..9 agree

    def test_geometry_inexpressible_token_is_unmapped_not_guessed(self):
        self.assertEqual(M.keycode("ax:rt", 0, 32, (10, 5)), M.UNMAPPED)
        self.assertEqual(M.keycode("btn:guide", 0, 32, (10, 5)), M.UNMAPPED)
        self.assertEqual(M.keycode("hat:up", 0, 32, (10, 5)), 601 + 20)

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
