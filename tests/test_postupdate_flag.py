"""deck-post-update.sh must clear the .post-update-pending offer flag on a SUCCESSFUL reapply (and only
then). Regression (live incident 2026-07-24): it wrote the OS-build marker on success but NOT the flag,
so once the marker matched the current BUILD_ID, esde-health-check.sh short-circuited (cur == prev ->
exit) and never reached its own flag-clear - the C++ "SteamOS update reset..." offer kept popping up
after a clean reapply + reboot.

This extracts the script's ACTUAL success-tail block and runs it with a stubbed check_missing, so the
test tracks the real code (no copied snippet to drift out of sync).

Run:  python3 -m unittest tests.test_postupdate_flag -v
"""
from __future__ import annotations

import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "deck-post-update.sh"


def _tail_block() -> str:
    text = SCRIPT.read_text(encoding="utf-8")
    m = re.search(r"^if check_missing >/dev/null 2>&1; then\n.*?^fi$", text, re.M | re.S)
    if not m:
        raise AssertionError("success-tail block not found in deck-post-update.sh (did it move?)")
    return m.group(0)


class FlagClearOnSuccess(unittest.TestCase):
    def _flag_after(self, check_rc: int) -> bool:
        """Run the real tail block with check_missing forced to check_rc; return whether the flag survives."""
        block = _tail_block()
        with tempfile.TemporaryDirectory() as d:
            flag = Path(d) / ".post-update-pending"
            flag.write_text("stale\n", encoding="utf-8")
            prog = (f'set -u\nL={d!r}\nexport MAD_POSTUPDATE_FLAG={str(flag)!r}\n'
                    f'check_missing() {{ return {check_rc}; }}\n{block}\n')
            subprocess.run(["bash", "-c", prog], check=True, timeout=30)
            return flag.exists()

    def test_all_present_clears_the_flag(self):
        self.assertFalse(self._flag_after(0), "a successful reapply must clear the offer flag")

    def test_still_missing_keeps_the_flag(self):
        self.assertTrue(self._flag_after(1),
                        "if something is still missing, keep the flag so the offer keeps nagging")


if __name__ == "__main__":
    unittest.main()
