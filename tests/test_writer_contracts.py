"""
Repo-wide writer-safety contract sweep (audit finding test-coverage-2).

Nothing here inspects behaviour at runtime; every check below is a STATIC sweep over
the AST of lib/*.py and lib/madsrv/*.py, enforcing four rules that the rest of the
suite has no single place to guard:

  R1  Every module under lib/ and lib/madsrv/ still IMPORTS cleanly (a stray
      syntax error, circular import, or module-level crash in a rarely-exercised
      file otherwise bit-rots silently until someone happens to load it).
  R2  No module writes a config-shaped file with a raw ``open(...)``/``write_text``/
      ``write_bytes`` outside the two real chokepoints (``fsutil.atomic_write_text``
      and ``cfgutil.atomic_write``) or a recognised tmp/backup/recovery/marker/cache
      helper - i.e. nobody re-invents a non-atomic writer next to the real ones.
  R3  Any writer that DOES stage a same-dir ``*tmp*`` file actually swaps it onto the
      target (``os.replace``/an ``atomic``/``_swap`` helper) rather than leaving an
      orphaned temp or a write with no atomic finish.
  R4  Every ``lib.madsrv`` RPC method whose name ends ``.set``/``.install``/
      ``.uninstall``/``.clear`` is reachable from an EBUSY guard (``proc_guard``,
      ``cfgutil.do_set``/``apply_set``, or ``abort_if_esde_running``) so a live
      emulator can't silently clobber an edit on exit, UNLESS the module is listed
      in R4_EXEMPT with a reason that the write is safe without one.

Philosophy: near-zero false positives - a sweep that cries wolf gets deleted. Every
exemption below is scoped to the exact (module, function) or module it excuses, and
every exemption must still match a real site (stale entries fail loud) - an entry
that stops matching anything is a bug in THIS file, not a favour to the codebase.

Run:  python3 -m unittest tests.test_writer_contracts -v
"""
from __future__ import annotations

import ast
import functools
import importlib
import pkgutil
import re
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# -- module trees, built once ----------------------------------------------------
def _build_trees() -> dict:
    trees = {}
    for sub, prefix in (("lib", "lib"), ("lib/madsrv", "lib.madsrv")):
        for f in sorted((ROOT / sub).glob("*.py")):
            if f.name == "__init__.py":
                continue
            dotted = f"{prefix}.{f.stem}"
            trees[dotted] = (f, ast.parse(f.read_text(encoding="utf-8"), filename=str(f)))
    return trees


_TREES = _build_trees()

# Any Name/Attribute/string-Constant in a write's TARGET expression matching this is
# considered a recognised non-config write (a tmp/backup/recovery/marker/cache file).
_TMPOK = re.compile(r"tmp|bak|backup|recovery|marker|cache", re.I)


# -- R1: modules that legitimately cannot be imported top-level in a test process --
IMPORT_SKIP = {
    "lib.hypseus_capture": "sys-path: mutates sys.path and imports hypinput as a top-level "
        "module at import; subprocess-only capture tool (daphne_cmds spawns it), never "
        "imported in-process",
}

# -- R2: (module, enclosing_function) sites that write a non-chokepoint file on purpose --
R2_EXEMPT = {
    ("lib.deck_power", "_write"):
        "sysfs: kernel attribute node - tmp/replace/.bak are meaningless on sysfs",
    ("lib.es_find_rules", "ensure_find_rules"):
        "FIXME: REAL FINDING (audit 2026-08-12) - non-atomic write of a live ES-DE config "
        "(custom_systems/es_find_rules.xml, no .bak, no tmp+replace). Route through "
        "fsutil.atomic_write_text in the phase-5 hygiene pass, then DELETE this entry",
    ("lib.granular_backup", "_samefs_snap_root"):
        "recovery: writes the fixed advisory header into a rule-5 snapshot dir's "
        "RECOVERY.txt (guarded by rec.exists(), written once) - a recovery artifact "
        "itself, not a config; parallels fsutil.recoverable_delete's own RECOVERY.txt",
    ("lib.job_registry", "__enter__"):
        "lock: open(jobs_dir()/'.lock', 'w') exists only to give fcntl.flock a file "
        "descriptor to lock - its contents are never written or read",
    ("lib.madsrv.backup_cmds", "_backup_set_dest"):
        "pref: one-line panel preference (remembered local-backup destination) under "
        "storage/; loss is cosmetic (falls back to DEFAULT_DEST)",
    ("lib.madsrv.backup_cmds", "_backup_set_format"):
        "pref: one-line panel preference (remembered backup archive format) under "
        "storage/; loss is cosmetic (falls back to the default format)",
    ("lib.madsrv.cloud_cmds", "_persist_games_plan_and_stream"):
        "job-output: writes a job-scoped plan file (NUL-delimited src/rel pairs) under "
        "the daemon's state dir, consumed once by the deck-cloud.sh subprocess this same "
        "call launches, then never read again - job input/output, not a persisted config "
        "(the manifest written alongside it goes through backup_manifest.write, its own "
        "tmp+replace chokepoint)",
    ("lib.madsrv.cloud_cmds", "dispatch_queue"):
        "job-output: writes an error message into a queued job's .out file so the "
        "Transfers tile can show why it failed - a job's status/output stream, not config",
    ("lib.mugen_res", "apply"):
        "sidecar: MAD-owned resting-resolution sidecar beside config.ini, rewritten "
        "wholesale per downshift; harmless if lost (a crash-sweep on the next apply() "
        "restores or re-derives it)",
}

# -- R3: functions whose tmp-named write is NOT followed by an atomic swap ---------
R3_EXEMPT: dict = {}

# -- R4: lib.madsrv modules that register a mutator RPC method with no EBUSY guard --
R4_EXEMPT = {
    "lib.madsrv.daphne_cmds":
        "no-clobber-window: daphne.clear only mutates the in-memory HypInput buffer; "
        "the disk write happens at daphne.save via hypinput.write_global/write_per_game, "
        "which already takes its own one-time .bak (hypinput._atomic_write), and "
        "hypinput.ini is NOT rewritten by Hypseus/ES-DE on exit (lib/hypinput.py:20 - "
        "'safe to edit anytime')",
    "lib.madsrv.lindbergh_cmds":
        "no-clobber-window: lindbergh.set/.clear only mutate an in-memory ini-text "
        "buffer (_buf); buffered save takes a one-time .bak; loader believed not to "
        "rewrite the ini on exit. lindbergh_hhinput.set/lindbergh_hhres.set write "
        "MAD-owned per-game handheld override JSON via lindbergh_pads.save_* (a store "
        "applied at launch, not the live lindbergh.ini)",
    "lib.madsrv.mugen_onthego_cmds":
        "store: mugen_hhres.set/mugen_hhres_pg.set write the handheld render-scale "
        "policy via localpolicy.dump (controller-policy.local.toml) - a MAD-owned store "
        "applied by lib/mugen_res.apply() at the NEXT launch, not a live MUGEN config",
    "lib.madsrv.onthego_cmds":
        "store: onthego_global.set/quit_handheld.set/daphne_handheld.set are policy-"
        "backed (localpolicy.dump, controller-policy.local.toml) per the module "
        "docstring - a MAD-owned store applied at the next launch, not a live emulator "
        "config that a running process could rewrite out from under it",
    "lib.madsrv.pcsx2_blacklist_cmds":
        "store: pcsx2blacklist.set writes device-visibility overrides via localpolicy.dump "
        "(controller-policy.local.toml); the module docstring is explicit that 'this module "
        "only READS devices + STORES the overrides' - the effective blacklist is applied "
        "at game launch by the ES-DE wrapper, not by a live PCSX2 process",
    "lib.madsrv.policy_cmds":
        "pref: splash.set writes one splash-image preference via mad_config.set_splash "
        "- a MAD-owned panel preference (which images/mode to show at boot), not a live "
        "emulator config any running process could clobber",
    "lib.madsrv.ra_profiles_cmds":
        "store: raprof.set only stages a key/value into an InputBuffer per the module "
        "docstring ('BUFFERED per-profile detail'); raprof.save is what flushes it, in "
        "ONE write, to controller-policy.local.toml via localpolicy.dump - a MAD-owned "
        "store, not RetroArch's own live config",
    "lib.madsrv.sidebar_cmds":
        "pref: sidebar.set writes FORCE_SHOW_*/FORCE_HIDE_* into install.conf via "
        "install_conf.set_value - a MAD panel-visibility preference, not a live "
        "emulator config",
    "lib.madsrv.sinden_cmds":
        "scripted: sinden.install runs sinden-install.sh as a detached, logged "
        "subprocess (the script owns its own file safety, per the module docstring "
        "'MAD only CALLS the sinden-*.sh scripts - it never absorbs their logic'); "
        "camera.set only mutates the in-memory _cam slider-value dict and live v4l2 "
        "controls (sinden_cfg.set_ctrl) - camera.save is the actual persist point",
}


# -- shared AST helpers -----------------------------------------------------------
def _str_const(node) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _enclosing_func(func_ranges: list, lineno: int) -> str:
    """The innermost (start, end, name) range (from a _scan_tree func_ranges list)
    covering ``lineno``, or "<module>" for module-level code."""
    best = None
    for start, end, name in func_ranges:
        if start <= lineno <= end:
            span = end - start
            if best is None or span < best[0]:
                best = (span, name)
    return best[1] if best else "<module>"


def _main_block_ranges(tree: ast.AST) -> list:
    """(start, end) line ranges of every top-level `if __name__ == "__main__":` block."""
    ranges = []
    for node in tree.body:
        if isinstance(node, ast.If) and isinstance(node.test, ast.Compare) \
                and isinstance(node.test.left, ast.Name) and node.test.left.id == "__name__":
            ranges.append((node.lineno, getattr(node, "end_lineno", node.lineno)))
    return ranges


def _classify_write_call(node: ast.Call):
    """If ``node`` is a raw file-open-for-write, return its TARGET expression (the
    path being written); else None. Covers ``x.write_text(...)``/``x.write_bytes(...)``,
    builtin ``open(x, "w"/"x"...)``, and ``x.open("w"/"x"...)`` (a Path.open call)."""
    func = node.func
    if isinstance(func, ast.Attribute) and func.attr in ("write_text", "write_bytes"):
        return func.value
    if isinstance(func, ast.Name) and func.id == "open":
        mode = _str_const(node.args[1]) if len(node.args) >= 2 else None
        if mode is None:
            mode = next((_str_const(kw.value) for kw in node.keywords
                        if kw.arg == "mode"), None)
        if mode and ("w" in mode or "x" in mode) and node.args:
            return node.args[0]
        return None
    if isinstance(func, ast.Attribute) and func.attr == "open":
        mode = _str_const(node.args[0]) if node.args else None
        if mode is None:
            mode = next((_str_const(kw.value) for kw in node.keywords
                        if kw.arg == "mode"), None)
        if mode and ("w" in mode or "x" in mode):
            return func.value
    return None


_TMP_ONLY = re.compile(r"tmp", re.I)
_REPLACE_RE = re.compile(r"atomic|_swap", re.I)


def _call_is_replace(call: ast.Call) -> bool:
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr == "replace" or bool(_REPLACE_RE.search(func.attr))
    if isinstance(func, ast.Name):
        return bool(_REPLACE_RE.search(func.id))
    return False


def _scan_tree(tree: ast.AST):
    """ONE ast.walk over ``tree``, collecting everything R2 and R3 need:
    write-calls (lineno, target_expr, call_node), the line numbers of every
    "replace"/"atomic"/"_swap" call, and every FunctionDef/AsyncFunctionDef's
    (start, end, name) range.

    Computed ONCE per module at import time (see _TREE_SCANS below) instead of
    per test method, and instead of the O(functions x subtree-size) trap of
    re-``ast.walk``-ing a fresh sub-node for every single FunctionDef (including
    every nested function's subtree, re-walked again by each of its ancestors) -
    on this repo's ~3000 functions that re-walk pattern alone cost ~2s of test
    time for no reason; a single pass costs a few hundred ms."""
    write_calls = []
    replace_linenos = []
    func_ranges = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            func_ranges.append((node.lineno, getattr(node, "end_lineno", node.lineno), node.name))
        elif isinstance(node, ast.Call):
            target = _classify_write_call(node)
            if target is not None:
                write_calls.append((node.lineno, target, node))
            if _call_is_replace(node):
                replace_linenos.append(node.lineno)
    return write_calls, replace_linenos, func_ranges


_TREE_SCANS = {dotted: _scan_tree(tree) for dotted, (_path, tree) in _TREES.items()}


def _target_matches(target: ast.AST, pattern: re.Pattern) -> bool:
    """True iff any Name id, Attribute attr, or string Constant anywhere inside the
    write-target expression subtree matches ``pattern``."""
    for n in ast.walk(target):
        if isinstance(n, ast.Name) and pattern.search(n.id):
            return True
        if isinstance(n, ast.Attribute) and pattern.search(n.attr):
            return True
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and pattern.search(n.value):
            return True
    return False


def _module_names() -> list:
    """Every importable dotted name under lib/ and lib/madsrv/, sorted."""
    names = set()
    for _, name, _ in pkgutil.iter_modules([str(ROOT / "lib")], prefix="lib."):
        names.add(name)
    for _, name, _ in pkgutil.iter_modules([str(ROOT / "lib" / "madsrv")], prefix="lib.madsrv."):
        names.add(name)
    return sorted(names)


def _import_all(skip_test) -> list:
    """Try importing every module under lib/ + lib/madsrv/ (skipping IMPORT_SKIP);
    returns a list of (name, repr(exc)) failures. Skips the whole sweep (via
    ``skip_test``, an unittest.TestCase.skipTest bound method) if evdev is absent,
    since most of lib/ depends on it and a bare CI runner has no hardware access."""
    try:
        import evdev  # noqa: F401
    except ImportError:
        skip_test("evdev not installed in this environment - skipping the import sweep")
    failures = []
    for name in _module_names():
        if name in IMPORT_SKIP:
            continue
        try:
            importlib.import_module(name)
        except Exception as exc:
            failures.append((name, repr(exc)))
    return failures


class ImportSweep(unittest.TestCase):
    """R1: every module under lib/ and lib/madsrv/ imports without raising."""

    def test_every_module_imports(self):
        failures = _import_all(self.skipTest)
        self.assertEqual(
            [], failures,
            "one or more modules failed to import - either fix the module or add a "
            "categorised IMPORT_SKIP entry with a real reason")

    def test_sentinel_registrations_present(self):
        """A handful of RPC methods, picked from ordinary modules AND from the f-string
        shared-engine shims (switch_dock / yuzu_hotkeys), must still be registered after
        the sweep - proves the import loop actually executed module bodies (decorators
        included), not just parsed them."""
        _import_all(self.skipTest)
        from lib.madsrv.rpc import _METHODS
        for sentinel in ("model2.get", "model2.set", "mugen.set",
                         "citron_hk.input_get", "eden_dock.set"):
            self.assertIn(sentinel, _METHODS,
                          f"{sentinel!r} missing from lib.madsrv.rpc._METHODS after the "
                          "import sweep - a registration regressed")

    def test_import_skip_not_stale(self):
        for dotted in IMPORT_SKIP:
            path = ROOT.joinpath(*dotted.split(".")).with_suffix(".py")
            self.assertTrue(
                path.is_file(),
                f"IMPORT_SKIP entry {dotted!r} has no file at {path} - delete the "
                "stale entry")


# -- R2 ----------------------------------------------------------------------------
@functools.lru_cache(maxsize=None)
def _scan_raw_writes():
    """Run ONCE (cached - shared by both RawWriteSweep tests, whichever runs first).
    Returns (violations, exempted) where violations is a list of formatted
    "file:line [func] CODE" strings and exempted is the set of R2_EXEMPT keys that
    excused at least one call this run."""
    violations = []
    exempted = set()
    for dotted, (path, tree) in _TREES.items():
        write_calls, _replace_linenos, func_ranges = _TREE_SCANS[dotted]
        main_ranges = _main_block_ranges(tree)
        for lineno, target, call in write_calls:
            if _target_matches(target, _TMPOK):
                continue
            if any(s <= lineno <= e for s, e in main_ranges):
                continue
            func = _enclosing_func(func_ranges, lineno)
            key = (dotted, func)
            if key in R2_EXEMPT:
                exempted.add(key)
                continue
            rel = path.relative_to(ROOT)
            violations.append(f"{rel}:{lineno} [{func}] {ast.unparse(call)}")
    return violations, exempted


class RawWriteSweep(unittest.TestCase):
    """R2: no module writes a config-shaped file outside the atomic chokepoints."""

    def test_no_raw_config_writes_outside_chokepoints(self):
        violations, _ = _scan_raw_writes()
        teach = (
            "\n\nEvery config write must go through one of:\n"
            "  1. fsutil.atomic_write_text / fsutil.atomic_write_json (lib/fsutil.py)\n"
            "  2. cfgutil.atomic_write (lib/madsrv/cfgutil.py)\n"
            "  3. a tmp/bak/backup/recovery/marker/cache-named target (matches _TMPOK)\n"
            "  4. a one-line R2_EXEMPT entry with a category token + a TRUE reason\n"
            "If this is a real unprotected config write, add a 'FIXME:' exemption and "
            "fix it in a follow-up, don't silently allow it.")
        self.assertEqual(
            [], violations,
            "raw (non-chokepoint) write(s) found:\n" + "\n".join(violations) + teach)

    def test_r2_exemptions_not_stale(self):
        _, exempted = _scan_raw_writes()
        for key in R2_EXEMPT:
            self.assertIn(
                key, exempted,
                f"R2_EXEMPT{key!r} excused nothing this run - stale exemption, delete it")


# -- R3 ----------------------------------------------------------------------------
class AtomicWriterShape(unittest.TestCase):
    """R3: a function that stages a same-dir *tmp* write must also swap it in."""

    def test_tmp_write_functions_call_replace(self):
        violations = []
        for dotted, (path, tree) in _TREES.items():
            write_calls, replace_linenos, func_ranges = _TREE_SCANS[dotted]
            tmp_writes = [c for c in write_calls if _target_matches(c[1], _TMP_ONLY)]
            if not tmp_writes or dotted in R3_EXEMPT:
                continue
            for start, end, name in func_ranges:
                owned = [c for c in tmp_writes if start <= c[0] <= end]
                if not owned:
                    continue
                has_swap = any(start <= ln <= end for ln in replace_linenos)
                if has_swap:
                    continue
                rel = path.relative_to(ROOT)
                codes = ", ".join(ast.unparse(c[2]) for c in owned)
                violations.append(
                    f"{rel}:{start} [{name}] writes a tmp-named file ({codes}) "
                    "but never calls .replace()/an atomic/_swap helper on it")
        self.assertEqual(
            [], violations,
            "tmp-named write(s) with no atomic finish:\n" + "\n".join(violations) +
            "\n\nEither call os.replace()/Path.replace() (or an *atomic*/*_swap* helper) "
            "on the tmp file, or add an R3_EXEMPT entry with a real reason.")


# -- R4 ----------------------------------------------------------------------------
_MUTATOR_SUFFIXES = (".set", ".install", ".uninstall", ".clear")
_GUARD_NAMES = {"proc_guard", "do_set", "apply_set", "abort_if_esde_running"}


@functools.lru_cache(maxsize=None)
def _mutators(tree: ast.AST) -> list:
    """[(method_name, function_name), ...] for every @method("...set"/...) decorator
    whose name argument is a literal string Constant (f-string names are invisible
    here by design - see BusyGuardReachability's docstring). Cached: called from
    both R4 test methods, once per module."""
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call) or not dec.args:
                continue
            fn = dec.func
            is_method_call = (isinstance(fn, ast.Name) and fn.id == "method") or \
                             (isinstance(fn, ast.Attribute) and fn.attr == "method")
            if not is_method_call:
                continue
            name = _str_const(dec.args[0])
            if name and name.endswith(_MUTATOR_SUFFIXES):
                found.append((name, node.name))
    return found


@functools.lru_cache(maxsize=None)
def _has_guard(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in _GUARD_NAMES:
            return True
        if isinstance(node, ast.Attribute) and node.attr in _GUARD_NAMES:
            return True
    return False


def _madsrv_trees():
    return {d: t for d, t in _TREES.items() if d.startswith("lib.madsrv.")}


class BusyGuardReachability(unittest.TestCase):
    """R4: every mutator RPC method in lib/madsrv is reachable from an EBUSY guard.

    KNOWN FALSE NEGATIVE: engines that register their methods via an f-string method
    name (``@method(f"{ns}.set")`` - switch_dock.register, yuzu_hotkeys/addons/cheats/
    pg_input's shared engines) are INVISIBLE to this scan, because the name argument is
    not a literal ast.Constant. The citron/eden/ryujinx *_dock/_addons/_cheats/_hotkeys/
    _pg_input shim modules are real writers but will never be flagged here - verified
    empirically (their guard, if any, lives in the shared engine module instead). This
    is a tripwire for NEW plain-@method modules, not a proof that every mutator in the
    tree is guarded. Scan granularity is also per-MODULE: one guarded method excuses
    every other mutator in the same file, even an unrelated unguarded one.
    """

    def test_busy_guard_or_exemption(self):
        violations = []
        for dotted, (path, tree) in _madsrv_trees().items():
            muts = _mutators(tree)
            if not muts:
                continue
            if dotted in R4_EXEMPT:
                continue
            if _has_guard(tree):
                continue
            rel = path.relative_to(ROOT)
            names = ", ".join(f"{m} ({fn})" for m, fn in muts)
            violations.append(
                f"{rel}: registers mutator(s) [{names}] with no reachable EBUSY guard")
        teach = (
            "\n\nAdd an EBUSY guard (proc_guard.emulator_running(...), like "
            "model2_cmds._model2_set, or route the write through cfgutil.do_set/"
            "apply_set), OR add an R4_EXEMPT entry explaining why this module writes a "
            "MAD-owned store applied at launch rather than a live emulator config.")
        self.assertEqual(
            [], violations,
            "mutator(s) with no busy guard:\n" + "\n".join(violations) + teach)

    def test_r4_exemptions_not_stale(self):
        trees = _madsrv_trees()
        for dotted, reason in R4_EXEMPT.items():
            self.assertIn(
                dotted, trees,
                f"R4_EXEMPT[{dotted!r}] is not a module under lib/madsrv/ - delete/"
                "re-audit the entry")
            tree = trees[dotted][1]
            self.assertTrue(
                _mutators(tree),
                f"R4_EXEMPT[{dotted!r}] registers no mutator method any more - "
                "delete/re-audit the entry")
            self.assertFalse(
                _has_guard(tree),
                f"R4_EXEMPT[{dotted!r}] already has a reachable guard - "
                "delete/re-audit the entry")


# -- fsutil bump contract (functional, not AST) -------------------------------------
class FsutilBumpContract(unittest.TestCase):
    """fsutil.atomic_write_bytes must NEVER bump staterev("config") (binary/media, not
    config - see fsutil.py's docstring at atomic_write_bytes); fsutil.atomic_write_text
    always must (it's the text/JSON config chokepoint)."""

    def test_atomic_write_bytes_never_bumps_config(self):
        from lib import fsutil, staterev
        recorded = []
        orig_bump = staterev.bump
        staterev.bump = lambda key: recorded.append(key)
        try:
            with tempfile.TemporaryDirectory() as td:
                tdir = Path(td)
                fsutil.atomic_write_bytes(tdir / "media.bin", b"x")
                self.assertEqual([], recorded,
                                 "atomic_write_bytes bumped staterev('config') - it must not")
                fsutil.atomic_write_text(tdir / "config.txt", "x")
                self.assertEqual(["config"], recorded,
                                 "atomic_write_text must bump staterev('config') exactly once")
        finally:
            staterev.bump = orig_bump


if __name__ == "__main__":
    unittest.main()
