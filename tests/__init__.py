"""Test-suite package init — STRUCTURAL STORE ISOLATION. Runs before any test
module (and therefore before any `lib.*`) is imported.

WHY THIS IS HERE AND NOT IN A setUp
-----------------------------------
Several lib modules resolve a real path AT IMPORT TIME:

    lib/openbor_maps.py:  _STORE = mad_paths.storage("openbor", "input-maps.json")

By the time a test could patch that, the real path is already baked into the
module global — so isolation depended on every author remembering to
`mock.patch.object(M, "_STORE", ...)` in every class that reaches a writer. That
is not isolation, it is a convention, and it failed for real: on 2026-07-16 a
fixture named "Contra" was left in the developer's live
~/Emulation/storage/openbor/input-maps.json.

The guard added afterwards SCANNED THIS PACKAGE'S SOURCE for classes calling
apply_map without a `_STORE` mock.patch line, which an adversarial review then
walked through three ways:
  * a patch built but never `.start()`ed reads as isolated (the line is there) and
    isolates nothing;
  * it REPORTS, it does not PREVENT — the polluting test has already run by the
    time the scan fails;
  * a module-level helper, a shared base class, or `openbor_cfg.main(["apply",…])`
    never matches the pattern at all.

So redirect the whole mutable data root ONCE, here, for every test. mad_paths
already reads $MAD_DATA_ROOT (and data_root() is cached, which is exactly why this
must happen before the first import, not in a fixture). A test that patches
_STORE itself still works — it patches over this and restores to it. Verified: the
full suite passes with the data root pointed at a throwaway tree, so nothing in it
legitimately needs the real one.

An explicit $MAD_DATA_ROOT from the caller is respected (CI, or a deliberate
fixture tree). $storagePath — which EmuDeck exports, and which data_root() honours
next — is deliberately OVERRIDDEN: on this rig it resolves to the real
~/Emulation, which is the leak.

NOTE: this only runs when the suite is imported as a package
(`python3 -m unittest …`, the documented way). Running a module's file directly
bypasses it, so StoreIsolation in test_openbor_cfg ASSERTS the redirect is in
effect rather than trusting it.
"""
import atexit
import os
import shutil
import tempfile

if not os.environ.get("MAD_DATA_ROOT"):
    _TEST_DATA_ROOT = tempfile.mkdtemp(prefix="mad-test-data-")
    os.environ["MAD_DATA_ROOT"] = _TEST_DATA_ROOT
    atexit.register(shutil.rmtree, _TEST_DATA_ROOT, True)
