"""
Golden-parity tests: every backend ``assign()`` must write byte-identical output
to the captured golden for each scenario. After the pad_assign refactor these
must all pass with the goldens UNCHANGED (proof of no behaviour change) — except
xemu's pin-collision scenarios, whose goldens are re-captured to the fixed
output and reviewed in the diff.

Run:  python3 -m unittest tests.test_golden -v
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests import scenarios
from tests._harness import run, BACKENDS

GOLD = Path(__file__).parent / "golden"


class GoldenParity(unittest.TestCase):
    pass


def _make(be, name, classes, pins):
    def test(self):
        gp = GOLD / be / f"{name}.txt"
        self.assertTrue(gp.exists(),
                        f"missing golden {gp} — run `python3 -m tests.capture_golden`")
        with tempfile.TemporaryDirectory() as tmp:
            got = run(be, classes, pins, tmp)
        self.assertMultiLineEqual(got, gp.read_text(encoding="utf-8"),
                                  f"{be}/{name} output drifted from golden")
    return test


for _be in BACKENDS:
    for _name, _classes, _pins in scenarios.SCENARIOS:
        setattr(GoldenParity, f"test_{_be}_{_name}",
                _make(_be, _name, _classes, _pins))


if __name__ == "__main__":
    unittest.main()
