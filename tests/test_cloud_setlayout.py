"""Cloud bucket LAYOUT + the push/fetch rel symmetry (deck-cloud.sh).

The bucket is flat: games/ bios/ system/ controllers/ hold one undated set each (files directly),
esde/<ts>/ and emucfg/<ts>/ hold dated sets. Every non-game category namespaces its items with its
own name (a system item's rel IS "system/Emulation/tools/smb.conf"), so the set dir would otherwise
repeat it -> system/system/... . _remote_rel drops that leading component for the REMOTE path only.

THE INVARIANT THIS FILE EXISTS FOR: _push_set and _fetch_set must strip IDENTICALLY. An asymmetry is
invisible until someone actually restores - the upload succeeds, the set lists, and only the download
looks in a place nothing was ever stored. So these tests do the real ROUND TRIP through the shell
(push -> list -> fetch) against a local "remote" dir and assert both the remote layout AND that the
staging tree restore reads is keyed by the FULL rel.

Run:  python3 -m unittest tests.test_cloud_setlayout -v
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib import backup_manifest as bm  # noqa: E402

CLOUD = ROOT / "deck-cloud.sh"
BIN = Path.home() / "Emulation" / "tools" / "bin"
HAVE_RCLONE = (BIN / "rclone").exists()

# category -> (push subcmd, fetch subcmd, list subcmd, base-override env, set token, item rel)
CATEGORIES = [
    ("games", "push-games", "fetch-games", "list-games",
     "DECK_CLOUD_GAMES_BASE_OVERRIDE", "games", "roms/nes/smb.nes"),
    ("bios", "push-bios", "fetch-bios", "list-bios",
     "DECK_CLOUD_BIOS_BASE_OVERRIDE", "bios", "bios/psx/scph5501.bin"),
    ("system", "push-system", "fetch-system", "list-system",
     "DECK_CLOUD_SYSTEM_BASE_OVERRIDE", "system", "system/Emulation/tools/smb.conf"),
    ("controllers", "push-controllers", "fetch-controllers", "list-controllers",
     "DECK_CLOUD_CONTROLLERS_BASE_OVERRIDE", "controllers", "controllers/.config/pad.cfg"),
    # dated sets: the container dir is the category, the set is the timestamp
    ("esde", "push-esde", "fetch-esde", "list-esde",
     "DECK_CLOUD_ESDE_BASE_OVERRIDE", "20260731T010000", "esde/gamelists/nes/gamelist.xml"),
    ("emucfg", "push-emucfg", "fetch-emucfg", "list-emucfg",
     "DECK_CLOUD_EMUCFG_BASE_OVERRIDE", "20260731T010000", "emucfg/.config/retroarch/retroarch.cfg"),
]

# what the item's path under the set dir must be once the redundant prefix is dropped
EXPECTED_REMOTE_REL = {
    "games": "roms/nes/smb.nes",                       # no strip: game rels never start with "games"
    "bios": "psx/scph5501.bin",
    "system": "Emulation/tools/smb.conf",
    "controllers": ".config/pad.cfg",
    "esde": "gamelists/nes/gamelist.xml",
    "emucfg": ".config/retroarch/retroarch.cfg",
}

PAYLOAD = b"PAYLOAD-BYTES"


@unittest.skipUnless(HAVE_RCLONE, "needs the vendored rclone (Deck only)")
class SetLayoutRoundTrip(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp())
        self.remote = self.base / "bucket"
        self.remote.mkdir()
        self._env = {"DECK_CLOUD_RCLONE": str(BIN / "rclone"), "DECK_CLOUD_SKIP_CONNCHECK": "1",
                     "DECK_CLOUD_NO_NICE": "1", "DECK_CLOUD_STATE_DIR": str(self.base / "state")}
        self._saved = {k: os.environ.get(k) for k in self._env}
        os.environ.update(self._env)

    def tearDown(self):
        for k, v in self._saved.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        import shutil
        shutil.rmtree(self.base, ignore_errors=True)

    def _env_for(self, override_key, cat):
        env = dict(os.environ)
        # a DATED category keeps a container dir; a fixed one is the bucket root itself
        env[override_key] = str(self.remote / cat) if cat in ("esde", "emucfg") else str(self.remote)
        return env

    def _plan_dir(self, cat, rel, token):
        """A one-item plan dir (src + rel) exactly as the Python push RPC persists it."""
        src = self.base / "live" / cat / Path(rel).name
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_bytes(PAYLOAD)
        pd = self.base / f"plan-{cat}"
        pd.mkdir()
        m = bm.new_manifest("granular", created="20260731T010000")
        bm.add_item(m, category=cat, category_label=cat, system="grp", system_label="grp",
                    item=bm.make_item(id=rel, name=Path(rel).name, src=str(src), rel=rel,
                                      kind="file", size=len(PAYLOAD)))
        bm.write(m, bm.manifest_path(pd))
        (pd / "plan").write_bytes(str(src).encode() + b"\0" + rel.encode() + b"\0")
        return pd

    def _run(self, argv, env):
        r = subprocess.run([str(CLOUD), *argv], env=env, capture_output=True, text=True, timeout=180)
        return r

    def test_remote_layout_has_no_duplicated_category_dir(self):
        for cat, push, _fetch, _list, override, token, rel in CATEGORIES:
            with self.subTest(category=cat):
                env = self._env_for(override, cat)
                pd = self._plan_dir(cat, rel, token)
                r = self._run([push, token, str(pd)], env)
                self.assertEqual(r.returncode, 0, r.stderr)
                setdir = (self.remote / cat / token) if cat in ("esde", "emucfg") \
                    else (self.remote / token)
                want = setdir / EXPECTED_REMOTE_REL[cat]
                self.assertTrue(want.is_file(), f"{cat}: expected {want} (got {sorted(setdir.rglob('*'))})")
                self.assertEqual(want.read_bytes(), PAYLOAD)
                # where a strip actually happens, the doubled path must NOT exist (for games the rel
                # is stored verbatim, so rel IS the expected path - nothing to assert against).
                if EXPECTED_REMOTE_REL[cat] != rel:
                    self.assertFalse((setdir / rel).exists(),
                                     f"{cat}: the redundant {token}/{rel} path was written")
                self.assertTrue((setdir / "mad-manifest.json").is_file(), f"{cat}: manifest published")

    def test_fetch_round_trips_into_a_staging_tree_keyed_by_the_full_rel(self):
        """The download must find what the upload stored AND land it under the FULL rel - restore
        maps staged items by rel, so a stripped staging path would break the restore, not the fetch."""
        for cat, push, fetch, _list, override, token, rel in CATEGORIES:
            with self.subTest(category=cat):
                env = self._env_for(override, cat)
                pd = self._plan_dir(cat, rel, token)
                self.assertEqual(self._run([push, token, str(pd)], env).returncode, 0)
                staging = self.base / f"staging-{cat}"
                planfile = self.base / f"fetchplan-{cat}"
                planfile.write_bytes(rel.encode() + b"\0" + b"file\0")
                r = self._run([fetch, token, str(staging), str(planfile)], env)
                self.assertEqual(r.returncode, 0, r.stderr)
                staged = staging / rel
                self.assertTrue(staged.is_file(),
                                f"{cat}: expected {staged} (got {sorted(staging.rglob('*'))})")
                self.assertEqual(staged.read_bytes(), PAYLOAD, f"{cat}: content survived the round trip")
                self.assertTrue((staging / "mad-manifest.json").is_file(),
                                f"{cat}: the manifest makes staging a valid restore source")

    def test_fixed_categories_do_not_cross_list(self):
        """games/ bios/ system/ controllers/ share the bucket root as their base, so each list must
        probe only its OWN dir - enumerating the root would report every sibling as a set."""
        env = dict(os.environ)
        for key in ("DECK_CLOUD_GAMES_BASE_OVERRIDE", "DECK_CLOUD_BIOS_BASE_OVERRIDE",
                    "DECK_CLOUD_SYSTEM_BASE_OVERRIDE", "DECK_CLOUD_CONTROLLERS_BASE_OVERRIDE"):
            env[key] = str(self.remote)
        (self.remote / "library").mkdir()          # a sibling that is NOT a backup set
        (self.remote / "precious").mkdir()
        pd = self._plan_dir("system", "system/Emulation/tools/smb.conf", "system")
        self.assertEqual(self._run(["push-system", "system", str(pd)], env).returncode, 0)
        sys_rows = self._run(["list-system"], env).stdout.split()
        self.assertTrue(sys_rows and sys_rows[0] == "system", f"list-system: {sys_rows}")
        for other in ("list-games", "list-bios", "list-controllers"):
            self.assertEqual(self._run([other], env).stdout.strip(), "",
                             f"{other} must not see the system set (or library/precious)")


class RemoteRelHelper(unittest.TestCase):
    """_remote_rel in isolation - the one place the strip rule is expressed."""

    def _remote_rel(self, strip, rel):
        # deck-cloud.sh dispatches on "$1" at the end, so it cannot be sourced; extract just the
        # function body and source THAT (keeps this test on the real definition, not a copy).
        fn = subprocess.run(["sed", "-n", "/^_remote_rel(){/,/^}/p", str(CLOUD)],
                            capture_output=True, text=True, timeout=60).stdout
        self.assertIn("_remote_rel()", fn, "could not extract _remote_rel from deck-cloud.sh")
        r = subprocess.run(["bash", "-c", fn + '\n_remote_rel "$1" "$2"', "_", strip, rel],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def test_strip_rules(self):
        cases = [
            ("system", "system/Emulation/tools/smb.conf", "Emulation/tools/smb.conf"),
            ("bios", "bios/psx/scph5501.bin", "psx/scph5501.bin"),
            ("", "roms/nes/smb.nes", "roms/nes/smb.nes"),          # no strip requested
            ("games", "roms/nes/smb.nes", "roms/nes/smb.nes"),     # prefix does not match
            ("system", "systemwide/x.cfg", "systemwide/x.cfg"),    # component must match WHOLE, not prefix
            ("system", "system", "system"),                        # nothing left -> keep as-is
        ]
        for strip, rel, want in cases:
            with self.subTest(strip=strip, rel=rel):
                self.assertEqual(self._remote_rel(strip, rel), want)


if __name__ == "__main__":
    unittest.main()
