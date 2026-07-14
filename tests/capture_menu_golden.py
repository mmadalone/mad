"""
Regenerate tests/golden/menu/<case>.json from the CURRENT menu-tree builders.

Run from the launchers root:  python3 -m tests.capture_menu_golden

Re-run ONLY when you intentionally change the emitted menus (e.g. P1's ASCII
separators / canonical labels, or a new tile) — then review `git diff
tests/golden/menu/` so every changed byte is a conscious decision. For a pure
refactor the goldens must NOT move; tests/test_menu_golden.py enforces that.
"""
from __future__ import annotations

from pathlib import Path

from tests._menu_capture import enumerate_cases, serialize

GOLD = Path(__file__).parent / "golden" / "menu"


def main() -> None:
    GOLD.mkdir(parents=True, exist_ok=True)
    for case_id, tree in enumerate_cases():
        (GOLD / f"{case_id}.json").write_text(serialize(tree), encoding="utf-8")
        print(f"  menu/{case_id}")
    print("captured menu goldens")


if __name__ == "__main__":
    main()
