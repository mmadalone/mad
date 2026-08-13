"""model2_cmds - EMULATOR.INI editor for ElSemi's Sega Model 2 Emulator.

model2_cmds.py is hand-rolled (no configparser, see its own module docstring
for why) and had zero tests before this file. It carries its own little
CRLF-preserving / one-time-.bak / atomic-write / EBUSY-guard / staterev-bump
stack, all pinned here against a temp EMULATOR.INI fixture (never a real
~/Emulation/roms/model2 install).

Every byte-level assertion reads the fixture and the file under test with
read_bytes() - never text mode - so the CRLF endings and the Wine Z:\\
backslash path are exercised exactly as the module sees them, not silently
normalized away by Python's universal-newline handling.

Run: python3 -m unittest tests.test_model2_cmds -v
"""
import tempfile
import unittest
from pathlib import Path

from lib import proc_guard
from lib import staterev as sr
from lib.madsrv import model2_cmds, rpc
from lib.madsrv.rpc import RpcError

# The fixture deliberately mixes every shape the module's key-regex has to
# survive: a leading comment block, inline ;comments (some column-aligned),
# GammaR with spaces around "=" AND a trailing comment, non-curated keys
# (FullMode, DrawCross, the [RomDirs] Wine Z:\ path) that must never be
# offered/writable, and RawDevP2 (curated but absent from this INI build).
_INI = """\
; ElSemi MODEL2 EMULATOR v1.1a
;  edit with care - inline comments matter
[Renderer]
FullMode=4
FullScreenWidth=1280
FullScreenHeight=800
Bilinear=1
Trilinear=0 ;can break Dead or Alive
FilterTilemaps=0
ForceManaged=0 ;Enable if crashes or blank screen
AutoMip=0
MeshTransparency=0 ;Needs PS3.0 GPU
FSAA=0
FakeGouraud=0
WideScreenWindow=1
DrawCross=1 ;managed by model-2-emulator.sh
GammaR = 1.0   ; red gamma, 1.0 = none
GammaG = 1.0
GammaB = 1.0

[Input]
XInput=0
UseRawInput=0 ;2 mice / lightguns
RawDevP1=0
HoldGears=0

[RomDirs]
Dir1=Z:\\home\\deck\\Emulation\\roms\\model2 ;backslashes must survive
"""

# Variant: a non-preset fullscreen resolution (must be inserted as options[0]).
_NONPRESET = (_INI.replace("FullScreenWidth=1280\n", "FullScreenWidth=1152\n")
                  .replace("FullScreenHeight=800\n", "FullScreenHeight=864\n"))

# Variant: FullScreenHeight line missing entirely (get must skip Resolution;
# set must ENOKEY without ever touching disk).
_NO_HEIGHT = _INI.replace("FullScreenHeight=800\n", "")

# Variant: an int-typed key written as a float string (must still coerce to int).
_FLOAT_RAWDEV = _INI.replace("RawDevP1=0\n", "RawDevP1=1.0\n")


class Model2Cmds(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.ini = self.root / "model2" / "EMULATOR.INI"
        self.ini.parent.mkdir(parents=True)
        self._write()

        self._orig_ini = model2_cmds.MODEL2_INI
        model2_cmds.MODEL2_INI = self.ini
        # Belt-and-braces: make sure the patch actually landed inside the temp
        # dir before any test can touch a real path.
        self.assertTrue(str(model2_cmds.MODEL2_INI).startswith(self.tmp.name))

        self.running = False
        self.guard_calls = []
        self._orig_running = proc_guard.emulator_running
        proc_guard.emulator_running = lambda name: (self.guard_calls.append(name), self.running)[1]

        self.bumps = []
        self._orig_bump = sr.bump
        sr.bump = lambda n: self.bumps.append(n)

    def tearDown(self):
        model2_cmds.MODEL2_INI = self._orig_ini
        proc_guard.emulator_running = self._orig_running
        sr.bump = self._orig_bump
        self.tmp.cleanup()

    # -- helpers ----------------------------------------------------------
    def _write(self, text=_INI):
        self.ini.write_bytes(text.replace("\n", "\r\n").encode())

    def _get(self):
        return rpc._METHODS["model2.get"][0]({})

    def _set(self, key, value):
        return rpc._METHODS["model2.set"][0]({"key": key, "value": value})

    def _rows(self):
        return {s["key"]: s for g in self._get()["groups"] for s in g["settings"]}

    def _bak(self):
        # Mirror model2_cmds._ensure_bak's own construction exactly.
        return self.ini.with_suffix(self.ini.suffix + ".bak")

    def _tmp_files(self):
        return list(self.ini.parent.glob("*.model2-tmp"))

    # -- registration -------------------------------------------------------
    def test_registered(self):
        self.assertIn("model2.get", rpc._METHODS)
        self.assertIn("model2.set", rpc._METHODS)

    # -- get: existence / shape / coercion -----------------------------------
    def test_get_exists_false_when_ini_absent(self):
        self.ini.unlink()
        res = self._get()
        self.assertEqual(res, {"exists": False, "path": str(self.ini), "groups": []})
        self.assertFalse(self._bak().exists())
        self.assertEqual(self._tmp_files(), [])

    def test_get_groups_shape_and_coercion(self):
        res = self._get()
        self.assertTrue(res["exists"])
        got_titles = [g["title"] for g in res["groups"]]
        want_titles = [g["title"] for g in model2_cmds.GROUPS]
        self.assertEqual(got_titles, want_titles)

        rows = self._rows()
        self.assertIs(rows["Bilinear"]["value"], True)
        self.assertEqual(rows["WideScreenWindow"]["value"], 1)
        self.assertIsInstance(rows["WideScreenWindow"]["value"], int)
        self.assertEqual(rows["GammaR"]["value"], 1.0)
        self.assertIsInstance(rows["GammaR"]["value"], float)
        self.assertEqual(rows["RawDevP1"]["value"], 0)
        self.assertIsInstance(rows["RawDevP1"]["value"], int)

        # min/max/step echoed where the descriptor declares them.
        self.assertEqual((rows["GammaR"]["min"], rows["GammaR"]["max"], rows["GammaR"]["step"]),
                         (0.5, 2.5, 0.1))
        self.assertEqual((rows["RawDevP1"]["min"], rows["RawDevP1"]["max"], rows["RawDevP1"]["step"]),
                         (0, 9, 1))

    def test_get_skips_absent_key(self):
        self.assertNotIn("RawDevP2", self._rows())

    def test_get_never_offers_uncurated_keys(self):
        rows = self._rows()
        for key in ("FullMode", "DrawCross", "Dir1"):
            self.assertNotIn(key, rows)

    def test_get_resolution_preset_options(self):
        res = self._rows()["Resolution"]
        self.assertEqual(res["value"], "1280x800")
        self.assertEqual(res["options"], model2_cmds.RESOLUTION_PRESETS)

    def test_get_resolution_nonpreset_inserted_first(self):
        self._write(_NONPRESET)
        res = self._rows()["Resolution"]
        self.assertEqual(res["value"], "1152x864")
        self.assertEqual(res["options"][0], "1152x864")

    def test_get_skips_resolution_when_height_missing(self):
        self._write(_NO_HEIGHT)
        rows = self._rows()
        self.assertNotIn("Resolution", rows)
        # the rest of the Display group is unaffected
        self.assertIn("WideScreenWindow", rows)
        self.assertIn("FSAA", rows)

    def test_get_int_tolerates_float_written_value(self):
        self._write(_FLOAT_RAWDEV)
        row = self._rows()["RawDevP1"]
        self.assertEqual(row["value"], 1)
        self.assertIsInstance(row["value"], int)

    # -- set: byte-preserving write -------------------------------------------
    def test_set_scalar_byte_identical_outside_edited_line(self):
        before = self.ini.read_bytes()
        self._set("GammaR", 1.4)
        after = self.ini.read_bytes()

        # No bare \n outside a \r\n pair anywhere in the rewritten file.
        self.assertNotIn(b"\n", after.replace(b"\r\n", b""))

        before_lines = before.split(b"\r\n")
        after_lines = after.split(b"\r\n")
        self.assertEqual(len(before_lines), len(after_lines))

        diffs = [(b1, a1) for b1, a1 in zip(before_lines, after_lines) if b1 != a1]
        self.assertEqual(len(diffs), 1)
        b1, a1 = diffs[0]
        self.assertTrue(b1.startswith(b"GammaR"))
        self.assertTrue(a1.startswith(b"GammaR = 1.4"))
        self.assertTrue(a1.endswith(b"; red gamma, 1.0 = none"))

        def _line(blob, prefix):
            return next(l for l in blob.split(b"\r\n") if l.startswith(prefix))
        self.assertEqual(_line(before, b"Dir1"), _line(after, b"Dir1"))
        self.assertEqual(_line(before, b"DrawCross"), _line(after, b"DrawCross"))

    def test_set_preserves_line_with_no_trailing_comment(self):
        # GammaG has spaces around "=" but no inline comment - the no-comment
        # branch of the key regex.
        self._set("GammaG", 1.6)
        self.assertIn(b"GammaG = 1.6\r\n", self.ini.read_bytes())

    def test_set_bool_and_enum(self):
        self._set("FSAA", True)
        self.assertIn(b"FSAA=1", self.ini.read_bytes())

        r = self._set("WideScreenWindow", 2)
        self.assertEqual(r["value"], 2)
        self.assertIn(b"WideScreenWindow=2", self.ini.read_bytes())

    def test_set_creates_bak_once_with_pristine_bytes(self):
        bak = self._bak()
        self.assertFalse(bak.exists())
        original = self.ini.read_bytes()

        self._set("GammaR", 1.4)
        self.assertEqual(bak.read_bytes(), original)

        self._set("FSAA", True)
        self.assertEqual(bak.read_bytes(), original)

    def test_set_leaves_no_tmp_file(self):
        self._set("GammaR", 1.4)
        self.assertEqual(self._tmp_files(), [])

    def test_set_bumps_staterev_config(self):
        self._set("GammaR", 1.4)
        self.assertEqual(self.bumps, ["config"])

    def test_set_same_value_still_writes_and_bumps(self):
        # Pins the documented no-short-circuit behavior: writing back the
        # unchanged current value still makes a .bak and still bumps staterev.
        bak = self._bak()
        self._set("GammaR", 1.0)
        self.assertTrue(bak.is_file())
        self.assertEqual(self.bumps, ["config"])

    # -- set: guard / error paths ---------------------------------------------
    def test_set_ebusy_when_running(self):
        self.running = True
        before = self.ini.read_bytes()
        with self.assertRaises(RpcError) as cm:
            self._set("GammaR", 1.4)
        self.assertEqual(cm.exception.code, "EBUSY")
        self.assertEqual(self.guard_calls, ["model2"])
        self.assertEqual(self.ini.read_bytes(), before)
        self.assertFalse(self._bak().exists())
        self.assertEqual(self.bumps, [])

    def test_set_enoent_when_ini_missing(self):
        self.ini.unlink()
        with self.assertRaises(RpcError) as cm:
            self._set("GammaR", 1.4)
        self.assertEqual(cm.exception.code, "ENOENT")

    def test_set_einval_uncurated_key(self):
        before = self.ini.read_bytes()
        for key in ("FullMode", "Wireframe"):
            with self.assertRaises(RpcError) as cm:
                self._set(key, "9")
            self.assertEqual(cm.exception.code, "EINVAL")
        self.assertEqual(self.ini.read_bytes(), before)

    def test_set_enokey_when_key_absent(self):
        # RawDevP2 is a curated key (in GROUPS) but absent from this fixture
        # INI - the raise happens before _ensure_bak, so no .bak/bump either.
        before = self.ini.read_bytes()
        with self.assertRaises(RpcError) as cm:
            self._set("RawDevP2", 3)
        self.assertEqual(cm.exception.code, "ENOKEY")
        self.assertEqual(self.ini.read_bytes(), before)
        self.assertFalse(self._bak().exists())
        self.assertEqual(self.bumps, [])

    # -- set: the synthetic "Resolution" key ----------------------------------
    def test_set_resolution_writes_both_lines(self):
        before = self.ini.read_bytes().split(b"\r\n")
        r = self._set("Resolution", "1024x768")
        self.assertEqual(r, {"key": "Resolution", "value": "1024x768"})

        after = self.ini.read_bytes().split(b"\r\n")
        diffs = [(b1, a1) for b1, a1 in zip(before, after) if b1 != a1]
        self.assertEqual(len(diffs), 2)
        changed = sorted(a1.split(b"=")[0] for _, a1 in diffs)
        self.assertEqual(changed, [b"FullScreenHeight", b"FullScreenWidth"])

        self.assertTrue(self._bak().is_file())
        self.assertEqual(self.bumps, ["config"])

    def test_set_resolution_tolerates_spaces_and_capital_x(self):
        r = self._set("Resolution", " 640 X 480 ")
        self.assertEqual(r["value"], "640x480")

    def test_set_resolution_bad_shape_einval(self):
        before = self.ini.read_bytes()
        for bad in ("banana", "1024x", "9x9"):
            with self.assertRaises(RpcError) as cm:
                self._set("Resolution", bad)
            self.assertEqual(cm.exception.code, "EINVAL")
        self.assertEqual(self.ini.read_bytes(), before)
        self.assertEqual(self.bumps, [])

    def test_set_resolution_enokey_when_height_missing(self):
        self._write(_NO_HEIGHT)
        before = self.ini.read_bytes()
        with self.assertRaises(RpcError) as cm:
            self._set("Resolution", "1024x768")
        self.assertEqual(cm.exception.code, "ENOKEY")
        # Proves raise-before-write ordering: FullScreenWidth was already
        # substituted in the in-memory text before the height lookup failed,
        # but nothing ever reached disk.
        self.assertEqual(self.ini.read_bytes(), before)

    # -- characterization: a known validation hole ----------------------------
    def test_set_fullscreenwidth_direct_is_unvalidated_characterization(self):
        # FullScreenWidth/Height are only added to EDITABLE_KEYS, not a GROUPS
        # item, so a direct "FullScreenWidth" set has no GROUPS item (_item_by_key
        # returns None) and therefore skips both the WxH pairing the synthetic
        # "Resolution" key enforces AND any numeric coercion (falls back to
        # plain str). This pins a real audit-finding gap in the module - it is
        # NOT the desired behavior, but this suite documents current behavior,
        # so do not "fix" it here.
        r = self._set("FullScreenWidth", "9999")
        self.assertEqual(r, {"key": "FullScreenWidth", "value": "9999"})
        self.assertIn(b"FullScreenWidth=9999", self.ini.read_bytes())


if __name__ == "__main__":
    unittest.main()
