"""
Regenerate tests/golden/<backend>/<scenario>.txt from the CURRENT backend code.

Run from the launchers root:  python3 -m tests.capture_golden

Re-run ONLY when you intentionally change assignment behaviour (the xemu fix, or
a documented normalization) — then review `git diff tests/golden/` so every
changed byte is a conscious decision. For a pure refactor the goldens must NOT
move; test_golden.py enforces that.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from tests import scenarios
from tests._harness import run, BACKENDS

GOLD = Path(__file__).parent / "golden"


def main() -> None:
    for be in BACKENDS:
        d = GOLD / be
        d.mkdir(parents=True, exist_ok=True)
        for name, classes, pins in scenarios.SCENARIOS:
            with tempfile.TemporaryDirectory() as tmp:
                text = run(be, classes, pins, tmp)
            (d / f"{name}.txt").write_text(text, encoding="utf-8")
            print(f"  {be}/{name}")
    print("captured golden outputs")


if __name__ == "__main__":
    main()
