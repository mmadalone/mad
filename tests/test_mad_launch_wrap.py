"""Golden test: lib/mad_launch_wrap.transform() is BYTE-IDENTICAL to the old inline python
block it replaced (the 44-liner that was copy-pasted into install.sh + deck-post-update.sh),
and idempotent. The old block is reproduced here parameterised on the same W/S binder paths
the module derives from __file__. Run: python3 -m unittest tests.test_mad_launch_wrap -v
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from lib import mad_launch_wrap as new   # noqa: E402

W = str(ROOT / "mad-switch-launch.py")
S = str(ROOT / "mad-standalone-launch.py")


def _old(text):
    """The original inline block, verbatim (parameterised on W/S)."""
    def wrap(t, label, emu):
        pat = re.compile(r'(<command label="%s \(Standalone\)">)(?!%s)(.*?)(</command>)'
                         % (re.escape(label), re.escape(W)))
        return pat.sub(lambda m: f'{m.group(1)}{W} {emu} %ROM% -- {m.group(2)}{m.group(3)}', t)

    def rewrap(t, label, emu):
        pat = re.compile(r'(<command label="%s \(Standalone\)">)(?!\s*%s)(.*?)(</command>)'
                         % (re.escape(label), re.escape(S)), re.S)

        def sub(m):
            inner = m.group(2).strip()
            mm = re.match(r'\S*controller-router-wrap\.sh\s+\S+\s+%ROM%\s+"[^"]*"\s+"[^"]*"\s+--\s+(.*)',
                          inner, re.S)
            real = (mm.group(1) if mm else inner).strip()
            return f'{m.group(1)} {S} {emu} %ROM% -- {real} {m.group(3)}'
        return pat.sub(sub, t)

    def inject(t):
        if "<name>xbox</name>" in t:
            return t
        block = ('    <system>\n        <name>xbox</name>\n        <fullname>Microsoft Xbox</fullname>\n'
                 '        <path>%ROMPATH%/xbox</path>\n        <extension>.iso .ISO .xiso .XISO</extension>\n'
                 f'        <command label="xemu (Standalone)">{S} xemu %ROM% -- '
                 '%INJECT%=%BASENAME%.esprefix %EMULATOR_XEMU% -dvd_path %ROM%</command>\n'
                 '        <platform>xbox</platform>\n        <theme>xbox</theme>\n    </system>\n')
        return t.replace("</systemList>", block + "</systemList>", 1)

    t = wrap(wrap(text, "Ryujinx", "ryujinx"), "Eden", "eden")
    t = rewrap(t, "PCSX2", "pcsx2")
    t = inject(t)
    t = rewrap(t, "xemu", "xemu")
    t = rewrap(t, "RPCS3", "rpcs3")
    return t


FIX = '''<?xml version="1.0"?>
<systemList>
    <system><name>switch</name>
        <command label="Ryujinx (Standalone)">/usr/bin/ryujinx %ROM%</command>
        <command label="Eden (Standalone)">/usr/bin/eden %ROM%</command></system>
    <system><name>ps2</name>
        <command label="PCSX2 (Standalone)">/usr/bin/pcsx2 %ROM%</command></system>
    <system><name>ps3</name>
        <command label="RPCS3 (Standalone)">/x/controller-router-wrap.sh rpcs3 %ROM% "%BASENAME%" "PS3" -- /usr/bin/rpcs3 %ROM%</command></system>
</systemList>
'''


class LaunchWrap(unittest.TestCase):
    def test_byte_identical_to_old(self):
        self.assertEqual(new.transform(FIX), _old(FIX))

    def test_idempotent(self):
        once = new.transform(FIX)
        self.assertEqual(new.transform(once), once)

    def test_expected_wraps_present(self):
        n = new.transform(FIX)
        self.assertIn("mad-switch-launch.py ryujinx", n)
        self.assertIn("mad-switch-launch.py eden", n)
        self.assertIn("mad-standalone-launch.py pcsx2", n)
        self.assertIn("mad-standalone-launch.py rpcs3", n)
        self.assertIn("<name>xbox</name>", n)
        # the router-wrap.sh prefix was stripped from the rpcs3 command
        self.assertNotIn("controller-router-wrap.sh rpcs3", n)


if __name__ == "__main__":
    unittest.main()
