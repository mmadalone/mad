"""Golden/invariant tests for the Sega Lindbergh MAD backend (settings + binder).

Pure where possible (synthetic ini text + profile dicts), so they don't depend on the
live ROM set. The CRC test skips if ramboM.elf isn't present. Run with the rest:
    python3 -m unittest discover -s tests -t .
"""
import json
import unittest
from pathlib import Path

from lib.madsrv import cfgutil, lindbergh_cmds as L
from tests._ci import skip_on_ci

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
    def _capture(axis_code, press, *, rest=0, mn=0, mx=255, axis=False, direction=False):
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
            return [t["token"] for t in C._read(devs, axis, direction)]

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

    def test_leaves_thumbstick_direction_max(self):
        # a thumbstick bound to a digital direction uses ABS_X/Y _MIN/_MAX legitimately (a centered
        # stick releases cleanly, like a hat). The narrowed regex must NOT strip those -> only true
        # trigger axes (Z/RZ/gas/brake/...) are converted, so stick-for-movement survives a binder load.
        ini = ('[EVDEV]\n'
               'PLAYER_1_BUTTON_DOWN = "PAD_ABS_Y_MAX"\n'
               'PLAYER_1_BUTTON_RIGHT = "PAD_ABS_X_MAX"\n'
               'PLAYER_1_BUTTON_2 = "PAD_ABS_RZ_MAX"\n')   # RT trigger -> still converted
        out, n = L._migrate_stuck_triggers(ini)
        self.assertEqual(n, 1)
        self.assertIn('PLAYER_1_BUTTON_DOWN = "PAD_ABS_Y_MAX"', out)
        self.assertIn('PLAYER_1_BUTTON_RIGHT = "PAD_ABS_X_MAX"', out)
        self.assertIn('PLAYER_1_BUTTON_2 = "PAD_ABS_RZ"', out)


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
        self.assertEqual(L._buf["disk"], ini)   # the PRE-migration raw text, for precise dirty


class SetDirtyPrecise(unittest.TestCase):
    """lindbergh.set returns a PRECISE dirty (staged buffer text != on-disk text), mirroring
    pcsx2_settings/pcsx2_pergame: set a value -> dirty True; revert to the saved value -> False."""

    def _game(self, ini_text):
        import tempfile
        gd = Path(tempfile.mkdtemp()) / "g.lindbergh"
        gd.mkdir()
        (gd / "g.lindbergh.commands").write_text("g.elf\n")
        (gd / "g.elf").write_text("x")
        (gd / "lindbergh.ini").write_text(ini_text)
        return gd

    def test_set_dirty_true_then_revert_to_saved_value_false(self):
        from unittest import mock
        gd = self._game(SAMPLE)
        with mock.patch.object(L, "_gamedir", lambda t: gd):
            L._load_buffer("g")
            on = L._set({"titleid": "g", "key": "REGION", "value": 0})    # US -> JP: differs from disk
            self.assertTrue(on["dirty"])
            back = L._set({"titleid": "g", "key": "REGION", "value": 1})  # JP -> US: back to disk
            self.assertFalse(back["dirty"])


class BindClearDirtyPrecise(unittest.TestCase):
    """lindbergh.bind / lindbergh.clear also return the PRECISE dirty flag now (both used to
    hardcode True)."""

    def _game(self):
        import tempfile
        gd = Path(tempfile.mkdtemp()) / "g.lindbergh"
        gd.mkdir()
        (gd / "g.lindbergh.commands").write_text("g.elf\n")
        (gd / "g.elf").write_text("x")
        (gd / "lindbergh.ini").write_text('[EVDEV]\nPLAYER_1_BUTTON_1 = "OLD"\n')
        return gd

    def test_bind_dirty_true_then_bind_back_to_disk_value_false(self):
        from unittest import mock
        gd = self._game()

        class _Proc:
            returncode = 0
            def __init__(s, argv, **k): pass
            def communicate(s, timeout=None): return (json.dumps({"token": "NEW", "name": "BTN_SOUTH"}), "")

        class _ProcBack(_Proc):
            def communicate(s, timeout=None): return (json.dumps({"token": "OLD", "name": "BTN_SOUTH"}), "")

        with mock.patch.object(L, "_gamedir", lambda t: gd), \
             mock.patch.object(L, "event", lambda *a, **k: None):
            L._load_buffer("g")
            with mock.patch.object(L.subprocess, "Popen", _Proc):
                res = L._bind({"titleid": "g", "key": "PLAYER_1_BUTTON_1", "label": "P1 B1"})
            self.assertTrue(res["dirty"])
            with mock.patch.object(L.subprocess, "Popen", _ProcBack):
                res2 = L._bind({"titleid": "g", "key": "PLAYER_1_BUTTON_1", "label": "P1 B1"})
            self.assertFalse(res2["dirty"])

    def test_clear_returns_dirty_key(self):
        from unittest import mock
        gd = self._game()
        with mock.patch.object(L, "_gamedir", lambda t: gd):
            L._load_buffer("g")
            res = L._clear_bind({"titleid": "g", "key": "PLAYER_1_BUTTON_1", "label": "P1 B1"})
        self.assertIn("dirty", res)      # previously missing from the return dict entirely
        self.assertTrue(res["dirty"])    # cleared "OLD" -> "" differs from the on-disk "OLD"


class HandheldInputEditor(unittest.TestCase):
    """lindbergh_hhinput.* -- the On-the-go per-game HANDHELD Deck-pad dropdown editor."""

    PROFILE = {"rows": [
        {"key": "PLAYER_1_BUTTON_1", "label": "Punch"}, {"key": "PLAYER_2_BUTTON_1", "label": "Punch"},
        {"key": "PLAYER_1_BUTTON_2", "label": "Kick"},  {"key": "PLAYER_2_BUTTON_2", "label": "Kick"},
    ]}

    def _game(self):
        import tempfile
        gd = Path(tempfile.mkdtemp()) / "vf5.lindbergh"
        gd.mkdir()
        (gd / "vf5.lindbergh.commands").write_text("g.elf\n")
        (gd / "g.elf").write_text("x")
        (gd / "lindbergh.ini").write_text('[EVDEV]\nPLAYER_1_BUTTON_1 = "OLD"\n')
        return gd

    def _ctx(self, gd):
        from unittest import mock
        return (mock.patch.object(L, "_gamedir", lambda t: gd),
                mock.patch.object(L, "_profile_of", lambda g: self.PROFILE))

    def _get(self, gd):
        a, b = self._ctx(gd)
        with a, b:
            return L._hhinput_get({"titleid": "vf5"})

    def test_get_lists_controls_at_default(self):
        rows = self._get(self._game())["groups"][0]["settings"]
        self.assertEqual([r["key"] for r in rows], ["BUTTON_1", "BUTTON_2"])   # slot-agnostic (2-human)
        b1 = next(r for r in rows if r["key"] == "BUTTON_1")
        self.assertEqual(b1["value"], 0)                                       # unset -> Default
        self.assertTrue(b1["options"][0].startswith("Default"))               # "Default (A)"

    def test_set_writes_handheld_slice_only(self):
        gd = self._game()
        bidx = 1 + L._DECK_EVDEV_CODES.index("BTN_EAST")                       # A -> B
        a, b = self._ctx(gd)
        with a, b:
            L._hhinput_set({"titleid": "vf5", "key": "BUTTON_1", "value": bidx})
        self.assertEqual(L.lindbergh_pads.load_handheld(gd), {"BUTTON_1": "BTN_EAST"})
        self.assertEqual(L.lindbergh_pads.load(gd).get("pads"), {})            # docked map untouched
        b1 = next(r for r in self._get(gd)["groups"][0]["settings"] if r["key"] == "BUTTON_1")
        self.assertEqual(b1["value"], bidx)                                    # reads back

    def test_default_and_equals_default_clear(self):
        gd = self._game()
        L.lindbergh_pads.save_handheld(gd, {"BUTTON_1": "BTN_EAST"})
        a, b = self._ctx(gd)
        with a, b:
            L._hhinput_set({"titleid": "vf5", "key": "BUTTON_1", "value": 0})  # Default -> clear
            self.assertEqual(L.lindbergh_pads.load_handheld(gd), {})
            aidx = 1 + L._DECK_EVDEV_CODES.index("BTN_SOUTH")                  # BUTTON_1's own default
            L._hhinput_set({"titleid": "vf5", "key": "BUTTON_1", "value": aidx})
            self.assertEqual(L.lindbergh_pads.load_handheld(gd), {})           # equals default -> sparse

    def test_set_rejects_unknown_control(self):
        gd = self._game()
        a, b = self._ctx(gd)
        with a, b, self.assertRaises(L.RpcError):
            L._hhinput_set({"titleid": "vf5", "key": "BUTTON_9", "value": 1})

    def test_games_filters_to_pad_eligible(self):
        from unittest import mock
        gd = self._game()
        rows = [{"titleid": "vf5", "name": "VF5", "stem": "vf5.lindbergh", "summary": "Per-game config"},
                {"titleid": "gun", "name": "Gun", "stem": "gun.lindbergh", "summary": "Per-game config"}]
        with mock.patch.object(L, "_games", lambda: rows), \
             mock.patch.object(L, "_pad_eligible", lambda t: t != "gun"), \
             mock.patch.object(L, "_gamedir", lambda t: gd):
            out = L._hhinput_games({})
        self.assertEqual([g["titleid"] for g in out["games"]], ["vf5"])        # gun game excluded
        self.assertEqual(out["games"][0]["summary"], "Deck defaults")          # no override yet

    RACING = {"rows": [{"key": "PLAYER_1_BUTTON_1", "label": "Start"},
                       {"key": "ANALOGUE_1", "label": "Wheel Axis"},
                       {"key": "ANALOGUE_2", "label": "Gas"}, {"key": "ANALOGUE_3", "label": "Brake"}]}

    def test_analog_group_for_racing_game(self):
        from unittest import mock
        gd = self._game()
        with mock.patch.object(L, "_gamedir", lambda t: gd), \
             mock.patch.object(L, "_profile_of", lambda g: self.RACING):
            groups = L._hhinput_get({"titleid": "id5"})["groups"]
        self.assertIn("Deck analog", [g["title"] for g in groups])
        ana = next(g for g in groups if g["title"] == "Deck analog")["settings"]
        self.assertEqual([r["key"] for r in ana], ["ANALOG_1", "ANALOG_2", "ANALOG_3"])
        wheel = ana[0]
        self.assertEqual(wheel["value"], 0)                                    # auto by default
        self.assertIn("L-stick X", wheel["options"][0])                        # "Default (L-stick X)"

    def test_no_analog_group_for_digital_game(self):
        from unittest import mock
        gd = self._game()
        with mock.patch.object(L, "_gamedir", lambda t: gd), \
             mock.patch.object(L, "_profile_of", lambda g: self.PROFILE):
            groups = L._hhinput_get({"titleid": "vf5"})["groups"]
        self.assertNotIn("Deck analog", [g["title"] for g in groups])

    def test_analog_set_roundtrip_and_clear(self):
        from unittest import mock
        gd = self._game()
        with mock.patch.object(L, "_gamedir", lambda t: gd), \
             mock.patch.object(L, "_profile_of", lambda g: self.RACING):
            rx = 1 + L._DECK_AXIS_CODES.index("ABS_RX")
            L._hhinput_set({"titleid": "id5", "key": "ANALOG_1", "value": rx})
            self.assertEqual(L.lindbergh_pads.load_handheld_analog(gd), {"ANALOG_1": "ABS_RX"})
            ax = 1 + L._DECK_AXIS_CODES.index("ABS_X")                         # ABS_X == the auto-default
            L._hhinput_set({"titleid": "id5", "key": "ANALOG_1", "value": ax})
            self.assertEqual(L.lindbergh_pads.load_handheld_analog(gd), {})    # equals auto -> cleared
            L._hhinput_set({"titleid": "id5", "key": "ANALOG_1", "value": rx})
            L._hhinput_set({"titleid": "id5", "key": "ANALOG_1", "value": 0})  # Default -> cleared
            self.assertEqual(L.lindbergh_pads.load_handheld_analog(gd), {})


class HandheldResEditor(unittest.TestCase):
    """lindbergh_hhres.* (per-game handheld resolution) + lindbergh_hhmenu.games (game-first picker)."""

    def _game(self, ini="[Display]\nWIDTH = 1920\nHEIGHT = 1080\nBOOST_RENDER_RES = true\n"):
        import tempfile
        gd = Path(tempfile.mkdtemp()) / "id5.lindbergh"
        gd.mkdir()
        (gd / "id5.lindbergh.commands").write_text("g.elf\n")
        (gd / "g.elf").write_text("x")
        (gd / "lindbergh.ini").write_text(ini)
        return gd

    def test_get_shows_res_and_boost_rungs(self):
        from unittest import mock
        gd = self._game()                                                    # docked 1920x1080
        with mock.patch.object(L, "_gamedir", lambda t: gd):
            rows = L._hhres_get({"titleid": "id5"})["groups"][0]["settings"]
        res = next(r for r in rows if r["key"] == "res")
        self.assertEqual(res["options"],                                     # 1080p offered (docked 1080)
                         ["Inherit (docked)", "1080p (1920x1080)", "720p (1280x720)", "540p (960x540)"])
        self.assertEqual(res["value"], 0)                                    # inherit by default
        self.assertTrue(any(r["key"] == "boost" for r in rows))             # ini has BOOST -> shown

    def test_1080p_hidden_when_docked_below(self):
        # "where possible": a game docked at 720p offers only 720p/540p, not 1080p.
        from unittest import mock
        gd = self._game(ini="[Display]\nWIDTH = 1280\nHEIGHT = 720\n")
        with mock.patch.object(L, "_gamedir", lambda t: gd):
            opts = L._hhres_get({"titleid": "id5"})["groups"][0]["settings"][0]["options"]
        self.assertEqual(opts, ["Inherit (docked)", "720p (1280x720)", "540p (960x540)"])

    def test_boost_row_hidden_when_key_absent(self):
        from unittest import mock
        gd = self._game(ini="[Display]\nWIDTH = 1920\nHEIGHT = 1080\n")      # no BOOST key
        with mock.patch.object(L, "_gamedir", lambda t: gd):
            rows = L._hhres_get({"titleid": "id5"})["groups"][0]["settings"]
        self.assertFalse(any(r["key"] == "boost" for r in rows))

    def test_set_roundtrip_and_inherit_clears(self):
        from unittest import mock
        gd = self._game()                                                    # rungs: 1080p, 720p, 540p
        with mock.patch.object(L, "_gamedir", lambda t: gd):
            L._hhres_set({"titleid": "id5", "key": "res", "value": 1})       # 1080p (first rung)
            self.assertEqual(L.lindbergh_pads.load_handheld_settings(gd), {"res": "1080"})
            L._hhres_set({"titleid": "id5", "key": "res", "value": 2})       # 720p
            self.assertEqual(L.lindbergh_pads.load_handheld_settings(gd), {"res": "720"})
            L._hhres_set({"titleid": "id5", "key": "boost", "value": 1})     # Off
            self.assertEqual(L.lindbergh_pads.load_handheld_settings(gd), {"res": "720", "boost": "off"})
            L._hhres_set({"titleid": "id5", "key": "res", "value": 0})       # Inherit -> clear res
            self.assertEqual(L.lindbergh_pads.load_handheld_settings(gd), {"boost": "off"})

    def test_menu_games_hides_input_for_gun_game(self):
        from unittest import mock
        gd = self._game()
        rows = [{"titleid": "id5", "name": "ID5", "stem": "id5.lindbergh", "summary": ""},
                {"titleid": "hotd4", "name": "HOTD4", "stem": "hotd4.lindbergh", "summary": ""}]
        with mock.patch.object(L, "_games", lambda: rows), \
             mock.patch.object(L, "_is_gun", lambda t: False), \
             mock.patch.object(L, "_pad_eligible", lambda t: t != "hotd4"), \
             mock.patch.object(L, "_gamedir", lambda t: gd):
            games = {g["titleid"]: g for g in L._hhmenu_games({})["games"]}
        self.assertEqual(games["id5"].get("hide"), None)                     # pad game: both leaves
        self.assertEqual(games["hotd4"].get("hide"), ["input"])             # profile-less: no input leaf

    def test_menu_games_drops_gun_titles(self):
        # A real lightgun title is useless handheld -> dropped from the picker ENTIRELY
        # (not merely input-hidden). Guards the on-the-go lightgun filter.
        from unittest import mock
        rows = [{"titleid": "id5", "name": "ID5", "stem": "id5.lindbergh", "summary": ""},
                {"titleid": "gun1", "name": "GUN1", "stem": "gun1.lindbergh", "summary": ""}]
        with mock.patch.object(L, "_games", lambda: rows), \
             mock.patch.object(L, "_is_gun", lambda t: t == "gun1"), \
             mock.patch.object(L, "_pad_eligible", lambda t: False):
            ids = [g["titleid"] for g in L._hhmenu_games({})["games"]]
        self.assertEqual(ids, ["id5"])                                       # gun1 dropped


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
            self.assertIn("BUTTON_1", pl["sections"]["buttons"])
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
        games = [{"titleid": "id5", "name": "ID5"}, {"titleid": "rambo", "name": "Rambo"},
                 {"titleid": "mahjong", "name": "MJ"}]
        profs = {"id5": {"rows": [{"key": "PLAYER_1_BUTTON_1", "label": "x"}]},
                 "rambo": {"gun": True, "rows": [{"key": "PLAYER_1_BUTTON_1"}]},
                 "mahjong": {"rows": None}}   # no usable profile -> not pad-eligible
        with mock.patch.object(L, "_games", lambda: games), \
             mock.patch.object(L, "_gamedir", lambda t: Path(f"/x/{t}")), \
             mock.patch.object(L, "_profile_of", lambda gd: profs.get(gd.name)):
            allg = [g["titleid"] for g in L._games_cmd({})["games"]]
            padg = [g["titleid"] for g in L._games_cmd({"pads": True})["games"]]
        self.assertEqual(allg, ["id5", "rambo", "mahjong"])
        self.assertEqual(padg, ["id5"])   # gun (rambo) + profileless (mahjong) excluded


# Single-driver profile (P1-only, like Harley): non-contiguous analog (wheel ch2, gas ch1, brake ch4).
_DRIVE_PROFILE = {"gun": False, "rows": [
    {"axis": False, "key": "PLAYER_1_BUTTON_3", "label": "Shift Up"},
    {"axis": False, "key": "PLAYER_1_BUTTON_UP", "label": "Menu Up"},
    {"axis": False, "key": "PLAYER_1_COIN", "label": "Coin"},
    {"axis": True, "key": "ANALOGUE_2", "label": "Wheel Axis"},
    {"axis": True, "key": "ANALOGUE_1", "label": "Gas"},
    {"axis": True, "key": "ANALOGUE_4", "label": "Brake"},
]}
# Single-driver, DUAL-SLOT (Initial D-style): the gear shifter sits on JVS PLAYER_2 of one driver.
_ID4_PROFILE = {"gun": False, "rows": [
    {"axis": False, "key": "PLAYER_1_BUTTON_1", "label": "View Change"},
    {"axis": False, "key": "PLAYER_1_BUTTON_UP", "label": "Menu Up"},
    {"axis": False, "key": "PLAYER_1_COIN", "label": "Coin"},
    {"axis": False, "key": "PLAYER_2_BUTTON_UP", "label": "Shift up"},
    {"axis": False, "key": "PLAYER_2_BUTTON_1", "label": "Gear 1"},
    {"axis": False, "key": "PLAYER_2_BUTTON_2", "label": "Gear 2"},
    {"axis": True, "key": "ANALOGUE_1", "label": "Wheel Axis"},
]}
# Symmetric 2-human versus profile (VF5-style): P1/P2 hold the SAME control set ("Player N" labels).
_VS_PROFILE = {"gun": False, "rows": [
    {"axis": False, "key": "PLAYER_1_BUTTON_1", "label": "Player 1 Punch"},
    {"axis": False, "key": "PLAYER_2_BUTTON_1", "label": "Player 2 Punch"},
    {"axis": False, "key": "PLAYER_1_BUTTON_UP", "label": "Player 1 Up"},
    {"axis": False, "key": "PLAYER_2_BUTTON_UP", "label": "Player 2 Up"},
    {"axis": False, "key": "PLAYER_1_COIN", "label": "Coin 1"},
    {"axis": False, "key": "PLAYER_2_COIN", "label": "Coin 2"},
]}
# 2-human analog (Hummer-style): a Player-2 wheel marks it 2-human even though digital is asymmetric.
_HUMMER_PROFILE = {"gun": False, "rows": [
    {"axis": False, "key": "PLAYER_1_BUTTON_DOWN", "label": "ViewChange"},
    {"axis": False, "key": "PLAYER_2_BUTTON_DOWN", "label": "Boost"},
    {"axis": True, "key": "ANALOGUE_1", "label": "Wheel Axis"},
    {"axis": True, "key": "ANALOGUE_2", "label": "Gas"},
    {"axis": True, "key": "ANALOGUE_3", "label": "Brake"},
    {"axis": True, "key": "ANALOGUE_5", "label": "Wheel Axis Player 2"},
    {"axis": True, "key": "ANALOGUE_6", "label": "Gas Player 2"},
    {"axis": True, "key": "ANALOGUE_7", "label": "Brake Player 2"},
]}


class TwoHumanDetection(unittest.TestCase):
    def test_symmetric_versus_is_two_human(self):
        self.assertTrue(L._two_human(_VS_PROFILE))

    def test_player2_analog_is_two_human(self):
        self.assertTrue(L._two_human(_HUMMER_PROFILE))   # asymmetric digital, but P2 wheel -> 2-human

    def test_single_driver_dual_slot_is_one_human(self):
        self.assertFalse(L._two_human(_ID4_PROFILE))     # gears on P2 of ONE driver
        self.assertFalse(L._two_human(_DRIVE_PROFILE))   # P1-only driver

    def test_no_profile_defaults_two_human(self):
        self.assertTrue(L._two_human(None))              # generic symmetric assumption


class AnalogFunctions(unittest.TestCase):
    def test_non_contiguous_p1(self):
        fns = L._analog_functions(_DRIVE_PROFILE)
        self.assertEqual([(f["fn"], f["label"], f["p1"], f["p2"]) for f in fns],
                         [("ANALOG_1", "Wheel Axis", 2, None),
                          ("ANALOG_2", "Gas", 1, None),
                          ("ANALOG_3", "Brake", 4, None)])

    def test_two_player_split_from_labels(self):
        fns = L._analog_functions(_HUMMER_PROFILE)
        self.assertEqual([(f["fn"], f["p1"], f["p2"]) for f in fns],
                         [("ANALOG_1", 1, 5), ("ANALOG_2", 2, 6), ("ANALOG_3", 3, 7)])

    def test_no_profile_is_empty(self):
        self.assertEqual(L._analog_functions(None), [])
        self.assertEqual(L._analog_functions({"rows": None}), [])


class PadRowsGrouping(unittest.TestCase):
    def test_two_human_slot_agnostic_keys_and_collapsed_labels(self):
        secs = L._pad_sections(_VS_PROFILE)               # symmetric versus -> slot-agnostic
        self.assertEqual(secs["buttons"], ["BUTTON_1"])
        self.assertEqual(secs["dpad"], ["BUTTON_UP"])
        self.assertEqual(secs["system"], ["COIN"])
        dig = dict(L._pad_digital(_VS_PROFILE))
        self.assertEqual(dig["BUTTON_1"], "Button 1 (Punch)")   # "Player 1/2" noise collapsed
        self.assertEqual(dig["COIN"], "Coin")                   # "Coin 1/2" -> "Coin"

    def test_single_driver_uses_real_player_keys(self):
        secs = L._pad_sections(_DRIVE_PROFILE)            # 1-human -> explicit PLAYER_<n> keys
        self.assertEqual(secs["buttons"], ["PLAYER_1_BUTTON_3"])
        self.assertEqual(secs["dpad"], ["PLAYER_1_BUTTON_UP"])
        self.assertEqual(secs["analog"], ["ANALOG_1", "ANALOG_2", "ANALOG_3"])
        self.assertEqual(secs["system"], ["PLAYER_1_COIN"])

    def test_dual_slot_gears_not_collapsed(self):
        # the gear shifter (PLAYER_2) must stay DISTINCT from PLAYER_1 controls, not merged
        secs = L._pad_sections(_ID4_PROFILE)
        self.assertEqual(secs["buttons"], ["PLAYER_1_BUTTON_1", "PLAYER_2_BUTTON_1", "PLAYER_2_BUTTON_2"])
        self.assertEqual(secs["dpad"], ["PLAYER_1_BUTTON_UP", "PLAYER_2_BUTTON_UP"])
        ctrls = dict(L._one_human_controls(_ID4_PROFILE))
        self.assertEqual(ctrls["PLAYER_1_BUTTON_1"], "View Change")
        self.assertEqual(ctrls["PLAYER_2_BUTTON_1"], "Gear 1")     # distinct, real function

    def test_rows_kind_and_axis(self):
        import tempfile
        gd = Path(tempfile.mkdtemp()) / "g.lindbergh"
        gd.mkdir()
        (gd / "g.lindbergh.commands").write_text("g.elf\n")
        (gd / "g.elf").write_text("x")
        (gd / "lindbergh.ini").write_text('[EVDEV]\nPLAYER_1_BUTTON_1 = ""\n')
        rows = L._pad_rows(gd, "XARC", _ID4_PROFILE)
        self.assertEqual(rows["PLAYER_1_BUTTON_1"]["kind"], "button")
        self.assertEqual(rows["PLAYER_2_BUTTON_UP"]["kind"], "direction")  # shifter dir works too
        self.assertEqual(rows["PLAYER_2_BUTTON_1"]["label"], "Gear 1")
        self.assertEqual(rows["ANALOG_1"]["kind"], "analog")
        self.assertTrue(rows["ANALOG_1"]["axis"])

    def test_no_profile_fallback_full_generic(self):
        dig = dict(L._pad_digital(None))
        self.assertEqual(dig["BUTTON_1"], "Button 1")        # generic label, no profile
        self.assertIn("BUTTON_8", dig)                       # full set shown
        self.assertEqual(L._pad_sections(None).get("analog"), None)  # no analog group


class CaptureDirection(unittest.TestCase):
    """--direction: a digital direction bound with the D-pad OR a centered-stick push (_MIN/_MAX)."""
    _cap = staticmethod(CaptureAnalogTrigger._capture)

    def test_centered_stick_invisible_in_button_mode(self):
        from evdev import ecodes
        # the whole reason --direction exists: a centered stick can't reach the button-mode guard
        self.assertEqual(self._cap(ecodes.ABS_Y, 0, rest=128), [])
        self.assertEqual(self._cap(ecodes.ABS_Y, 255, rest=128), [])

    def test_stick_push_min_max(self):
        from evdev import ecodes
        self.assertEqual(self._cap(ecodes.ABS_Y, 0, rest=128, direction=True),
                         ["XBOX_360_WIRELESS_RECEIVER_ABS_Y_MIN"])    # up
        self.assertEqual(self._cap(ecodes.ABS_Y, 255, rest=128, direction=True),
                         ["XBOX_360_WIRELESS_RECEIVER_ABS_Y_MAX"])    # down
        self.assertEqual(self._cap(ecodes.ABS_X, 255, rest=128, direction=True),
                         ["XBOX_360_WIRELESS_RECEIVER_ABS_X_MAX"])    # right

    def test_trigger_binds_as_bare_in_direction_mode(self):
        from evdev import ecodes
        # a gear-shift paddle / boost on a *_BUTTON_UP/DOWN key is often an LT/RT trigger; in direction
        # mode it must bind via the loader-safe BARE token (NOT a stuck _MAX, NOT a timeout)
        self.assertEqual(self._cap(ecodes.ABS_Z, 255, rest=0, direction=True),
                         ["XBOX_360_WIRELESS_RECEIVER_ABS_Z"])
        self.assertFalse(any(t.endswith("_MAX") for t in self._cap(ecodes.ABS_RZ, 255, rest=0, direction=True)))

    def test_dpad_hat_still_works_in_direction_mode(self):
        from evdev import ecodes
        self.assertEqual(self._cap(ecodes.ABS_HAT0Y, -1, mn=-1, mx=1, direction=True),
                         ["XBOX_360_WIRELESS_RECEIVER_ABS_HAT0Y_MIN"])


class PadBindCaptureMode(unittest.TestCase):
    """_pad_bind picks the capture mode from the control kind (server-side, authoritative)."""

    def _argv_for(self, control, profile=_DRIVE_PROFILE):
        import tempfile
        from unittest import mock
        gd = Path(tempfile.mkdtemp()) / "g.lindbergh"
        gd.mkdir()
        (gd / "g.lindbergh.commands").write_text("g.elf\n")
        (gd / "g.elf").write_text("x")
        (gd / "lindbergh.ini").write_text('[EVDEV]\nPLAYER_1_BUTTON_3 = ""\n')
        seen = {}
        name = {"ANALOG_1": "ABS_X", "PLAYER_1_BUTTON_UP": "ABS_Y_MIN",
                "PLAYER_1_BUTTON_3": "BTN_SOUTH", "BUTTON_1": "BTN_SOUTH"}[control]

        class _Proc:
            returncode = 0
            def __init__(s, argv, **k): seen["argv"] = argv
            def communicate(s, timeout=None):
                return (json.dumps({"token": f"XARC_{name}", "name": name, "device": "X"}), "")

        with mock.patch.object(L.subprocess, "Popen", _Proc), \
             mock.patch.object(L, "event", lambda *a, **k: None), \
             mock.patch.object(L.staterev, "bump", lambda *a, **k: None), \
             mock.patch.object(L, "_gamedir", lambda t: gd), \
             mock.patch.object(L, "_profile_of", lambda gd_: profile):
            res = L._pad_bind({"titleid": "g", "tag": "XARC", "control": control, "label": control})
        return seen["argv"], res, gd

    def test_button_no_flag(self):
        argv, res, _ = self._argv_for("PLAYER_1_BUTTON_3")
        self.assertNotIn("--axis", argv)
        self.assertNotIn("--direction", argv)
        self.assertFalse(res["warn"])

    def test_direction_flag_with_player_prefix(self):
        argv, _, _ = self._argv_for("PLAYER_1_BUTTON_UP")   # _control_kind handles the PLAYER_<n>_ prefix
        self.assertIn("--direction", argv)
        self.assertNotIn("--axis", argv)

    def test_analog_flag_and_layout_and_single_player_persisted(self):
        from lib import lindbergh_pads as P
        argv, res, gd = self._argv_for("ANALOG_1")
        self.assertIn("--axis", argv)
        data = P.load(gd)
        self.assertEqual(data["pads"]["XARC"]["ANALOG_1"], "ABS_X")
        # analog bind persists BOTH the fn->channel layout and the single_player shape for the launch CLI
        self.assertEqual([(a["fn"], a["p1"]) for a in data["analog"]],
                         [("ANALOG_1", 2), ("ANALOG_2", 1), ("ANALOG_3", 4)])
        self.assertTrue(data["single_player"])

    def test_two_human_game_marks_not_single_player(self):
        from lib import lindbergh_pads as P
        _, _, gd = self._argv_for("BUTTON_1", profile=_VS_PROFILE)
        self.assertFalse(P.load(gd)["single_player"])


class HealSidecar(unittest.TestCase):
    """_heal_sidecar backfills single_player + migrates legacy slot-agnostic keys on page entry, so a
    pre-rework sidecar for a single-driver game stops blanking the PLAYER_2 gear at launch."""

    def _game(self):
        import tempfile
        gd = Path(tempfile.mkdtemp()) / "g.lindbergh"
        gd.mkdir()
        (gd / "g.lindbergh.commands").write_text("g.elf\n")
        (gd / "g.elf").write_text("x")
        (gd / "lindbergh.ini").write_text('[EVDEV]\nPLAYER_1_BUTTON_1 = ""\n')
        return gd

    def test_migrates_legacy_single_human(self):
        from lib import lindbergh_pads as P
        gd = self._game()
        P.save(gd, {"priority": ["DS"], "pads": {"DS": {"BUTTON_1": "BTN_SOUTH", "ANALOG_1": "ABS_X"}}})
        self.assertNotIn("single_player", P.load(gd))     # legacy v1: no flag
        L._heal_sidecar(gd, _ID4_PROFILE)                 # 1-human game
        d = P.load(gd)
        self.assertTrue(d["single_player"])
        # slot-agnostic BUTTON_1 -> real PLAYER_1_BUTTON_1; analog key untouched
        self.assertEqual(d["pads"]["DS"], {"PLAYER_1_BUTTON_1": "BTN_SOUTH", "ANALOG_1": "ABS_X"})

    def test_two_human_keeps_slot_agnostic(self):
        from lib import lindbergh_pads as P
        gd = self._game()
        P.save(gd, {"priority": ["DS"], "pads": {"DS": {"BUTTON_1": "BTN_SOUTH"}}})
        L._heal_sidecar(gd, _VS_PROFILE)                  # 2-human game
        d = P.load(gd)
        self.assertFalse(d["single_player"])
        self.assertEqual(d["pads"]["DS"], {"BUTTON_1": "BTN_SOUTH"})   # not re-keyed

    def test_reverse_migrates_to_slot_agnostic(self):
        from lib import lindbergh_pads as P
        gd = self._game()
        # a single-human sidecar that the profile now classifies 2-human -> re-key back to slot-agnostic
        P.save(gd, {"single_player": True, "priority": ["DS"],
                    "pads": {"DS": {"PLAYER_1_BUTTON_1": "BTN_SOUTH", "PLAYER_2_BUTTON_1": "BTN_EAST"}}})
        L._heal_sidecar(gd, _VS_PROFILE)                  # now 2-human
        d = P.load(gd)
        self.assertFalse(d["single_player"])
        self.assertEqual(d["pads"]["DS"].get("BUTTON_1"), "BTN_SOUTH")
        self.assertNotIn("PLAYER_1_BUTTON_1", d["pads"]["DS"])
        self.assertNotIn("PLAYER_2_BUTTON_1", d["pads"]["DS"])   # orphan P2 gear key dropped

    def test_no_sidecar_is_noop(self):
        from lib import lindbergh_pads as P
        gd = self._game()
        L._heal_sidecar(gd, _ID4_PROFILE)
        self.assertEqual(P.load(gd), {})


class DockedPergameMenu(unittest.TestCase):
    """The docked Standalones Lindbergh SECTION BUILDER is GAME-FIRST (standing rule
    mad-pergame-game-first): _sections_for emits ONE settings_pergame_menu whose leaves are
    Settings / Controllers / Input mapping, all per-game. The SERVED tile is NOT single-section,
    though -- standalones.list appends the shared per-system X-Arcade warning to every arcade tile
    (lindbergh included), so Lindbergh opens a small [Per-game, X-Arcade warning] chooser rather
    than the game picker directly (test_served_tile_keeps_gamefirst_menu)."""

    def _menu(self):
        from lib.madsrv import standalones_cmds as SC
        entry = next(s for s in SC.STANDALONES if s.get("kind") == "lindbergh")
        menus = [s for s in SC._sections_for(entry) if s.get("kind") == "settings_pergame_menu"]
        self.assertEqual(len(menus), 1)   # the builder emits exactly one game-first menu
        return menus[0]

    def test_menu_is_gamefirst_with_three_leaves(self):
        menu = self._menu()
        self.assertEqual(menu["arg"], "lindbergh")
        leaves = [(c["kind"], c.get("arg")) for c in menu["sections"]]
        self.assertEqual(leaves, [("pergame_settings", "lindbergh"),
                                  ("pergame_lindbergh_pads", "lindbergh"),
                                  ("pergame_lindbergh_map", "lindbergh")])

    def test_controllers_leaf_has_hide_key(self):
        # The Controllers leaf carries the stable `key` the per-game hide list targets, so a
        # lightgun / profile-less game can drop it (test_hide_controllers_on_ineligible).
        menu = self._menu()
        ctrl = next(c for c in menu["sections"] if c["kind"] == "pergame_lindbergh_pads")
        self.assertEqual(ctrl.get("key"), "lindbergh_pads")

    @skip_on_ci  # depends on which lindbergh games are present on the live Deck
    def test_served_tile_keeps_gamefirst_menu(self):
        # The tile C++ actually receives = the standalones.list assembly = _sections_for + the
        # central per-system flag append (standalones_cmds.py:936-937). Lindbergh is an arcade
        # system, so the served sections carry the X-Arcade warning ALONGSIDE the game-first menu
        # -> the tile is a [Per-game, X-Arcade warning] chooser, not a single-section direct-open.
        # Assert the game-first menu survives assembly intact and the warning sibling is appended.
        from lib.madsrv import standalones_cmds as SC, policy_settings_cmds as PS
        entry = next(s for s in SC.STANDALONES if s.get("kind") == "lindbergh")
        syss = ["lindbergh"]
        served = SC._sections_for(entry, syss) + PS.tile_flag_sections(syss, entry["label"])
        menus = [s for s in served if s.get("kind") == "settings_pergame_menu"]
        self.assertEqual(len(menus), 1)                              # menu preserved through assembly
        self.assertEqual(menus[0]["arg"], "lindbergh")
        self.assertTrue(PS.tile_flag_sections(syss, entry["label"]))  # lindbergh IS an arcade tile
        self.assertGreaterEqual(len(served), 2)                       # warning rides alongside = chooser


class PergameHideList(unittest.TestCase):
    """_games() tags games where pads->players is inert (lightgun + profile-less) with a hide list
    so the game-first browser drops the Controllers leaf for exactly those games -- the same subset
    the old dedicated Controllers picker filtered to (pads:true)."""

    def test_hide_controllers_on_ineligible(self):
        import tempfile
        from unittest import mock
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for stem in ("drive", "rambo"):
                (root / f"{stem}.lindbergh" / "elf").mkdir(parents=True)
                (root / f"{stem}.lindbergh" / "elf" / "lindbergh.ini").write_text("[Display]\n")
            eligible = {"drive": True, "rambo": False}   # rambo = a gun game, not pad-eligible
            with mock.patch.object(L, "LINDBERGH_ROOT", root), \
                 mock.patch.object(L, "_ini_of", lambda p: p / "elf" / "lindbergh.ini"), \
                 mock.patch.object(L, "_game_names", lambda: {}), \
                 mock.patch.object(L, "_pad_eligible", lambda t: eligible[t]):
                games = {g["titleid"]: g for g in L._games()}
        self.assertNotIn("hide", games["drive"])                       # eligible -> Controllers shown
        self.assertEqual(games["rambo"]["hide"], ["lindbergh_pads"])   # ineligible -> Controllers hidden


if __name__ == "__main__":
    unittest.main()
