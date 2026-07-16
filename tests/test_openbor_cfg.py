"""openbor_cfg: era resolution, layout validation, and the in-place splice.

Synthetic fixtures fabricate each engine generation's s_savedata shape
(size, keys offset, slot count, stride, sentinel) with patterned tail bytes,
so every test can assert the strongest invariant: NOTHING outside the keys
block changes, ever."""
import re
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


# How each engine generation's SDL enumerates the SAME pad — the log lines the
# writer reads to place offsets. Real samples captured on-device 2026-07-16.
GEOM_XINPUT_LINE = b"UNKNOWN (XInput Controller #1) - 6 axes, 11 buttons, 1 hat(s), rumble support: yes"
GEOM_WINEJOY_LINE = b"Wine joystick driver - 5 axes, 10 buttons, 1 hat(s)"


def make_game(tmp: Path, gen, name="Game", cfgname="game.cfg",
              geom_line=GEOM_XINPUT_LINE, **kw) -> Path:
    size, offset, slots, stride, sentinel, banner = gen
    d = tmp / name
    (d / "Saves").mkdir(parents=True)
    (d / "Logs").mkdir()
    (d / "Saves" / cfgname).write_bytes(
        make_cfg(size, offset, slots, stride, sentinel, **kw))
    log = b"OpenBoR v_x Build , " + banner + b"\n"
    if geom_line:
        log += b"Input init...\t" + geom_line + b"\n"
    (d / "Logs" / "OpenBorLog.txt").write_bytes(log)
    return d


class EraResolution(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        # apply_map WRITES the store now (it marks a game seeded), so every test
        # that calls it must redirect the store or the suite scribbles seed
        # markers into the real one on the developer's own rig. It did exactly
        # that on 2026-07-16 — a fixture named "Contra" landed in the live
        # /home/deck/Emulation/storage/openbor/input-maps.json.
        self.store = self.root / "input-maps.json"
        self.patch = mock.patch.object(M, "_STORE", self.store)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_era_parse(self):
        g = make_game(self.root, GEN_2016)
        self.assertEqual(C.engine_era(g), (2016, 12))

    def test_era_missing_log(self):
        g = make_game(self.root, GEN_2016)
        (g / "Logs" / "OpenBorLog.txt").unlink()
        self.assertIsNone(C.engine_era(g))

    def test_dump_cli_decodes_under_both_geometries(self):
        # The dump CLI is the tool every on-device gate reads — a stale table
        # reference here crashed it while the whole suite still passed.
        g = make_game(self.root, GEN_LATE3, name="DumpNew")
        out = C.dump(g)
        self.assertIn("geom=11btn/6ax hat@23", out)
        self.assertIn("up=J0.hat:up", out)
        self.assertIn("special=J0.ax:rt", out)
        g2 = make_game(self.root, GEN_2016, name="DumpOld",
                       geom_line=GEOM_WINEJOY_LINE)
        out2 = C.dump(g2)
        self.assertIn("geom=10btn/5ax hat@20", out2)
        g3 = make_game(self.root, GEN_LATE3, name="DumpNone", geom_line=b"")
        self.assertIn("would refuse to write", C.dump(g3))

    def test_geometry_read_from_each_engine_generation(self):
        # The SAME pad, two engine views — every offset shifts with it.
        g = make_game(self.root, GEN_LATE3, name="New")
        self.assertEqual(C.pad_geometry(g), (11, 6))            # XInput
        g2 = make_game(self.root, GEN_2016, name="Old",
                       geom_line=GEOM_WINEJOY_LINE)
        self.assertEqual(C.pad_geometry(g2), (10, 5))           # Wine joystick

    def test_no_geometry_refuses(self):
        g = make_game(self.root, GEN_LATE3, name="NoGeom", geom_line=b"")
        self.assertIsNone(C.pad_geometry(g))
        self.assertEqual(C.apply_map(g), "skip-no-geometry")

    def test_old_engine_dpad_matches_the_on_device_truth(self):
        # Regression lock for the P1 gate bug: with the Wine-joystick view
        # (10 btn/5 axes -> hat base 20) the writer must produce EXACTLY the
        # d-pad Miquel configured by hand in Contrav2 on 2026-07-16.
        # Hardcoding the XInput base 23 wrote 624/626/627/625 and broke it.
        g = make_game(self.root, (348, 0x34, 12, 32, -999,
                                  b"Compile Date: Jan 31 2013"),
                      name="Contra", geom_line=GEOM_WINEJOY_LINE)
        # Give special a DISTINCT existing bind (btn:rb, as GHDC really ships)
        # so the assertion below can tell "kept" from "guessed at offset 22" —
        # the fixture's own special is 601+22, which on a 5-axis view is the
        # d-pad-down code, and would make the two outcomes look identical.
        cfg = C.locate_cfg(g)
        raw = bytearray(cfg.read_bytes())
        struct.pack_into("<i", raw, 0x34 + 9 * 4, 606)          # P1 special = btn:rb
        cfg.write_bytes(bytes(raw))

        self.assertEqual(C.apply_map(g), "applied")
        data = cfg.read_bytes()
        rows = C.resolve_layout(data, (2013, 1)).rows(data)
        self.assertEqual(list(rows[0][:4]), [621, 623, 624, 622])
        # buttons keep the same order under both drivers
        self.assertEqual(rows[0][8], 601)                       # jump = A
        self.assertEqual(rows[0][4], 603)                       # atk1 = X
        # ax:rt is INEXPRESSIBLE on a 5-axis view: never guessed at an offset,
        # and never wiped either — the game's own working bind stays put.
        self.assertEqual(rows[0][9], 606)                       # special kept

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


class StoreIsolation(unittest.TestCase):
    """apply_map writes the store (the seed marker), so an un-patched _STORE in
    ANY test scribbles into the real one on this rig. That happened for real on
    2026-07-16: a fixture named "Contra" was left in
    /home/deck/Emulation/storage/openbor/input-maps.json."""

    def test_every_apply_map_test_redirects_the_store(self):
        src = Path(__file__).read_text()
        cls, patched, calls = None, set(), {}
        for line in src.splitlines():
            m = re.match(r"class (\w+)\(", line)
            if m:
                cls = m.group(1)
            if cls and '_STORE' in line and "mock.patch" in line:
                patched.add(cls)
            if cls and "C.apply_map(" in line and "def " not in line:
                calls.setdefault(cls, 0)
                calls[cls] += 1
        unguarded = sorted(set(calls) - patched - {"StoreIsolation"})
        self.assertEqual(unguarded, [],
                         f"these classes call apply_map without redirecting "
                         f"M._STORE, so they write the REAL store: {unguarded}")


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
        # Seeded now, so a second launch must not touch the file at all — that
        # is what lets an in-game rebind survive. Re-seeding it deliberately
        # still lands the same bytes (idempotent).
        self.assertEqual(C.apply_map(g), "skip-seeded")
        self.assertEqual(cfg.read_bytes(), after, "a seeded game was rewritten")
        M.clear_seeded(g.name)
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
        # A hand-edited out-of-range kb value must never splice a joystick code
        # or a value that would make every later launch refuse the cfg. It is
        # also a token we cannot express, so — like ax:rt on a 5-axis engine —
        # it leaves the game's existing bind ALONE rather than wiping a working
        # control because the store has a typo in it.
        M.set_game_override("Poison", {"esc": "kb:1073741881",
                                       "atk1": "kb:700"})
        g = make_game(self.root, GEN_LATE3, name="Poison")
        self.assertEqual(C.apply_map(g), "applied")
        data = C.locate_cfg(g).read_bytes()
        lay = C.resolve_layout(data, (2023, 5))
        self.assertIsNotNone(lay, "cfg must stay resolvable (not poisoned)")
        rows = lay.rows(data)
        for v in (rows[0][12], rows[0][4]):
            self.assertTrue(lay.is_binding(v), "poison reached the file")
            self.assertNotIn(v, (1073741881, 700), "poison reached the file")
        self.assertEqual(rows[0][12], 0)                         # esc kept kb:0
        self.assertEqual(rows[0][4], 603)                        # atk1 kept btn:x
        # ...and re-seeding it writes the same healthy bytes again rather than
        # compounding the poison (the seed guard would mask that, so lift it).
        M.clear_seeded("Poison")
        self.assertEqual(C.apply_map(g), "unchanged")           # still healthy

    def test_a_working_bind_we_cannot_express_is_KEPT_not_wiped(self):
        # THE GHDC / Golden_Axe_Genesis case, on-device 2026-07-16. Those two
        # 5-axis engines ship special on a REAL button (btn:rb and btn:x — Golden
        # Axe's special IS the magic button). DEFAULT_MAP's special is `ax:rt`,
        # which cannot exist with only 5 axes, so keycode answers UNMAPPED — and
        # writing the sentinel on the strength of that would have killed both,
        # permanently, because we hand off after seeding. Contrav2 cannot even
        # rebind it: its own data/menu.txt has `disablekey special`.
        g = make_game(self.root, GEN_2016, name="OldEngine",
                      geom_line=GEOM_WINEJOY_LINE)               # 10 btn / 5 ax
        cfg = C.locate_cfg(g)
        data = bytearray(cfg.read_bytes())
        lay0 = C.resolve_layout(bytes(data), (2016, 12))
        special_i = M.SLOTS.index("special")
        rb = 601 + 5                                             # P1 special = btn:rb
        struct.pack_into("<i", data, lay0.offset + special_i * 4, rb)
        cfg.write_bytes(bytes(data))

        self.assertEqual(C.apply_map(g), "applied")
        rows = lay0.rows(cfg.read_bytes())
        self.assertEqual(rows[0][special_i], rb,
                         "a working special was wiped by an inexpressible token")

    def test_an_explicit_none_still_unbinds(self):
        # The other half of the rule: `none` is an INTENT to clear, not our
        # vocabulary failing, so it must still write the sentinel even though
        # keycode() reports UNMAPPED for it too.
        g = make_game(self.root, GEN_LATE3, name="Sshot")
        cfg = C.locate_cfg(g)
        lay = C.resolve_layout(cfg.read_bytes(), (2023, 5))
        sshot_i = M.SLOTS.index("sshot")
        data = bytearray(cfg.read_bytes())
        struct.pack_into("<i", data, lay.offset + sshot_i * 4, 601 + 6)
        cfg.write_bytes(bytes(data))
        self.assertEqual(M.DEFAULT_MAP["sshot"], M.NONE_TOKEN)
        self.assertEqual(C.apply_map(g), "applied")
        self.assertEqual(lay.rows(cfg.read_bytes())[0][sshot_i], lay.sentinel,
                         "an explicit `none` must still clear the slot")

    def test_an_in_game_rebind_survives_the_next_launch(self):
        # THE POINT of seeding once. The engine rewrites the cfg from memory on
        # quit, so a rebind made in the game's own Options -> Controls lands in
        # this file; re-applying on every launch (what we used to do) silently
        # undid it before the player ever saw it again.
        g = make_game(self.root, GEN_LATE3, name="Rebound")
        self.assertEqual(C.apply_map(g), "applied")
        cfg = C.locate_cfg(g)
        edited = bytearray(cfg.read_bytes())
        struct.pack_into("<i", edited, 0x34 + 4 * 4, 601 + 1)   # P1 atk1 -> btn:b
        cfg.write_bytes(bytes(edited))
        self.assertEqual(C.apply_map(g), "skip-seeded")
        self.assertEqual(cfg.read_bytes(), bytes(edited),
                         "the player's own binding was overwritten")

    def test_a_map_fix_reaches_a_seeded_game_exactly_once(self):
        # Hands-off must not mean frozen. The marker records WHICH map we seeded,
        # so a DEFAULT_MAP/geometry fix (or an override edit) still reaches the
        # game — once — while a player's own rebind never does (next test).
        g = make_game(self.root, GEN_LATE3, name="Revisable")
        self.assertEqual(C.apply_map(g), "applied")
        self.assertEqual(C.apply_map(g), "skip-seeded")

        M.set_game_override("Revisable", {"atk1": "btn:b"})       # intent changed
        self.assertEqual(C.apply_map(g), "applied")
        lay = C.resolve_layout(C.locate_cfg(g).read_bytes(), (2023, 5))
        self.assertEqual(lay.rows(C.locate_cfg(g).read_bytes())[0][4], 601 + 1)
        self.assertEqual(C.apply_map(g), "skip-seeded", "it re-seeded twice")

    def test_a_players_own_rebind_never_moves_the_fingerprint(self):
        # The fingerprint is of our INTENDED MAP, never of the file — hashing the
        # file would make every in-game rebind look like drift and we would
        # overwrite the exact thing hands-off exists to protect.
        g = make_game(self.root, GEN_LATE3, name="Rebind")
        self.assertEqual(C.apply_map(g), "applied")
        before = M.seed_fingerprint("Rebind")
        cfg = C.locate_cfg(g)
        edited = bytearray(cfg.read_bytes())
        struct.pack_into("<i", edited, 0x34 + 4 * 4, 601 + 1)
        cfg.write_bytes(bytes(edited))
        self.assertEqual(M.seed_fingerprint("Rebind"), before)
        self.assertEqual(C.apply_map(g), "skip-seeded")
        self.assertEqual(cfg.read_bytes(), bytes(edited))

    def test_a_seed_revision_bump_reseeds_every_game(self):
        # The escape hatch for "we fixed the ENCODER, not the map" — e.g. the
        # 2026-07-16 geometry fix, which changed the bytes without changing a
        # single token.
        g = make_game(self.root, GEN_LATE3, name="Rev")
        self.assertEqual(C.apply_map(g), "applied")
        self.assertEqual(C.apply_map(g), "skip-seeded")
        with mock.patch.object(M, "SEED_REVISION", M.SEED_REVISION + 1):
            self.assertIn(C.apply_map(g), ("applied", "unchanged"))

    def test_a_bricked_game_heals_itself(self):
        # OpenBOR's Setup Player N has a `Default` row one line below OK; it and
        # "Restore OpenBoR Defaults" both run clearbuttons(), which puts P1 on
        # KEYBOARD scancodes and unbinds P2-P4. There is no keyboard on this rig,
        # so that is a dead game whose only other recovery is a CLI. Same shape
        # the engine writes when it regenerates a cfg after a savedata delete —
        # which is the ONLY fix for the Contrav2/Jennifer fullscreen crash, since
        # video settings and keys share one struct.
        g = make_game(self.root, GEN_LATE3, name="Bricked")
        self.assertEqual(C.apply_map(g), "applied")
        cfg = C.locate_cfg(g)
        lay = C.resolve_layout(cfg.read_bytes(), (2023, 5))
        data = bytearray(cfg.read_bytes())
        for i, sc in enumerate((82, 81, 80, 79)):        # SDL scancodes: up/down/left/right
            struct.pack_into("<i", data, lay.offset + i * 4, sc)
        cfg.write_bytes(bytes(data))
        self.assertTrue(C.is_bricked(lay, lay.rows(bytes(data))))

        self.assertEqual(C.apply_map(g), "healed", "a dead game was left dead")
        rows = lay.rows(cfg.read_bytes())
        self.assertEqual(rows[0][0], 601 + 23, "P1 up is still not on a pad")

    def test_a_pad_configured_game_is_never_called_bricked(self):
        # The detector must be narrow: it asks ONLY whether P1's movement reaches
        # a joystick. A real player map always does, however odd it looks.
        g = make_game(self.root, GEN_LATE3, name="Odd")
        cfg = C.locate_cfg(g)
        lay = C.resolve_layout(cfg.read_bytes(), (2023, 5))
        rows = lay.rows(cfg.read_bytes())
        self.assertFalse(C.is_bricked(lay, rows))
        # even a weird-but-real map: movement on face buttons, esc on a key
        rows[0][:4] = [601, 602, 603, 604]
        self.assertFalse(C.is_bricked(lay, rows))
        # and a P1 that is unbound entirely IS bricked
        rows[0][:4] = [lay.sentinel] * 4
        self.assertTrue(C.is_bricked(lay, rows))

    def test_a_skip_never_marks_a_game_seeded(self):
        # Every skip must leave the game unseeded, or it is frozen forever on a
        # map it never actually received. The real case: a brand-new game has no
        # engine log yet -> skip-no-geometry -> it must still seed on the launch
        # after that, once the log exists to read the pad shape from.
        g = make_game(self.root, GEN_LATE3, name="Fresh")
        log = g / "Logs" / "OpenBorLog.txt"
        text = log.read_text()
        log.write_text("\n".join(l for l in text.splitlines()
                                 if "axes" not in l))           # drop the pad line
        self.assertEqual(C.apply_map(g), "skip-no-geometry")
        self.assertFalse(M.is_seeded("Fresh"), "a skip marked it seeded")
        log.write_text(text)                                    # log appears
        self.assertEqual(C.apply_map(g), "applied")
        self.assertTrue(M.is_seeded("Fresh"))

    def test_reseed_is_the_way_back_and_is_per_game(self):
        a = make_game(self.root, GEN_LATE3, name="A")
        b = make_game(self.root, GEN_LATE3, name="B")
        C.apply_map(a); C.apply_map(b)
        self.assertEqual(M.clear_seeded("A"), ["A"])
        self.assertFalse(M.is_seeded("A"))
        self.assertTrue(M.is_seeded("B"), "reseeding one game touched another")
        self.assertEqual(M.clear_seeded("A"), [], "clearing twice is not an error")
        self.assertEqual(M.clear_seeded(), ["B"])               # --all

    def test_seeding_does_not_disturb_the_override_store(self):
        # The seed marker is a sibling of "games" in the same file; writing it
        # must not drop a player's per-game overrides.
        M.set_game_override("Keep", {"atk1": "btn:b"})
        g = make_game(self.root, GEN_LATE3, name="Keep")
        self.assertEqual(C.apply_map(g), "applied")
        self.assertTrue(M.is_seeded("Keep"))
        self.assertEqual(M.effective_map("Keep")["atk1"], "btn:b",
                         "marking seeded ate the override")

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
