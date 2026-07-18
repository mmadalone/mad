"""MAD's Python support package.

STRUCTURAL TEST-STORE ISOLATION  (read tests/__init__.py first)
--------------------------------------------------------------
Several lib modules resolve a REAL data path AT IMPORT TIME, e.g.

    lib/openbor_maps.py:  _STORE = mad_paths.storage("openbor", "input-maps.json")

which on this rig is ~/Emulation/storage/openbor/input-maps.json — the
developer's LIVE library.

tests/__init__.py redirects $MAD_DATA_ROOT to a throwaway tree so the suite can
never touch it. But that package init only runs when the suite is discovered as a
package (`python3 -m unittest discover -s tests -t .`, the documented command).
Discover it WITHOUT `-t .`, or run a bare `python3 -m unittest`, and `tests/`
becomes the top-level dir: tests/__init__.py never runs, nothing redirects the
root, and the first writer scribbles seed markers into the live store. That
happened for real (2026-07-16 "Contra", 2026-07-17 twice). The safety was a
convention about how you type the command, not a guarantee — see the memory
`test-isolation-derive-dont-remember`.

`lib/__init__.py` IS the guarantee. It runs on the first `import lib.*`, which
precedes every writer's module-level path resolution in EVERY invocation — a test
cannot resolve a MAD path without importing lib first. So when the unittest
runner is the running program and no explicit $MAD_DATA_ROOT was given, redirect
the data root to a throwaway tree here: the same thing tests/__init__.py does,
from the one place that always runs, regardless of `-t .`. Belt and suspenders —
under `-t .` tests/__init__.py has already set the var and this is a no-op.

Deliberately narrow. It keys on the unittest RUNNER being the program (its
__main__ / argv[0]), NOT on `"unittest" in sys.modules`: a production tool that
imports unittest.mock anywhere in its dependency tree would trip that, and MAD
would then resolve its live data root to a temp dir on the Deck. An explicit
$MAD_DATA_ROOT (CI, or a deliberate fixture tree) always wins and is left
untouched. data_root() itself stays pure so tests/test_mad_paths can still probe
the production resolution logic.
"""
import atexit
import os
import shutil
import sys
import tempfile


def _running_the_test_suite() -> bool:
    """True only while a test runner is the program being run.

    Matches how the suite actually starts — `python -m unittest ...` (which runs
    unittest/__main__.py as __main__) and a test module executed directly — and
    never a production tool that merely imported unittest somewhere. `pytest` /
    `py.test` are covered too: not because they are used here (they are not — rule
    #6 keeps pip deps off SteamOS, and CI + the docs are all `python -m unittest`),
    but so the guard is keyed on "a test runner is running", not on one runner's
    spelling. No production entrypoint is named pytest / py.test / test_*.py."""
    for cand in (getattr(sys.modules.get("__main__"), "__file__", None),
                 sys.argv[0] if sys.argv else None):
        p = (cand or "").replace("\\", "/").rstrip("/")
        if not p:
            continue
        base = p.rsplit("/", 1)[-1]
        parent = p.rsplit("/", 2)[-2] if "/" in p else ""
        if base == "__main__.py" and parent == "unittest":   # python -m unittest ...
            return True
        if base in ("pytest", "py.test"):                     # pytest / python -m pytest
            return True
        if base.startswith("test_") and base.endswith(".py"):  # python tests/test_x.py
            return True
    return False


if _running_the_test_suite() and not os.environ.get("MAD_DATA_ROOT"):
    _TEST_DATA_ROOT = tempfile.mkdtemp(prefix="mad-test-data-lib-")
    os.environ["MAD_DATA_ROOT"] = _TEST_DATA_ROOT
    atexit.register(shutil.rmtree, _TEST_DATA_ROOT, True)
