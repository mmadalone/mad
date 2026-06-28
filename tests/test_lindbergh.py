"""Golden/invariant tests for the Sega Lindbergh MAD backend (settings + binder).

Pure where possible (synthetic ini text + profile dicts), so they don't depend on the
live ROM set. The CRC test skips if ramboM.elf isn't present. Run with the rest:
    python3 -m unittest discover -s tests -t .
"""
import json
import unittest
from pathlib import Path

from lib.madsrv import cfgutil, lindbergh_cmds as L

SAMPLE = (
    "[Display]\n"
    "WIDTH = 1280\n"
    "HEIGHT = 768\n"
    "FULLSCREEN = true\n"
    "KEEP_ASPECT_RATIO = true\n"
    "\n"
    "[Input]\n"
    "INPUT_MODE = 2\n"
    "\n"
    "[Emulation]\n"
    "REGION = US\n"
    "FREEPLAY = true\n"
    "\n"
    "[GameSpecific]\n"
    "CPU_FREQ_GHZ = 0.0\n"
    "\n"
    "[CrossHairs]\n"
    "ENABLE_CROSSHAIRS = true\n"
    'P1_CROSSHAIR_PATH = "/home/deck/ROMs/lindbergh/_crosshairs/p1.png"\n'
    "\n"
    "[System]\n"
    "DEBUG_MSGS = true\n"
    "\n"
    "[EVDEV]\n"
    'PLAYER_1_BUTTON_1 = "SINDENLIGHTGUN_MOUSE__SMOOTHED_P1__BTN_LEFT"\n'
)

# The lindbergh.ini [EVDEV] keys the loader actually reads (config.c) — every profile
# row key must be one of these, or it's a dead binding.
VALID_KEYS = (
    {f"ANALOGUE_{i}" for i in range(1, 9)}
    | {f"PLAYER_{p}_BUTTON_{b}" for p in (1, 2)
       for b in [*range(1, 9), "UP", "DOWN", "LEFT", "RIGHT", "SERVICE", "START"]}
    | {f"PLAYER_{p}_COIN" for p in (1, 2)} | {"TEST_BUTTON"}
)

GUN = {"gameid": "SBLC", "genre": "shooting", "gun": True, "native_w": 1280, "native_h": 768}
DRIVE = {"gameid": "SBMB", "genre": "driving", "gun": False, "native_w": 800, "native_h": 480}


def _keys(groups):
    return {s["key"] for g in groups for s in g["settings"]}


class ProfilesData(unittest.TestCase):
    def test_profiles_sane(self):
        data = json.loads((L.PROFILES_PATH).read_text())
        self.assertGreater(len(data), 50)
        genres = set()
        for crc, v in data.items():
            self.assertRegex(crc, r"^[0-9a-f]{8}$")            # CRC hex key
            for k in ("gameid", "name", "genre", "gun", "native_w", "native_h", "rows"):
                self.assertIn(k, v, f"{crc} missing {k}")
            genres.add(v["genre"])
            self.assertEqual(v["gun"], v["genre"] == "shooting")
            if v["rows"]:
                seen = set()
                for r in v["rows"]:
                    self.assertIn(r["key"], VALID_KEYS, f"{crc} bad key {r['key']}")
                    self.assertNotIn(r["key"], seen, f"{crc} dup key {r['key']}")
                    seen.add(r["key"])
                    self.assertTrue(r["label"])
        self.assertEqual(genres, {"shooting", "driving", "digital", "abc", "mahjong"})


class SettingsSchema(unittest.TestCase):
    def test_cpufreq_only_hotd4(self):
        self.assertIn("CPU_FREQ_GHZ", _keys(L._build_groups(SAMPLE, GUN)))      # SBLC
        self.assertNotIn("CPU_FREQ_GHZ", _keys(L._build_groups(SAMPLE, DRIVE)))  # SBMB

    def test_crosshairs_gun_only(self):
        self.assertIn("ENABLE_CROSSHAIRS", _keys(L._build_groups(SAMPLE, GUN)))
        self.assertNotIn("ENABLE_CROSSHAIRS", _keys(L._build_groups(SAMPLE, DRIVE)))

    def test_resolution_presets(self):
        rows = [s for g in L._build_groups(SAMPLE, GUN) for s in g["settings"]
                if s["key"] == "Resolution"]
        self.assertEqual(len(rows), 1)
        # H1 contract: value must equal one of options (the C++ matches value == options[i]),
        # and each option must carry a parseable WxH (lindbergh.set re.search-extracts it).
        self.assertIn(rows[0]["value"], rows[0]["options"])
        whs = {o.split()[0] for o in rows[0]["options"]}
        self.assertIn("1280x768", whs)
        self.assertIn("1920x1080", whs)
        self.assertTrue(rows[0]["value"].startswith("1280x768"))


class ByteStable(unittest.TestCase):
    def test_single_key_edit_preserves_rest(self):
        out = cfgutil.ini_replace(SAMPLE, "Emulation", "REGION", "JP")
        self.assertIsNotNone(out)
        # exactly one line differs, and it's the REGION line
        diff = [(a, b) for a, b in zip(SAMPLE.splitlines(), out.splitlines()) if a != b]
        self.assertEqual(diff, [("REGION = US", "REGION = JP")])
        self.assertEqual(len(SAMPLE.splitlines()), len(out.splitlines()))


class BinderRows(unittest.TestCase):
    def test_gun_hides_axes_nongun_shows(self):
        prof = {"gun": True, "rows": [{"key": "ANALOGUE_1", "label": "Aim X", "axis": True},
                                      {"key": "PLAYER_1_BUTTON_1", "label": "Trigger", "axis": False}]}
        secs, _ = L._binder_data("", prof, True)
        self.assertNotIn("axes", secs)
        prof2 = dict(prof, gun=False)
        secs2, _ = L._binder_data("", prof2, False)
        self.assertIn("axes", secs2)

    def test_generic_fallback_valid_keys(self):
        for r in L._generic_rows(False):
            self.assertIn(r["key"], VALID_KEYS)


class RegionCrc(unittest.TestCase):
    def test_rambo_crc(self):
        elf = Path.home() / "ROMs/lindbergh/rambo.lindbergh/elf/ramboM.elf"
        if not elf.is_file():
            self.skipTest("ramboM.elf not present")
        self.assertEqual(L._region_crc(elf), "048f49dd")  # == loader's RAMBO constant


class BinderUnion(unittest.TestCase):
    def test_unions_ini_keys_the_profile_omits(self):
        # M3: profile lists only P1 + P2 Coin; the ini binds P2 buttons -> union exposes them.
        prof = {"gun": True, "rows": [
            {"key": "PLAYER_1_BUTTON_1", "label": "P1 Trigger", "axis": False},
            {"key": "PLAYER_2_COIN", "label": "P2 Coin", "axis": False}]}
        ini = ('[EVDEV]\n'
               'PLAYER_1_BUTTON_1 = "X"\n'
               'PLAYER_2_BUTTON_1 = "Y"\n'
               'PLAYER_2_BUTTON_START = "Z"\n')
        secs, _ = L._binder_data(ini, prof, True)
        self.assertIn("PLAYER_2_BUTTON_1", secs.get("p2", []))
        self.assertIn("PLAYER_2_BUTTON_START", secs.get("p2", []))
        # a key NOT in the ini stays out (no clutter)
        self.assertNotIn("PLAYER_2_BUTTON_4", secs.get("p2", []))


class IniDrivenBinder(unittest.TestCase):
    """The control SET shown by the binder is whatever the game's ini binds (loader-safe); labels
    come from the profile, then the generic fallback. ('feed the input page from the inis'.)"""

    def test_set_from_ini_labels_from_profile(self):
        prof = {"gun": False, "rows": [
            {"key": "ANALOGUE_1", "label": "Wheel Axis", "axis": True},
            {"key": "ANALOGUE_3", "label": "Gas", "axis": True},
            {"key": "PLAYER_1_BUTTON_START", "label": "Start", "axis": False},
            {"key": "PLAYER_1_BUTTON_7", "label": "Unused Seven", "axis": False}]}  # NOT in the ini
        ini = ('[EVDEV]\n'
               'ANALOGUE_1 = "PAD_ABS_X"\n'
               'ANALOGUE_3 = "PAD_ABS_RZ"\n'
               'PLAYER_1_BUTTON_START = "PAD_BTN_START"\n'
               'PLAYER_1_BUTTON_2 = "PAD_BTN_EAST"\n')  # in the ini, NOT in the profile
        _, rows = L._binder_data(ini, prof, False)
        self.assertNotIn("PLAYER_1_BUTTON_7", rows)            # profile-only key not shown
        self.assertEqual(rows["ANALOGUE_1"]["label"], "Wheel Axis")
        self.assertEqual(rows["ANALOGUE_3"]["label"], "Gas")   # profile label wins
        self.assertEqual(rows["PLAYER_1_BUTTON_2"]["label"], "P1 Button 2")  # ini-only -> generic

    def test_no_profile_uses_generic_on_ini_set(self):
        ini = '[EVDEV]\nANALOGUE_1 = "PAD_ABS_X"\nPLAYER_1_BUTTON_1 = "PAD_BTN_SOUTH"\n'
        _, rows = L._binder_data(ini, None, False)
        self.assertEqual(set(rows), {"ANALOGUE_1", "PLAYER_1_BUTTON_1"})
        self.assertEqual(rows["PLAYER_1_BUTTON_1"]["label"], "P1 Button 1")

    def test_deadzone_and_nonbindable_excluded(self):
        ini = '[EVDEV]\nANALOGUE_1 = "PAD_ABS_X"\nANALOGUE_DEADZONE_1 = 0 0 0\n'
        _, rows = L._binder_data(ini, None, False)
        self.assertEqual(set(rows), {"ANALOGUE_1"})

    def test_ini_file_order_preserved(self):
        ini = ('[EVDEV]\nPLAYER_1_BUTTON_3 = "a"\nPLAYER_1_BUTTON_1 = "b"\nPLAYER_1_BUTTON_2 = "c"\n')
        secs, _ = L._binder_data(ini, None, False)
        self.assertEqual(secs["p1"], ["PLAYER_1_BUTTON_3", "PLAYER_1_BUTTON_1", "PLAYER_1_BUTTON_2"])

    def test_clean_tok_strips_inline_comment_and_quotes(self):
        self.assertEqual(L._clean_tok('"DEV_ABS_X"    # Wheel / Steering'), "DEV_ABS_X")
        self.assertEqual(L._clean_tok('"DEV_ABS_X"'), "DEV_ABS_X")
        self.assertEqual(L._clean_tok('""    # Coin 2'), "")
        self.assertEqual(L._clean_tok(None), "")

    def test_commented_value_displays_clean(self):
        ini = '[EVDEV]\nANALOGUE_1 = "PAD_ABS_X"    # Wheel / Steering\n'
        _, rows = L._binder_data(ini, None, False)
        self.assertEqual(rows["ANALOGUE_1"]["display"], "PAD_ABS_X")
        self.assertFalse(rows["ANALOGUE_1"]["warn"])

    def test_empty_evdev_falls_back_to_profile(self):
        prof = {"gun": False, "rows": [{"key": "PLAYER_1_BUTTON_1", "label": "Punch", "axis": False}]}
        _, rows = L._binder_data("", prof, False)        # no [EVDEV] -> profile fallback
        self.assertEqual(rows["PLAYER_1_BUTTON_1"]["label"], "Punch")


class AnalogChannelMapping(unittest.TestCase):
    """Generator: TeknoParrot AnalogN is a BYTE OFFSET (JVS analog channels are 16-bit = 2 bytes each),
    so AnalogN addresses channel N/2; the loader's ANALOGUE_k drives channel k-1; so AnalogN ->
    ANALOGUE_(N/2+1). Proof: gun profiles map P1 X/Y=Analog0/2 and P2 X/Y=Analog4/6 onto the loader's
    contiguous crosshair channel pairs 0+1 and 2+3 (evdevInput.c updateCrosshairPosition)."""

    def _tp(self):
        import importlib.util
        p = Path(__file__).resolve().parent.parent / "tools" / "tp2lindbergh.py"
        spec = importlib.util.spec_from_file_location("tp2lindbergh", p)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m

    def test_byte_offset_to_channel(self):
        tp = self._tp()
        # even byte offsets -> contiguous channels 0,1,2,3 (gun X/Y pairs; driving wheel/gas/brake)
        self.assertEqual(tp.tp_to_inikey("Analog0"), "ANALOGUE_1")   # ch0
        self.assertEqual(tp.tp_to_inikey("Analog2"), "ANALOGUE_2")   # ch1  (Harley lean; the on-device fix)
        self.assertEqual(tp.tp_to_inikey("Analog4"), "ANALOGUE_3")   # ch2
        self.assertEqual(tp.tp_to_inikey("Analog6"), "ANALOGUE_4")   # ch3  (Harley brake)
        # high 2-player channels stay in range: Hummer P2 Analog12 -> ch6 -> ANALOGUE_7
        self.assertEqual(tp.tp_to_inikey("Analog12"), "ANALOGUE_7")
        # odd byte offsets (rare: IDTA/SWDC) floor to the same channel index
        self.assertEqual(tp.tp_to_inikey("Analog1"), "ANALOGUE_1")
        self.assertEqual(tp.tp_to_inikey("Analog3"), "ANALOGUE_2")
        # special / suffixed entries are not evdev-bindable
        self.assertIsNone(tp.tp_to_inikey("Analog0Special1"))


class CaptureTokens(unittest.TestCase):
    def test_face_buttons_cardinal(self):  # H2
        import lib.lindbergh_capture as C
        self.assertEqual(C.kname(0x130), "BTN_SOUTH")
        self.assertEqual(C.kname(0x131), "BTN_EAST")

    def test_san_loader_parity(self):  # M2
        import lib.lindbergh_capture as C
        self.assertEqual(C.san("8BitDo SN30 Pro+"), "8BITDO_SN30_PRO+")  # keeps '+'
        self.assertEqual(C.san("T.16000M"), "T.16000M")                  # keeps '.'
        # shipped Sinden token must be byte-identical to what the loader/ini already use
        self.assertEqual(C.san("SindenLightgun Mouse (Smoothed P1)"),
                         "SINDENLIGHTGUN_MOUSE__SMOOTHED_P1_")


class CaptureAnalogTrigger(unittest.TestCase):
    """An analog trigger captured as a digital button must yield the BARE axis token
    (..._ABS_Z / ..._ABS_RZ), NOT the loader's stuck-prone ANALOGUE_TO_DIGITAL_MAX (..._ABS_Z_MAX).
    Drives the real lindbergh_capture._read with a fake device + patched select()."""

    @staticmethod
    def _capture(axis_code, press, *, rest=0, mn=0, mx=255, axis=False):
        import lib.lindbergh_capture as C
        from evdev import ecodes
        from unittest import mock

        class _Ev:
            def __init__(s, t, c, v): s.type, s.code, s.value = t, c, v

        class _Info:
            def __init__(s, lo, hi, val): s.min, s.max, s.value = lo, hi, val

        class _Dev:
            name = "Xbox 360 Wireless Receiver"
            def __init__(s, evs): s._evs = evs
            def read(s): return list(s._evs)

        devs = {1: (_Dev([_Ev(ecodes.EV_ABS, axis_code, press)]),
                    "XBOX_360_WIRELESS_RECEIVER",
                    ({axis_code: _Info(mn, mx, rest)}, {axis_code: rest}))}

        class _FakeSelect:
            @staticmethod
            def select(rlist, *a, **k): return (list(rlist), [], [])

        with mock.patch.object(C, "select", _FakeSelect):
            return [t["token"] for t in C._read(devs, axis)]

    def test_lt_abs_z_is_bare(self):  # X-Arcade P1 LT
        from evdev import ecodes
        self.assertEqual(self._capture(ecodes.ABS_Z, 255),
                         ["XBOX_360_WIRELESS_RECEIVER_ABS_Z"])  # no _MAX

    def test_rt_abs_rz_is_bare(self):  # X-Arcade P1 RT
        from evdev import ecodes
        self.assertEqual(self._capture(ecodes.ABS_RZ, 255),
                         ["XBOX_360_WIRELESS_RECEIVER_ABS_RZ"])  # no _MAX

    def test_no_max_suffix_ever(self):
        from evdev import ecodes
        for code in (ecodes.ABS_Z, ecodes.ABS_RZ):
            self.assertFalse(any(t.endswith("_MAX") for t in self._capture(code, 255)))

    def test_min_resting_axis_still_min(self):
        # documented edge: an axis resting HIGH driven to its MIN still yields _MIN (not X-Arcade
        # hardware, which rests at 0). Left as-is; the loader has no clean named token for it.
        from evdev import ecodes
        self.assertEqual(self._capture(ecodes.ABS_Z, 0, rest=255),
                         ["XBOX_360_WIRELESS_RECEIVER_ABS_Z_MIN"])

    def test_axis_mode_unchanged(self):
        # the --axis ANALOGUE_n path already emits a bare token; this fix must not touch it
        from evdev import ecodes
        self.assertEqual(self._capture(ecodes.ABS_X, 32000, mn=-32768, mx=32767, axis=True),
                         ["XBOX_360_WIRELESS_RECEIVER_ABS_X"])

    def test_dpad_hat_min_max(self):
        # a controller D-pad on a hat axis (rest 0, range -1..1): -1 -> _MIN (up/left),
        # +1 -> _MAX (down/right), 0 -> nothing. Previously missed (guard never fired).
        from evdev import ecodes
        self.assertEqual(self._capture(ecodes.ABS_HAT0Y, -1, mn=-1, mx=1),
                         ["XBOX_360_WIRELESS_RECEIVER_ABS_HAT0Y_MIN"])   # Up
        self.assertEqual(self._capture(ecodes.ABS_HAT0Y, 1, mn=-1, mx=1),
                         ["XBOX_360_WIRELESS_RECEIVER_ABS_HAT0Y_MAX"])   # Down
        self.assertEqual(self._capture(ecodes.ABS_HAT0X, -1, mn=-1, mx=1),
                         ["XBOX_360_WIRELESS_RECEIVER_ABS_HAT0X_MIN"])   # Left
        self.assertEqual(self._capture(ecodes.ABS_HAT0Y, 0, mn=-1, mx=1), [])  # rest

    def test_axis_mode_ignores_hat(self):
        # in --axis (ANALOGUE_n) capture a stray d-pad must NOT bind a bogus bare hat token
        from evdev import ecodes
        self.assertEqual(self._capture(ecodes.ABS_HAT0X, 1, mn=-1, mx=1, axis=True), [])


class StuckTriggerMigration(unittest.TestCase):
    """_migrate_stuck_triggers strips the buggy _MAX suffix from digital-button EVDEV bindings."""

    def test_lt_rt_both_players(self):
        ini = ('[EVDEV]\n'
               'PLAYER_1_BUTTON_3 = "XBOX_360_WIRELESS_RECEIVER_ABS_Z_MAX"\n'
               'PLAYER_1_BUTTON_4 = "XBOX_360_WIRELESS_RECEIVER_ABS_RZ_MAX"\n'
               'PLAYER_2_BUTTON_3 = "XBOX_360_WIRELESS_RECEIVER_2_ABS_Z_MAX"\n'
               'PLAYER_2_BUTTON_4 = "XBOX_360_WIRELESS_RECEIVER_2_ABS_RZ_MAX"\n')
        out, n = L._migrate_stuck_triggers(ini)
        self.assertEqual(n, 4)
        self.assertNotIn("_MAX", out)
        self.assertIn('PLAYER_1_BUTTON_3 = "XBOX_360_WIRELESS_RECEIVER_ABS_Z"', out)
        self.assertIn('PLAYER_2_BUTTON_4 = "XBOX_360_WIRELESS_RECEIVER_2_ABS_RZ"', out)

    def test_leaves_analogue_and_plain_and_bare(self):
        ini = ('[EVDEV]\n'
               'PLAYER_1_BUTTON_1 = "XBOX_360_WIRELESS_RECEIVER_BTN_SOUTH"\n'   # digital button, fine
               'PLAYER_1_BUTTON_2 = "XBOX_360_WIRELESS_RECEIVER_ABS_Z"\n'        # already bare
               'ANALOGUE_2 = "SOME_WHEEL_ABS_Y_MAX"\n')                          # analog channel: untouched
        out, n = L._migrate_stuck_triggers(ini)
        self.assertEqual(n, 0)
        self.assertEqual(out, ini)

    def test_scoped_to_evdev_section(self):
        ini = ('[EVDEV]\n'
               'PLAYER_1_BUTTON_3 = "DEV_ABS_Z_MAX"\n'
               '\n[Other]\n'
               'STRAY_ABS_Z_MAX = "x"\n')
        out, n = L._migrate_stuck_triggers(ini)
        self.assertEqual(n, 1)
        self.assertIn('STRAY_ABS_Z_MAX = "x"', out)  # outside [EVDEV], untouched

    def test_idempotent(self):
        once, _ = L._migrate_stuck_triggers('[EVDEV]\nPLAYER_1_BUTTON_3 = "DEV_ABS_Z_MAX"\n')
        twice, n2 = L._migrate_stuck_triggers(once)
        self.assertEqual(n2, 0)
        self.assertEqual(once, twice)

    def test_leaves_hat_dpad_max(self):
        # a hat D-pad legitimately binds _MAX (down/right) and must NOT be stripped (a hat rests
        # at its midpoint so its _MAX releases correctly); only true trigger _MAX is converted.
        ini = ('[EVDEV]\n'
               'PLAYER_1_BUTTON_DOWN = "PAD_ABS_HAT0Y_MAX"\n'
               'PLAYER_1_BUTTON_RIGHT = "PAD_ABS_HAT0X_MAX"\n'
               'PLAYER_1_BUTTON_3 = "PAD_ABS_Z_MAX"\n')
        out, n = L._migrate_stuck_triggers(ini)
        self.assertEqual(n, 1)  # only the ABS_Z trigger
        self.assertIn('PLAYER_1_BUTTON_DOWN = "PAD_ABS_HAT0Y_MAX"', out)
        self.assertIn('PLAYER_1_BUTTON_RIGHT = "PAD_ABS_HAT0X_MAX"', out)
        self.assertIn('PLAYER_1_BUTTON_3 = "PAD_ABS_Z"', out)


class QuitComboDisplay(unittest.TestCase):
    """_quit_combo_for reads the per-game [quit_combo.lindbergh-<titleid>] for display."""

    def test_resolves_names_from_policy(self):
        from unittest import mock
        merged = {"quit_combo": {"lindbergh-vf5": {"buttons": [314, 315]}}}
        with mock.patch("lib.policy.load_merged", lambda: merged):
            qc = L._quit_combo_for("vf5")
        self.assertEqual(qc["scope"], "lindbergh-vf5")
        self.assertEqual(qc["buttons"], [314, 315])
        self.assertEqual(qc["names"], ["SELECT", "START"])
        self.assertEqual(qc["display"], "SELECT + START")

    def test_empty_when_unset(self):
        from unittest import mock
        with mock.patch("lib.policy.load_merged", lambda: {"quit_combo": {}}):
            qc = L._quit_combo_for("id5")
        self.assertEqual(qc["buttons"], [])
        self.assertEqual(qc["display"], "")
        self.assertEqual(qc["scope"], "lindbergh-id5")

    def test_load_buffer_self_heals_and_marks_dirty(self):
        from unittest import mock
        ini = '[EVDEV]\nPLAYER_1_BUTTON_3 = "DEV_ABS_Z_MAX"\n'
        with mock.patch.object(L, "_gamedir", lambda t: Path("/x")), \
             mock.patch.object(L, "_ini_of", lambda gd: Path("/x/lindbergh.ini")), \
             mock.patch.object(L, "_profile_of", lambda gd: None), \
             mock.patch.object(L.cfgutil, "read_text", lambda p: ini):
            L._load_buffer("DEADBEEF")
        self.assertTrue(L._buf["dirty"])
        self.assertEqual(L._buf["migrated"], 1)
        self.assertNotIn("_MAX", L._buf["text"])


class QuitComboFallback(unittest.TestCase):
    """quit-combo-watcher._read_quit_combo layering: per-game [quit_combo.lindbergh-<stem>]
    overrides the system-wide [quit_combo.lindbergh] overrides the global default."""

    POL = ('[quit_combo]\nbuttons = [314, 315]\nhold_sec = 2.0\n'
           '[quit_combo.lindbergh]\nbuttons = [106, 108]\n'
           '[quit_combo.lindbergh-vf5]\nbuttons = [304, 305]\n'
           '[quit_combo.switch]\nbuttons = [9, 10]\n')

    def _read(self, system, toml=None):
        import importlib.util, tempfile
        f = Path(tempfile.mkdtemp()) / "local.toml"
        f.write_text(toml if toml is not None else self.POL)
        spec = importlib.util.spec_from_file_location(
            "qcw_test", str(Path(__file__).resolve().parent.parent / "quit-combo-watcher.py"))
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        m.POLICY = Path("/nonexistent")  # base absent -> only our temp local
        m.LOCAL_POLICY = f
        return m._read_quit_combo(system)

    def test_per_game_wins(self):
        combo, _ = self._read("lindbergh-vf5")
        self.assertEqual(sorted(combo), [304, 305])

    def test_falls_back_to_system_lindbergh(self):
        combo, hold = self._read("lindbergh-id5")   # no per-game key -> [quit_combo.lindbergh]
        self.assertEqual(sorted(combo), [106, 108])  # NOT the global [314,315]
        self.assertEqual(hold, 2.0)

    def test_non_hyphen_system_unchanged(self):
        combo, _ = self._read("switch")
        self.assertEqual(sorted(combo), [9, 10])

    def test_unknown_system_falls_to_global(self):
        combo, _ = self._read("pcsx2")
        self.assertEqual(sorted(combo), [314, 315])


class LindberghPadsRpc(unittest.TestCase):
    """The per-game pads -> players RPC layer in lindbergh_cmds."""

    def test_captured_tag(self):
        self.assertEqual(L._captured_tag("XBOX_360_WIRELESS_RECEIVER_2_BTN_SOUTH", "BTN_SOUTH"),
                         "XBOX_360_WIRELESS_RECEIVER_2")           # keeps the _2 dup suffix
        self.assertEqual(L._captured_tag("DEV_ABS_HAT0Y_MIN", "ABS_HAT0Y_MIN"), "DEV")
        self.assertEqual(L._captured_tag("DEV_BTN_A", "BTN_X"), "")  # name doesn't match -> reject
        self.assertEqual(L._captured_tag("", "BTN_A"), "")

    def _tmp_game(self):
        import tempfile
        gd = Path(tempfile.mkdtemp()) / "g.lindbergh"
        gd.mkdir()
        (gd / "g.lindbergh.commands").write_text("g.elf\n")
        (gd / "g.elf").write_text("x")
        (gd / "lindbergh.ini").write_text('[EVDEV]\nPLAYER_1_BUTTON_1 = ""\n')
        return gd

    def test_pads_get_structure(self):
        from unittest import mock
        from lib import lindbergh_pads as P
        gd = self._tmp_game()
        P.save(gd, {"priority": ["XARC"], "pads": {"XARC": {"BUTTON_1": "BTN_SOUTH"}}})
        conn = [{"tag": "XARC", "name": "X", "label": "X-Arcade #1", "path": "/d"}]
        with mock.patch.object(L, "_gamedir", lambda t: gd), \
             mock.patch.object(L.lindbergh_pads, "connected_pads", lambda: conn):
            r = L._pads_get({"titleid": "g"})
        self.assertEqual(r["players"], 2)
        rows = {p["tag"]: p for p in r["pads"]}
        self.assertIn("XARC", rows)
        self.assertTrue(rows["XARC"]["connected"])
        self.assertTrue(rows["XARC"]["mapped"])

    def test_pad_load_set_order_clear(self):
        from unittest import mock
        from lib import lindbergh_pads as P
        gd = self._tmp_game()
        with mock.patch.object(L, "_gamedir", lambda t: gd):
            L._pads_set_order({"titleid": "g", "order": ["XARC", "DS"]})
            self.assertEqual(P.load(gd)["priority"], ["XARC", "DS"])
            pl = L._pad_load({"titleid": "g", "tag": "XARC"})
            self.assertEqual(pl["rows"]["BUTTON_1"]["display"], "— unbound")
            self.assertIn("BUTTON_1", pl["controls"])
            # simulate a successful bind (pad_bind itself needs a real device press)
            d = P.load(gd)
            d.setdefault("pads", {}).setdefault("XARC", {})["BUTTON_1"] = "BTN_SOUTH"
            P.save(gd, d)
            self.assertEqual(L._pad_load({"titleid": "g", "tag": "XARC"})["rows"]["BUTTON_1"]["display"],
                             "BTN_SOUTH")
            L._pad_clear({"titleid": "g", "tag": "XARC", "control": "BUTTON_1"})
            self.assertEqual(L._pad_load({"titleid": "g", "tag": "XARC"})["rows"]["BUTTON_1"]["display"],
                             "— unbound")

    def test_games_pads_filter(self):
        from unittest import mock
        games = [{"titleid": "id5", "name": "ID5"}, {"titleid": "rambo", "name": "Rambo"}]
        with mock.patch.object(L, "_games", lambda: games), \
             mock.patch.object(L, "_is_gun", lambda t: t == "rambo"):
            allg = [g["titleid"] for g in L._games_cmd({})["games"]]
            padg = [g["titleid"] for g in L._games_cmd({"pads": True})["games"]]
        self.assertEqual(allg, ["id5", "rambo"])
        self.assertEqual(padg, ["id5"])   # the lightgun game is excluded


if __name__ == "__main__":
    unittest.main()
