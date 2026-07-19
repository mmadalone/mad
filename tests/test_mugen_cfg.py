"""mugen_cfg - the canonical Ikemen GO joystick writer (merger launches).

CI-safe: runs against a temp config.ini fixture. Guards that a game hand-de-rotated
for the RAW X-Arcade is rewritten to the standard binding the twin needs, that only
the [Joystick_Pn] tokens change (byte-preserving), and that it is idempotent.
"""
import tempfile
import unittest
from pathlib import Path

from lib import mugen_cfg

# AvX-style: P1/P2 de-rotated for the raw X-Arcade (up=DP_L ...), plus other sections
# and the keyboard block that must NOT be touched.
_INI = """\
; header comment
[Video]
GameWidth               = 1280
[Keys_P1]
up       = UP
a        = z
[Joystick_P1]
Joystick = 0
GUID     = 030000005e040000a102000000010000
up       = DP_L
down     = DP_R
left     = DP_U
right    = DP_D
a        = A
b        = B
c        = LB
x        = X
y        = Y
z        = RB
start    = START
d        = LT
w        = RT
menu     = BACK
RumbleOn = 0
[Joystick_P2]
Joystick = 1
up       = DP_L
down     = DP_R
left     = DP_U
right    = DP_D
a        = A
b        = B
c        = LB
x        = X
y        = Y
z        = RB
start    = START
d        = LT
w        = RT
menu     = BACK
RumbleOn = 0
"""


class MugenCfg(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ini = Path(self.tmp.name) / "config.ini"
        self.ini.write_text(_INI)

    def tearDown(self):
        self.tmp.cleanup()

    def test_derotates_to_standard(self):
        self.assertEqual(mugen_cfg.apply(self.ini), "applied")
        from lib.madsrv import cfgutil
        t = self.ini.read_text()
        for sec in ("Joystick_P1", "Joystick_P2"):
            self.assertEqual(cfgutil.ini_read(t, sec, "up"), "DP_U")
            self.assertEqual(cfgutil.ini_read(t, sec, "down"), "DP_D")
            self.assertEqual(cfgutil.ini_read(t, sec, "left"), "DP_L")
            self.assertEqual(cfgutil.ini_read(t, sec, "right"), "DP_R")
            self.assertEqual(cfgutil.ini_read(t, sec, "c"), "RT")
            self.assertEqual(cfgutil.ini_read(t, sec, "d"), "LB")
            self.assertEqual(cfgutil.ini_read(t, sec, "w"), "LT")

    def test_byte_preserving_and_seat_index(self):
        before = self.ini.read_text().splitlines()
        mugen_cfg.apply(self.ini)
        after = self.ini.read_text().splitlines()
        self.assertEqual(len(before), len(after))
        # untouched lines: header, [Video], [Keys_P1] keyboard, GUID, a/b/x/y/z/start/menu/RumbleOn
        for i, (a, b) in enumerate(zip(before, after)):
            if a != b:
                self.assertRegex(a.strip().split("=")[0].strip(),
                                 r"^(up|down|left|right|c|d|w|Joystick)$")
        # keyboard block untouched
        from lib.madsrv import cfgutil
        t = self.ini.read_text()
        self.assertEqual(cfgutil.ini_read(t, "Keys_P1", "up"), "UP")
        self.assertEqual(cfgutil.ini_read(t, "Joystick_P1", "Joystick"), "0")
        self.assertEqual(cfgutil.ini_read(t, "Joystick_P2", "Joystick"), "1")

    def test_idempotent(self):
        self.assertEqual(mugen_cfg.apply(self.ini), "applied")
        self.assertEqual(mugen_cfg.apply(self.ini), "unchanged")

    def test_missing_file(self):
        self.assertEqual(mugen_cfg.apply(Path(self.tmp.name) / "nope.ini"),
                         "skip-no-config")


if __name__ == "__main__":
    unittest.main()
