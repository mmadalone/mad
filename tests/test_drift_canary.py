"""The two always-on drift canaries (audit 2026-08-12 phase 5).

CANARY 1, EmuDeck stub reversion: EmuDeck's own installEmuBI.sh / flushEmulatorLaunchers()
`rm -f` the deployed launcher and `cp` their template over it, straight into the launchers git
clone, on any emulator (re)install or update. MAD's patched stubs lose their lib resolver, their
emudeck-shim fallback and mad-paths.sh, and launches silently lose splash + router integration.
mad-drift-check.sh detects that by the `_MAD_LIB` marker (verified in both directions: every
patched stub has it, no template does) and restores from git after archiving to _TMP.

CANARY 2, an emulator binary changed: lib/emu_fingerprint compares a cheap whole-directory
fingerprint against a recorded one. The interesting property is that the FIRST run records
silently - without it a fresh install opens a dialog listing every emulator on the machine, which
is exactly how a warning gets trained into background noise.

Run:  python3 -m unittest tests.test_drift_canary -v
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from lib import emu_fingerprint as ef

ROOT = Path(__file__).resolve().parent.parent
DRIFT = ROOT / "mad-drift-check.sh"

MARKER = "_MAD_LIB"
PATCHED = f'#!/bin/bash\n{MARKER}="$(dirname "$0")/lib"\n. "$_MAD_LIB/mad-paths.sh"\nemulatorInit "X"\n'
TEMPLATE = '#!/bin/bash\n. "$HOME/.config/EmuDeck/backend/functions/all.sh"\nemulatorInit "X"\n'


# User-relative roots ONLY. lib.emu_fingerprint's default roots include the ABSOLUTE
# /var/lib/flatpak/app, so redirecting $HOME does not isolate a test from the real machine - it
# would fingerprint this Deck's actual flatpaks and pass for the wrong reason (caught exactly that
# way while writing these tests).
USER_ROOTS = (".local/share/flatpak/app",)


class Fingerprint(unittest.TestCase):
    """The cheap version token, and what counts as a change."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        (self.home / "Applications").mkdir()
        self.state = self.home / "emu-fingerprint.json"
        self.addCleanup(self.tmp.cleanup)

    def _fp(self, home=None):
        return ef.fingerprint(home or self.home, USER_ROOTS)

    def _appimage(self, name: str, body: bytes = b"x" * 100):
        p = self.home / "Applications" / name
        p.write_bytes(body)
        return p

    def test_appimages_are_fingerprinted_and_other_files_ignored(self):
        self._appimage("Ryujinx.AppImage")
        (self.home / "Applications" / "notes.txt").write_text("not an emulator")
        fp = self._fp()
        self.assertIn("appimage:Ryujinx.AppImage", fp)
        self.assertNotIn("appimage:notes.txt", fp)
        self.assertNotIn("notes.txt", str(list(fp)))

    def test_a_resized_appimage_is_a_change(self):
        p = self._appimage("Cemu.AppImage")
        before = self._fp()
        p.write_bytes(b"y" * 500)                     # an update: different size
        self.assertEqual(ef.changed(before, self._fp()), ["Cemu.AppImage"])

    def test_appearing_and_vanishing_both_count(self):
        self._appimage("Eden.AppImage")
        before = self._fp()
        self._appimage("Citron.AppImage")
        self.assertEqual(ef.changed(before, self._fp()), ["Citron.AppImage"])
        (self.home / "Applications" / "Eden.AppImage").unlink()
        self.assertEqual(sorted(ef.changed(before, self._fp())),
                         ["Citron.AppImage", "Eden.AppImage"])

    def test_flatpak_commit_comes_from_the_ostree_symlink(self):
        # <root>/<id>/current/active is a symlink whose target basename IS the deployed commit;
        # this is what `flatpak info --show-commit` prints, at 0 subprocesses instead of 21 ms.
        app = self.home / ".local/share/flatpak/app/org.libretro.RetroArch/current"
        app.mkdir(parents=True)
        os.symlink("../x86_64/stable/deadbeefcafe", app / "active")
        self.assertEqual(self._fp()["flatpak:org.libretro.RetroArch"], "deadbeefcafe")

    def test_missing_dirs_are_not_an_error(self):
        empty = Path(self.tmp.name) / "nothing-here"
        empty.mkdir()
        self.assertEqual(self._fp(empty), {})

    def test_the_real_machine_is_actually_readable(self):
        # Anti-vacuous guard: every other test here runs against a fabricated tree, so a
        # fingerprint() that silently returned {} on real paths would still pass them all. This
        # Deck has both AppImages and flatpaks installed.
        real = ef.fingerprint()
        self.assertTrue(any(k.startswith("appimage:") for k in real), real)
        self.assertTrue(any(k.startswith("flatpak:") for k in real), real)

    def test_mads_own_artefacts_are_not_emulators(self):
        # deck-fetch-esde.sh rewrites ES-DE-MAD.AppImage on EVERY in-app update, and ES-DE.AppImage
        # is the generated launch wrapper. Without this exclusion the single most frequent update
        # event on this machine opens a dialog headed "Emulators were updated", which is how a
        # warning gets trained into background noise.
        for name in ("ES-DE-MAD.AppImage", "ES-DE.AppImage", "EmuDeck.AppImage"):
            self._appimage(name)
        self._appimage("Ryujinx.AppImage")
        fp = self._fp()
        self.assertEqual(list(fp), ["appimage:Ryujinx.AppImage"])

    def test_a_known_non_emulator_flatpak_is_ignored(self):
        for app_id in ("com.spotify.Client", "org.libretro.RetroArch"):
            d = self.home / ".local/share/flatpak/app" / app_id / "current"
            d.mkdir(parents=True)
            os.symlink("../x86_64/stable/abc123", d / "active")
        self.assertEqual(list(self._fp()), ["flatpak:org.libretro.RetroArch"])


class FirstRunIsSilent(unittest.TestCase):
    """check() must record rather than report when there is nothing to compare against."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        (self.home / "Applications").mkdir()
        (self.home / "Applications" / "Ryujinx.AppImage").write_bytes(b"a" * 10)
        self.state = self.home / "emu-fingerprint.json"
        self.addCleanup(self.tmp.cleanup)

    def _check(self):
        return ef.check(self.state, self.home, USER_ROOTS)

    def test_first_run_records_and_reports_nothing(self):
        self.assertEqual(self._check(), [])
        self.assertTrue(self.state.is_file())
        self.assertIn("appimage:Ryujinx.AppImage", json.loads(self.state.read_text()))

    def test_second_run_reports_a_real_change_and_does_NOT_record_it(self):
        self._check()                                         # seed
        (self.home / "Applications" / "Ryujinx.AppImage").write_bytes(b"b" * 999)
        self.assertEqual(self._check(), ["Ryujinx.AppImage"])
        # Recording is the ACKNOWLEDGEMENT and belongs to the caller, after the owner has seen
        # the dialog. If check() recorded it here, cancelling the dialog would still silence it.
        self.assertEqual(self._check(), ["Ryujinx.AppImage"])
        ef.save(ef.fingerprint(self.home, USER_ROOTS), self.state)
        self.assertEqual(self._check(), [])

    def test_a_corrupt_state_file_re_records_silently(self):
        self.state.write_text("{not json")
        self.assertEqual(self._check(), [])
        self.assertIn("appimage:Ryujinx.AppImage", json.loads(self.state.read_text()))


class StubReversion(unittest.TestCase):
    """The real mad-drift-check.sh against a fake HOME holding a real git clone."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.mad = self.home / "Emulation/tools/launchers"
        self.mad.mkdir(parents=True)
        self.tmpl = self.home / ".config/EmuDeck/backend/tools/launchers"
        self.tmpl.mkdir(parents=True)
        for name in ("retroarch.sh", "cemu.sh"):
            (self.mad / name).write_text(PATCHED)
            (self.tmpl / name).write_text(TEMPLATE)
        (self.mad / "install.sh").write_text("#!/bin/bash\n# MAD-only, no template\n")
        self._git("init", "-q")
        self._git("add", "-A")
        self._git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "seed")
        self.addCleanup(self.tmp.cleanup)

    def _git(self, *args):
        subprocess.run(["git", "-C", str(self.mad), *args], check=True,
                       capture_output=True, text=True)

    def _run(self):
        env = dict(os.environ, HOME=str(self.home))
        env.pop("MAD_EMUDECK_TEMPLATES", None)
        return subprocess.run(["bash", str(DRIFT)], capture_output=True, text=True,
                              env=env, timeout=120)

    def test_a_reverted_stub_is_archived_and_restored(self):
        (self.mad / "retroarch.sh").write_text(TEMPLATE)          # EmuDeck clobbers it
        res = self._run()
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn(MARKER, (self.mad / "retroarch.sh").read_text(),
                      "the MAD version was not restored from git")
        arcs = list((self.home / "Downloads/_TMP").glob("*-emudeck-stub-revert"))
        self.assertEqual(len(arcs), 1, "rule 5: the overwritten bytes must be archived")
        self.assertEqual((arcs[0] / "retroarch.sh").read_text(), TEMPLATE)
        self.assertIn("checkout", (arcs[0] / "RECOVERY.txt").read_text())
        self.assertIn("retroarch.sh", res.stdout)

    def test_an_intact_tree_changes_nothing_and_says_nothing(self):
        res = self._run()
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(res.stdout.strip(), "")
        self.assertFalse((self.home / "Downloads/_TMP").exists())
        for name in ("retroarch.sh", "cemu.sh"):
            self.assertIn(MARKER, (self.mad / name).read_text())

    def test_a_hand_edited_stub_is_reported_but_never_overwritten(self):
        # No marker, but not byte-identical to the template either: that is somebody's work in
        # progress, not an EmuDeck reversion. Restoring over it would destroy it.
        hand = "#!/bin/bash\n# mid-edit, marker not added yet\necho hi\n"
        (self.mad / "cemu.sh").write_text(hand)
        res = self._run()
        self.assertEqual((self.mad / "cemu.sh").read_text(), hand)
        self.assertIn("left alone", res.stdout)
        self.assertFalse((self.home / "Downloads/_TMP").exists())

    def test_mad_only_scripts_are_never_candidates(self):
        # install.sh has no EmuDeck template and no _MAD_LIB marker. A check that scanned the
        # whole clone root instead of deriving from the template dir would flag it every launch.
        res = self._run()
        self.assertNotIn("install.sh", res.stdout)

    def test_no_emudeck_on_the_machine_is_a_clean_no_op(self):
        import shutil
        shutil.rmtree(self.tmp.name + "/.config")
        (self.mad / "retroarch.sh").write_text(TEMPLATE)
        res = self._run()
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(res.stdout.strip(), "")
        self.assertEqual((self.mad / "retroarch.sh").read_text(), TEMPLATE)

    def test_an_untracked_emudeck_drop_is_never_a_candidate(self):
        # THE regression this class exists for. EmuDeck ships templates for emulators MAD has
        # never patched (citron.sh, eden.sh, yuzu.sh, ...). Installing one drops the template into
        # the clone root: no marker, byte-identical to the template, UNTRACKED. Before the
        # tracked-only candidate test, that was classified as a reversion, failed to restore, and
        # raised a blocking dialog at every ES-DE start forever with a git command that errors.
        (self.tmpl / "citron.sh").write_text(TEMPLATE)
        (self.mad / "citron.sh").write_text(TEMPLATE)      # EmuDeck's own drop, never tracked
        res = self._run()
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(res.stdout.strip(), "", "an untracked EmuDeck drop must be silent")
        self.assertEqual((self.mad / "citron.sh").read_text(), TEMPLATE, "and left alone")
        self.assertFalse((self.home / "Downloads/_TMP").exists())

    def test_a_staged_reversion_is_reported_as_failed_not_as_repaired(self):
        # `git checkout -- <path>` restores from the INDEX, not from HEAD, and exits 0 even when it
        # writes nothing. If the reverted stub was ever staged, trusting that exit code makes the
        # canary claim a repair it did not perform, on every start, while the stub stays broken.
        (self.mad / "retroarch.sh").write_text(TEMPLATE)
        self._git("add", "retroarch.sh")                   # the reversion is now IN the index
        res = self._run()
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual((self.mad / "retroarch.sh").read_text(), TEMPLATE,
                         "nothing could restore it, and nothing should pretend otherwise")
        self.assertNotIn("restored from git", res.stdout)
        self.assertIn("could not restore", res.stdout)

    def test_the_archive_gates_the_restore(self):
        # Rule 5: if the overwritten bytes cannot be archived, the overwrite must not happen.
        (self.mad / "retroarch.sh").write_text(TEMPLATE)
        dl = self.home / "Downloads"
        dl.mkdir()
        dl.chmod(0o555)
        self.addCleanup(dl.chmod, 0o755)
        res = self._run()
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("cannot archive", res.stdout)
        self.assertEqual((self.mad / "retroarch.sh").read_text(), TEMPLATE,
                         "no archive means no overwrite")

    def test_a_symlinked_stub_is_skipped(self):
        # citra.sh and lime3ds.sh are tracked symlinks to azahar.sh. grep and cmp read THROUGH
        # them, so after EmuDeck clobbers azahar.sh they would each raise a second, confusing
        # warning about the same one clobber.
        (self.tmpl / "citra.sh").write_text(TEMPLATE)
        (self.mad / "citra.sh").symlink_to(self.mad / "retroarch.sh")
        self._git("add", "-A")
        self._git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "link")
        (self.mad / "retroarch.sh").write_text(TEMPLATE)   # clobber the symlink TARGET
        res = self._run()
        self.assertNotIn("citra.sh", res.stdout)
        self.assertIn("retroarch.sh", res.stdout)

    def test_an_absent_launchers_tree_exits_immediately(self):
        # The shape every existing fake-HOME test in the suite creates: no clone at all.
        with tempfile.TemporaryDirectory() as bare:
            res = subprocess.run(["bash", str(DRIFT)], capture_output=True, text=True,
                                 env=dict(os.environ, HOME=bare), timeout=60)
        self.assertEqual(res.returncode, 0)
        self.assertEqual(res.stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()
