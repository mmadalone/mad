"""openbor_cfg: era resolution, layout validation, and the in-place splice.

Synthetic fixtures fabricate each engine generation's s_savedata shape
(size, keys offset, slot count, stride, sentinel) with patterned tail bytes,
so every test can assert the strongest invariant: NOTHING outside the keys
block changes, ever."""
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lib import openbor_cfg as C
from lib import openbor_maps as M

# (size, offset, slots, stride, sentinel, compile-date banner)
GEN_2016 = (324, 0x34, 12, 32, -999, b"Compile Date: Dec 22 2016")
GEN_7530 = (324, 0x28, 13, 64, 6937, b"Compile Date: Jan  1 2024")
GEN_LATE3 = (352, 0x34, 13, 64, -999, b"Compile Date: May 12 2023")
GEN_BDD = (340, 0x34, 13, 32, -999, b"Compile Date: Oct  3 2017")


def make_cfg(size, offset, slots, stride, sentinel, *, dword0=0x33748,
             bound_rows=1) -> bytes:
    """A synthetic cfg: patterned filler everywhere, a plausible keys block
    (P1..bound_rows bound to per-port defaults, the rest all-sentinel)."""
    data = bytearray((0x55 + i) % 251 for i in range(size))
    struct.pack_into("<I", data, 0, dword0)
    for p in range(4):
        if p < bound_rows:
            row = [601 + p * stride + o for o in
                   (23, 25, 26, 24, 2, 5, 4, 3, 0, 22, 7)]      # 11 gameplay
            row += [sentinel] * (slots - len(row) - 1) + [0]    # sshot.., esc=0
            row = row[:slots]
        else:
            row = [sentinel] * slots
            if slots == 13:
                row[12] = 0
        struct.pack_into(f"<{slots}i", data, offset + p * slots * 4, *row)
    return bytes(data)


def make_game(tmp: Path, gen, name="Game", cfgname="game.cfg", **kw) -> Path:
    size, offset, slots, stride, sentinel, banner = gen
    d = tmp / name
    (d / "Saves").mkdir(parents=True)
    (d / "Logs").mkdir()
    (d / "Saves" / cfgname).write_bytes(
        make_cfg(size, offset, slots, stride, sentinel, **kw))
    (d / "Logs" / "OpenBorLog.txt").write_bytes(
        b"OpenBoR v_x Build , " + banner + b"\n")
    return d


class EraResolution(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_era_parse(self):
        g = make_game(self.root, GEN_2016)
        self.assertEqual(C.engine_era(g), (2016, 12))

    def test_era_missing_log(self):
        g = make_game(self.root, GEN_2016)
        (g / "Logs" / "OpenBorLog.txt").unlink()
        self.assertIsNone(C.engine_era(g))

    def test_era_unknown_month_refuses_not_guesses(self):
        g = make_game(self.root, GEN_2016, name="Weird")
        (g / "Logs" / "OpenBorLog.txt").write_bytes(
            b"OpenBoR v_x Build , Compile Date: Mai 12 2023\n")   # localized
        self.assertIsNone(C.engine_era(g))                       # not (2023,12)
        self.assertEqual(C.apply_map(g), "skip-no-fingerprint")

    def _layout(self, gen, era):
        size, offset, slots, stride, sentinel, _ = gen
        data = make_cfg(size, offset, slots, stride, sentinel)
        return C.resolve_layout(data, era)

    def test_generation_matrix(self):
        for gen, era in ((GEN_2016, (2016, 12)), (GEN_7530, (2024, 1)),
                         (GEN_LATE3, (2023, 5)), (GEN_BDD, (2017, 10))):
            lay = self._layout(gen, era)
            size, offset, slots, stride, sentinel, _ = gen
            self.assertIsNotNone(lay, f"gen {gen} unresolved")
            self.assertEqual((lay.offset, lay.slots, lay.stride, lay.sentinel),
                             (offset, slots, stride, sentinel))

    def test_dd_remix_case_13_slots_stride_32(self):
        # Mar 2018: after the 12->13 flip, before the 32->64 flip.
        data = make_cfg(332, 0x34, 13, 32, -999)
        lay = C.resolve_layout(data, (2018, 3))
        self.assertEqual((lay.slots, lay.stride), (13, 32))

    def test_unknown_size_refused(self):
        self.assertIsNone(C.resolve_layout(b"\0" * 300, (2023, 5)))

    def test_movement_duplicate_refused(self):
        size, offset, slots, stride, sentinel, _ = GEN_LATE3
        data = bytearray(make_cfg(size, offset, slots, stride, sentinel))
        struct.pack_into("<2i", data, offset, 624, 624)         # up == down
        self.assertIsNone(C.resolve_layout(bytes(data), (2023, 5)))

    def test_out_of_range_value_refused(self):
        size, offset, slots, stride, sentinel, _ = GEN_LATE3
        data = bytearray(make_cfg(size, offset, slots, stride, sentinel))
        struct.pack_into("<i", data, offset + 16, 44100)        # not a binding
        self.assertIsNone(C.resolve_layout(bytes(data), (2023, 5)))

    def test_sentinel_detected_6937(self):
        size, offset, slots, stride, sentinel, _ = GEN_7530
        data = make_cfg(size, offset, slots, stride, sentinel)
        self.assertEqual(C.resolve_layout(data, (2024, 1)).sentinel, 6937)


class Locate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_newest_non_default_wins(self):
        g = make_game(self.root, GEN_LATE3, cfgname="real.cfg")
        (g / "Saves" / "Default.cfg").write_bytes(b"x" * 352)   # case-insensitive skip
        old = g / "Saves" / "older.cfg"
        old.write_bytes(make_cfg(*GEN_LATE3[:5]))
        import os
        os.utime(old, (1, 1))
        self.assertEqual(C.locate_cfg(g).name, "real.cfg")

    def test_pak_name_beats_newest_mtime(self):
        # The engine loads Saves/<pakstem>.cfg. A newer stale sibling must not
        # win, or we would write a file the engine never reads.
        import os
        g = make_game(self.root, GEN_LATE3, name="Pak", cfgname="thegame.cfg")
        (g / "Paks").mkdir()
        (g / "Paks" / "thegame.pak").write_bytes(b"pak")
        (g / "Paks" / "menu.pak").write_bytes(b"menu")          # ignored
        leftover = g / "Saves" / "oldversion.cfg"
        leftover.write_bytes(make_cfg(*GEN_LATE3[:5]))
        os.utime(leftover, None)                                # newest
        self.assertEqual(C.locate_cfg(g).name, "thegame.cfg")

    def test_mtime_fallback_when_pak_name_absent(self):
        import os
        g = make_game(self.root, GEN_LATE3, name="NoMatch", cfgname="a.cfg")
        (g / "Paks").mkdir()
        (g / "Paks" / "different.pak").write_bytes(b"pak")
        newer = g / "Saves" / "b.cfg"
        newer.write_bytes(make_cfg(*GEN_LATE3[:5]))
        os.utime(newer, None)
        self.assertEqual(C.locate_cfg(g).name, "b.cfg")

    def test_no_cfg(self):
        g = make_game(self.root, GEN_LATE3)
        (g / "Saves" / "game.cfg").unlink()
        self.assertIsNone(C.locate_cfg(g))


class Apply(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        # isolate the store
        self.store = self.root / "input-maps.json"
        self.patch = mock.patch.object(M, "_STORE", self.store)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def _assert_outside_keys_untouched(self, before, after, lay):
        lo, hi = lay.offset, lay.offset + 4 * lay.slots * 4
        self.assertEqual(before[:lo], after[:lo], "bytes before keys changed")
        self.assertEqual(before[hi:], after[hi:], "bytes after keys changed")
        self.assertEqual(len(before), len(after))

    def _apply_and_check(self, gen, era):
        size, offset, slots, stride, sentinel, _ = gen
        g = make_game(self.root, gen, name=f"G{size}_{offset}")
        cfg = C.locate_cfg(g)
        before = cfg.read_bytes()
        self.assertEqual(C.apply_map(g), "applied")
        after = cfg.read_bytes()
        lay = C.resolve_layout(after, era)
        self._assert_outside_keys_untouched(before, after, lay)
        rows = lay.rows(after)
        for p in range(4):
            self.assertEqual(rows[p][0], 601 + p * stride + 23,  # up=hat:up
                             f"P{p+1} up wrong (stride {stride})")
            self.assertEqual(rows[p][9], 601 + p * stride + 22)  # special=ax:rt
            if slots == 13:
                self.assertEqual(rows[p][11], sentinel)          # sshot=none
                self.assertEqual(rows[p][12], 0)                 # esc=kb:0
        # idempotence
        self.assertEqual(C.apply_map(g), "unchanged")

    def test_apply_all_generations(self):
        self._apply_and_check(GEN_2016, (2016, 12))
        self._apply_and_check(GEN_7530, (2024, 1))
        self._apply_and_check(GEN_LATE3, (2023, 5))
        self._apply_and_check(GEN_BDD, (2017, 10))

    def test_per_game_override_applies(self):
        M.set_game_override("Ovr", {"atk1": "btn:b"})
        g = make_game(self.root, GEN_LATE3, name="Ovr")
        self.assertEqual(C.apply_map(g), "applied")
        lay = C.resolve_layout(C.locate_cfg(g).read_bytes(), (2023, 5))
        rows = lay.rows(C.locate_cfg(g).read_bytes())
        self.assertEqual(rows[0][4], 601 + 1)                    # atk1 = btn:b

    def test_skip_paths(self):
        g = make_game(self.root, GEN_LATE3, name="NoLog")
        (g / "Logs" / "OpenBorLog.txt").unlink()
        self.assertEqual(C.apply_map(g), "skip-no-fingerprint")

        g2 = make_game(self.root, GEN_LATE3, name="NoCfg")
        (g2 / "Saves" / "game.cfg").unlink()
        self.assertEqual(C.apply_map(g2), "skip-no-cfg")

        g3 = make_game(self.root, GEN_LATE3, name="Tiny")
        (g3 / "Saves" / "game.cfg").write_bytes(b"\0" * 248)
        self.assertEqual(C.apply_map(g3), "skip-248")

        g4 = make_game(self.root, GEN_LATE3, name="BadWin")
        data = bytearray((g4 / "Saves" / "game.cfg").read_bytes())
        struct.pack_into("<2i", data, 0x34, 624, 624)            # movement dup
        (g4 / "Saves" / "game.cfg").write_bytes(bytes(data))
        self.assertEqual(C.apply_map(g4), "skip-unknown-layout")

    def test_poison_kb_override_cannot_reach_the_file(self):
        # A hand-edited out-of-range kb value must degrade to the file's
        # sentinel, never splice a joystick code or a value that would make
        # every later launch refuse the cfg.
        M.set_game_override("Poison", {"esc": "kb:1073741881",
                                       "atk1": "kb:700"})
        g = make_game(self.root, GEN_LATE3, name="Poison")
        self.assertEqual(C.apply_map(g), "applied")
        data = C.locate_cfg(g).read_bytes()
        lay = C.resolve_layout(data, (2023, 5))
        self.assertIsNotNone(lay, "cfg must stay resolvable (not poisoned)")
        rows = lay.rows(data)
        self.assertEqual(rows[0][12], lay.sentinel)             # esc unmapped
        self.assertEqual(rows[0][4], lay.sentinel)              # atk1 unmapped
        self.assertEqual(C.apply_map(g), "unchanged")           # still healthy

    def test_non_str_override_value_does_not_abort_the_write(self):
        import json
        (self.root / "input-maps.json").write_text(
            json.dumps({"games": {"Numeric": {"atk1": 700}}}))
        g = make_game(self.root, GEN_LATE3, name="Numeric")
        self.assertEqual(C.apply_map(g), "applied")             # no crash
        data = C.locate_cfg(g).read_bytes()
        rows = C.resolve_layout(data, (2023, 5)).rows(data)
        self.assertEqual(rows[0][4], 601 + 2)                   # default btn:x

    def test_permissions_preserved(self):
        g = make_game(self.root, GEN_LATE3, name="Perms")
        cfg = C.locate_cfg(g)
        cfg.chmod(0o770)                                         # Windows-copy style
        self.assertEqual(C.apply_map(g), "applied")
        self.assertEqual(cfg.stat().st_mode & 0o777, 0o770)


class RealFixture(unittest.TestCase):
    """The banked real MIW cfg (352 B, ZVitor May-2023 engine) must resolve and
    round-trip with its tail byte-identical."""
    FIXTURE = Path(__file__).parent / "fixtures" / "openbor" / "miw_352.cfg"

    def test_real_cfg_resolves_and_applies(self):
        if not self.FIXTURE.exists():
            self.skipTest("fixture not present")
        with tempfile.TemporaryDirectory() as td:
            g = make_game(Path(td), GEN_LATE3, name="MIW")
            (g / "Saves" / "game.cfg").write_bytes(self.FIXTURE.read_bytes())
            before = (g / "Saves" / "game.cfg").read_bytes()
            with mock.patch.object(M, "_STORE", Path(td) / "s.json"):
                self.assertEqual(C.apply_map(g), "applied")
            after = (g / "Saves" / "game.cfg").read_bytes()
            lay = C.resolve_layout(after, (2023, 5))
            self.assertEqual((lay.offset, lay.slots, lay.stride),
                             (0x34, 13, 64))
            self.assertEqual(before[:0x34], after[:0x34])
            self.assertEqual(before[0x34 + 208:], after[0x34 + 208:])


if __name__ == "__main__":
    unittest.main()
