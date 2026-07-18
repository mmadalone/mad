"""The store-isolation guard must hold for EVERY unittest invocation, not only the
documented `... discover -s tests -t .`.

StoreIsolation in test_openbor_cfg proves the redirect works IN-PROCESS (a writer
under the redirected root cannot reach the real store). It cannot prove the thing
that actually bit three times: that running the suite WITHOUT `-t .` — which makes
`tests/` the top-level dir, so tests/__init__.py never runs — still does not touch
the live store. Only a real subprocess in that exact invocation can show it, so
that is what this does.

The guard lives in lib/__init__.py (runs on the first `import lib.*`, before any
writer resolves a path, in every invocation). See the memory
`test-isolation-derive-dont-remember`.

Hermetic: spawns python against a THROWAWAY probe package and a sandbox
`storagePath`, so it is safe on CI and on the Deck. `storagePath` stands in for the
real EmuDeck root — the guard's job is to keep the suite off it even though it is
resolvable.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# A minimal test module: import the openbor store writer and write ONE seed
# marker. Where that marker lands is the whole question.
_PROBE = """\
import json, os, unittest
from pathlib import Path
from lib import openbor_maps as M

class Probe(unittest.TestCase):
    def test_writer(self):
        M.mark_seeded("ISOLATION_PROBE")
        Path(os.environ["LEAK_PROBE_OUT"]).write_text(json.dumps({
            "store": str(M._STORE),
            "seeded": M.is_seeded("ISOLATION_PROBE"),
        }))
"""


class InvocationIsolation(unittest.TestCase):
    def _run(self, workdir: Path, extra_env: dict):
        pkg = workdir / "t_probe"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")            # a naive package: NO redirect of its own
        (pkg / "test_probe.py").write_text(_PROBE)
        sandbox = workdir / "sandbox"
        (sandbox / "storage").mkdir(parents=True)
        out = workdir / "probe_out.json"

        env = dict(os.environ)
        env.pop("MAD_DATA_ROOT", None)                  # the dangerous state: no explicit root
        env["storagePath"] = str(sandbox / "storage")   # a resolvable "real" EmuDeck root
        env["PYTHONPATH"] = str(REPO_ROOT)
        env["LEAK_PROBE_OUT"] = str(out)
        env.update(extra_env)

        # NB: NO `-t .` — tests/__init__.py deliberately does not run here.
        r = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", str(pkg)],
            cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, timeout=120)
        sandbox_store = sandbox / "storage" / "openbor" / "input-maps.json"
        result = json.loads(out.read_text()) if out.exists() else None
        return r, result, sandbox_store

    def test_no_t_dot_does_not_reach_the_configured_root(self):
        with tempfile.TemporaryDirectory() as td:
            r, result, sandbox_store = self._run(Path(td), extra_env={})
            self.assertEqual(r.returncode, 0,
                             f"probe suite failed:\n{r.stdout}\n{r.stderr}")
            self.assertIsNotNone(result, "probe never ran / wrote no result")
            # Guard-the-guard: the writer really did write (else this proves nothing).
            self.assertTrue(result["seeded"], "the probe writer did not write at all")
            # The store was redirected away from the resolvable sandbox root...
            self.assertNotIn(str(sandbox_store.parent), result["store"],
                             "openbor store resolved INTO the configured storagePath: "
                             "the suite would write the live library without -t .")
            # ...and into a lib/__init__.py throwaway tree.
            self.assertIn("mad-test-data", result["store"],
                          f"store not redirected by the guard: {result['store']}")
            self.assertFalse(sandbox_store.exists(),
                             "a seed marker was written into the configured root")

    def test_positive_control_the_probe_can_reach_a_root(self):
        # Proves the check above is not vacuous: point MAD_DATA_ROOT AT the sandbox
        # and the very same probe DOES write the marker there. If this fails, the
        # probe/sandbox wiring is broken and the negative test means nothing.
        with tempfile.TemporaryDirectory() as td:
            sandbox_root = Path(td) / "explicit"
            r, result, sandbox_store = self._run(
                Path(td), extra_env={"MAD_DATA_ROOT": str(sandbox_root)})
            self.assertEqual(r.returncode, 0,
                             f"probe suite failed:\n{r.stdout}\n{r.stderr}")
            self.assertIsNotNone(result)
            self.assertTrue(result["seeded"])
            expect = sandbox_root / "storage" / "openbor" / "input-maps.json"
            self.assertEqual(Path(result["store"]), expect)
            self.assertTrue(expect.exists(),
                            "positive control did not write where MAD_DATA_ROOT pointed")


class RunnerDetection(unittest.TestCase):
    """Unit-test lib._running_the_test_suite() directly: it decides whether the
    redirect fires, so its True/False boundary is the whole safety contract. Table
    covers every runner shape and — the load-bearing half — the production
    entrypoints that must NOT match (or MAD would point its live data root at a
    temp dir on the Deck)."""

    def _detect(self, main_file, argv0):
        import lib
        saved_main = sys.modules.get("__main__")
        saved_argv = sys.argv

        class _Main:
            pass
        m = _Main()
        if main_file is not None:
            m.__file__ = main_file
        try:
            sys.modules["__main__"] = m
            sys.argv = [argv0]
            return lib._running_the_test_suite()
        finally:
            if saved_main is not None:
                sys.modules["__main__"] = saved_main
            else:
                sys.modules.pop("__main__", None)
            sys.argv = saved_argv

    def test_test_runners_are_detected(self):
        for main_file, argv0, why in [
            ("/usr/lib/python3.13/unittest/__main__.py",
             "/usr/lib/python3.13/unittest/__main__.py", "python -m unittest"),
            (None, "/usr/lib/python3.13/unittest/__main__.py", "-m unittest, no __file__"),
            ("/home/deck/Emulation/tools/launchers/tests/test_x.py",
             "/home/deck/Emulation/tools/launchers/tests/test_x.py", "direct test file"),
            ("/usr/bin/pytest", "/usr/bin/pytest", "pytest console script"),
            (None, "/usr/bin/py.test", "py.test console script"),
        ]:
            with self.subTest(why=why):
                self.assertTrue(self._detect(main_file, argv0), why)

    def test_production_entrypoints_are_not_detected(self):
        # If ANY of these matched, MAD would silently relocate its live data root
        # under that process.
        for main_file, argv0, why in [
            ("/home/deck/Emulation/tools/launchers/mad-backend.py",
             "/home/deck/Emulation/tools/launchers/mad-backend.py", "mad-backend"),
            ("/home/deck/Emulation/tools/launchers/router-config-gui.py",
             "/home/deck/Emulation/tools/launchers/router-config-gui.py", "gui"),
            ("/home/deck/Emulation/tools/launchers/controller-router.py",
             "/home/deck/Emulation/tools/launchers/controller-router.py", "router"),
            ("/home/deck/Emulation/tools/launchers/hooks/game-end.py",
             "/home/deck/Emulation/tools/launchers/hooks/game-end.py", "hook"),
            (None, "-c", "python -c"),
            (None, "", "interactive / no argv0"),
        ]:
            with self.subTest(why=why):
                self.assertFalse(self._detect(main_file, argv0), why)


if __name__ == "__main__":
    unittest.main()
