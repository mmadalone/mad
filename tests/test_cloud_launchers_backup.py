"""Lean launchers backup + native staged restore + the wrapper apply-on-boot.

The launchers item (a git worktree = mmadalone/mad) is backed up THIN: only the files a fresh
install.sh clone would NOT recreate (untracked + git-ignored config + unpushed tracked edits), via
`rclone --files-from`, never the tracked code. A per-backup .mad-cloud-manifest.txt (config-only)
drives a STAGED restore that the launch wrapper applies on the next ES-DE boot.

These use a throwaway git repo (DECK_CLOUD_LAUNCHERS_DIR override) + real rclone to a local dest, so
they exercise the actual --files-from / -L / manifest / staging behavior end to end. Skipped on CI
where rclone / git are absent.

Run:  python3 -m unittest tests.test_cloud_launchers_backup -v
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLOUD = ROOT / "deck-cloud.sh"
APPLY = ROOT / "apply-staged-restore.sh"
BIN = Path.home() / "Emulation" / "tools" / "bin"
HAVE_RCLONE = (BIN / "rclone").exists()
HAVE_GIT = shutil.which("git") is not None


def _git(repo: Path, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                   capture_output=True, text=True,
                   env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                            GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"))


@unittest.skipUnless(HAVE_RCLONE and HAVE_GIT, "needs rclone + git (Deck-only)")
class LaunchersThinUpload(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp())
        self.home = self.base / "home"; self.home.mkdir()
        self.repo = self.home / "Emulation" / "tools" / "launchers"
        self.repo.mkdir(parents=True)
        self.dest = self.base / "dest"

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def _init_repo(self, remote=False):
        _git(self.repo, "init", "-q", "-b", "main")
        # a TRACKED code file (recoverable from GitHub) + a .gitignore.
        (self.repo / "deck-cloud.sh").write_text("# tracked code\n")
        (self.repo / ".gitignore").write_text("*.local.toml\nsinden.conf\n*.pyc\n__pycache__/\n*.log\n")
        _git(self.repo, "add", "-A"); _git(self.repo, "commit", "-qm", "init")
        if remote:
            bare = self.base / "remote.git"; bare.mkdir()
            _git(bare, "init", "-q", "--bare")
            _git(self.repo, "remote", "add", "origin", str(bare))
            _git(self.repo, "push", "-q", "-u", "origin", "main")

    def _push(self, launchers_dir=None):
        stub = self.base / "backup.sh"
        stub.write_text('#!/usr/bin/env bash\ncase "$*" in *--list-items*) '
                        'printf "%s\\n" "' + str(self.repo) + '";; esac\n')
        stub.chmod(0o755)
        env = dict(os.environ, HOME=str(self.home), DECK_CLOUD_SKIP_CONNCHECK="1",
                   DECK_CLOUD_NO_NICE="1", DECK_CLOUD_RCLONE=str(BIN / "rclone"),
                   DECK_CLOUD_BACKUP_SCRIPT=str(stub), DECK_CLOUD_STATE_DIR=str(self.base / "state"),
                   DECK_CLOUD_LAUNCHERS_DIR=str(launchers_dir or self.repo),
                   DECK_CLOUD_PRECIOUS_BASE_OVERRIDE=str(self.dest),
                   DECK_CLOUD_PRECIOUS_VERS_OVERRIDE=str(self.base / "vers"))
        p = subprocess.run([str(CLOUD), "push-precious", "--force"], env=env,
                           capture_output=True, text=True, timeout=120)
        return p, self.dest / "Emulation" / "tools" / "launchers"

    def test_zero_tracked_clean_leak_keeps_local_only(self):
        self._init_repo()
        (self.repo / "controller-policy.local.toml").write_text("POLICY")  # ignored config
        (self.repo / "notes.txt").write_text("NEW")                        # untracked new
        _, got = self._push()
        self.assertTrue((got / "controller-policy.local.toml").exists(), "ignored config uploaded")
        self.assertTrue((got / "notes.txt").exists(), "untracked file uploaded")
        self.assertFalse((got / "deck-cloud.sh").exists(),
                         "a TRACKED-CLEAN file (on GitHub) must NOT upload")
        self.assertFalse((got / ".gitignore").exists(), "tracked .gitignore must NOT upload")

    def test_symlink_config_survives(self):
        # regression for the '19 listed / 18 copied' -L bug: a local-only symlink must land.
        # (Uses a neutral dir - es-de/ is now dropped as an EmuDeck shim, see the test below.)
        self._init_repo()
        (self.repo / "cfg").mkdir()
        (self.repo / "cfg" / "tool.sh").write_text("shim")   # untracked target
        (self.repo / "link.sh").symlink_to("cfg/tool.sh")    # untracked symlink
        _, got = self._push()
        self.assertTrue((got / "link.sh").exists(), "the symlinked config must be backed up (-L)")

    def test_reacquirable_dirs_excluded(self):
        # es-de/ esde/ srm/ = EmuDeck-generated launcher shims; review-findings/ = the assistant's
        # adversarial-review scratch. All re-acquirable, so the lean backup drops them while keeping
        # real local config.
        self._init_repo()
        (self.repo / "controller-policy.local.toml").write_text("P")   # a real kept file
        (self.repo / "openbor-metadata.json").write_text("{}")          # owner catalog - kept
        for d, f in (("es-de", "es-de.sh"), ("esde", "emulationstationde.sh"),
                     ("srm", "steamrommanager.sh"), ("review-findings", "wave1-raw.json")):
            (self.repo / d).mkdir()
            (self.repo / d / f).write_text("x")
        _, got = self._push()
        self.assertTrue((got / "controller-policy.local.toml").exists(), "real config still kept")
        self.assertTrue((got / "openbor-metadata.json").exists(), "owner catalog still kept")
        for d in ("es-de", "esde", "srm", "review-findings"):
            self.assertFalse((got / d).exists(), f"{d}/ must be dropped as re-acquirable")

    def test_manifest_is_config_only(self):
        self._init_repo()
        (self.repo / "sinden.conf").write_text("SIND")     # ignored config
        _, got = self._push()
        man = (got / ".mad-cloud-manifest.txt")
        self.assertTrue(man.exists(), "manifest must be written beside the backup")
        lines = [l for l in man.read_text().splitlines() if l.strip()]
        self.assertIn("sinden.conf", lines)
        self.assertNotIn("deck-cloud.sh", lines, "manifest is CONFIG only, never tracked code")

    def test_debris_excluded_owner_data_kept(self):
        self._init_repo()
        (self.repo / "controller-policy.local.toml").write_text("P")   # kept
        (self.repo / "openbor-metadata.json").write_text("{}")          # owner data (untracked) kept
        (self.repo / "debug.log").write_text("L")                       # debris (ignored)
        (self.repo / "__pycache__").mkdir(); (self.repo / "__pycache__" / "x.pyc").write_text("B")
        _, got = self._push()
        self.assertTrue((got / "controller-policy.local.toml").exists())
        self.assertTrue((got / "openbor-metadata.json").exists(), "owner data must be kept")
        self.assertFalse((got / "debug.log").exists(), "*.log debris must be dropped")
        self.assertFalse((got / "__pycache__").exists(), "__pycache__ debris must be dropped")

    def test_clean_empty_is_a_noop_not_a_fallback(self):
        # everything tracked + clean, nothing untracked/ignored -> empty set -> no upload, rc 0.
        self._init_repo()
        p, got = self._push()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("no local-only files", p.stdout + p.stderr)
        self.assertFalse(got.exists(), "nothing should be uploaded for a clean repo")

    def test_nonrepo_falls_back_to_whole_dir(self):
        # DECK_CLOUD_LAUNCHERS_DIR points at the repo, but we hand the helper a NON-git dir by
        # de-initing: the branch must fall back to the whole-dir copy (never a silent skip).
        self._init_repo()
        (self.repo / "controller-policy.local.toml").write_text("P")
        shutil.rmtree(self.repo / ".git")     # no longer a worktree
        p, got = self._push()
        self.assertIn("fallback", p.stdout + p.stderr, "must announce the whole-dir fallback")
        self.assertTrue((got / "controller-policy.local.toml").exists(), "config still backed up")
        self.assertTrue((got / "deck-cloud.sh").exists(),
                        "whole-dir fallback keeps everything (git can't tell what's tracked)")

    def test_diverged_unpushed_tracked_rides_but_not_in_manifest(self):
        self._init_repo(remote=True)
        (self.repo / "deck-cloud.sh").write_text("# LOCAL EDIT not pushed\n")  # tracked, diverged
        (self.repo / "sinden.conf").write_text("S")                            # ignored config
        p, got = self._push()
        self.assertIn("UNPUSHED", p.stdout + p.stderr, "diverged edit logs a PUSH warning")
        self.assertTrue((got / "deck-cloud.sh").exists(), "the unpushed EDIT is backed up")
        man = [l for l in (got / ".mad-cloud-manifest.txt").read_text().splitlines() if l.strip()]
        self.assertNotIn("deck-cloud.sh", man, "diverged code is NOT in the restore manifest")
        self.assertIn("sinden.conf", man)


@unittest.skipUnless(HAVE_RCLONE, "needs rclone (Deck-only)")
class LaunchersStagedRestore(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp())
        self.home = self.base / "home"; (self.home / "Downloads").mkdir(parents=True)
        self.state = self.home / ".config" / "deck-cloud"
        self.prec = self.base / "prec"
        self.lp = self.prec / "Emulation" / "tools" / "launchers"; self.lp.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def _restore(self):
        env = dict(os.environ, HOME=str(self.home), DECK_CLOUD_RCLONE=str(BIN / "rclone"),
                   DECK_CLOUD_STATE_DIR=str(self.state), DECK_CLOUD_SKIP_CONNCHECK="1",
                   DECK_CLOUD_NO_NICE="1", DECK_CLOUD_PRECIOUS_BASE_OVERRIDE=str(self.prec))
        return subprocess.run([str(CLOUD), "restore-precious", "--to-live"], env=env,
                              capture_output=True, text=True, timeout=90)

    def _staged(self):
        g = list((self.home / "Downloads").glob("_TMP/cloud-restore-*/_staged-apply"))
        return g[0] if g else None

    def test_stages_config_never_stale_code(self):
        # precious holds config + manifest AND a STALE tracked file (a leftover whole-tree push).
        # Restore must stage ONLY the manifest config, never the code, and arm the marker.
        (self.lp / "controller-policy.local.toml").write_text("CFG")
        (self.lp / "deck-cloud.sh").write_text("STALE-CODE")
        (self.lp / ".mad-cloud-manifest.txt").write_text("controller-policy.local.toml\n")
        p = self._restore()
        self.assertEqual(p.returncode, 0, p.stderr)
        staged = self._staged()
        self.assertIsNotNone(staged, "a _staged-apply tree must exist")
        lsp = staged / "Emulation" / "tools" / "launchers"
        self.assertEqual((lsp / "controller-policy.local.toml").read_text(), "CFG", "config staged")
        self.assertFalse((lsp / "deck-cloud.sh").exists(), "stale tracked code must NOT be staged")
        # the LIVE launchers tree is never materialized by the restore.
        self.assertFalse((self.home / "Emulation" / "tools" / "launchers" / "deck-cloud.sh").exists(),
                         "restore must not write live launchers code")
        marker = self.state / "pending-restore-apply"
        self.assertTrue(marker.exists() and marker.read_text().strip() == str(staged),
                        "marker armed at the staged tree")

    def test_manifest_absent_uses_pinned_allowlist(self):
        # an OLD backup with no manifest: restore falls back to the pinned allowlist and still
        # stages the known config while never staging code.
        (self.lp / "controller-policy.local.toml").write_text("CFG")
        (self.lp / "sinden.conf").write_text("SND")
        (self.lp / "deck-cloud.sh").write_text("STALE-CODE")   # not in the allowlist
        self.assertEqual(self._restore().returncode, 0)
        lsp = self._staged() / "Emulation" / "tools" / "launchers"
        self.assertEqual((lsp / "controller-policy.local.toml").read_text(), "CFG")
        self.assertEqual((lsp / "sinden.conf").read_text(), "SND")
        self.assertFalse((lsp / "deck-cloud.sh").exists(), "allowlist never names tracked code")

    def test_self_heals_hookless_launch_wrapper(self):
        # review fix #2: a wrapper written BEFORE this feature lacks the apply hook, so a restart
        # would be inert. The restore must regenerate it (deck-post-update.sh --wrapper) when arming.
        (self.lp / "controller-policy.local.toml").write_text("CFG")
        (self.lp / ".mad-cloud-manifest.txt").write_text("controller-policy.local.toml\n")
        wrapper = self.home / "Applications" / "ES-DE.AppImage"
        wrapper.parent.mkdir(parents=True)
        wrapper.write_text("#!/usr/bin/env bash\n# old wrapper, no apply hook\nexec es-de\n")
        self.assertEqual(self._restore().returncode, 0)
        self.assertIn("apply-staged-restore.sh", wrapper.read_text(),
                      "restore must regenerate a hookless wrapper so the restart actually applies")


class WrapperApply(unittest.TestCase):
    """apply-staged-restore.sh: fail-safe apply of a staged tree onto live $HOME (subprocess with a
    throwaway HOME). No rclone/git needed -> RUNS on CI."""

    def setUp(self):
        self.h = Path(tempfile.mkdtemp())
        self.state = self.h / ".config" / "deck-cloud"; self.state.mkdir(parents=True)
        self.staged = self.h / "Downloads" / "_TMP" / "cloud-restore-x" / "_staged-apply"
        self.staged.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.h, ignore_errors=True)

    def _run(self):
        return subprocess.run([str(APPLY)], env=dict(os.environ, HOME=str(self.h)),
                              capture_output=True, text=True, timeout=30)

    def _arm(self):
        (self.state / "pending-restore-apply").write_text(str(self.staged) + "\n")

    def test_applies_backs_up_and_clears_marker(self):
        (self.staged / "ES-DE").mkdir()
        (self.staged / "ES-DE" / "es_settings.xml").write_text("NEW")
        live = self.h / "ES-DE"; live.mkdir(); (live / "es_settings.xml").write_text("OLD")
        self._arm()
        r = self._run()
        self.assertEqual(r.returncode, 0)
        self.assertEqual((live / "es_settings.xml").read_text(), "NEW", "staged applied over live")
        backed = list((self.h / "Downloads" / "_TMP").glob("wrapper-apply-*/ES-DE/es_settings.xml"))
        self.assertTrue(backed and backed[0].read_text() == "OLD", "old live file preserved (rule #5)")
        self.assertFalse((self.state / "pending-restore-apply").exists(), "marker cleared (single-shot)")

    def test_no_marker_is_a_fast_noop(self):
        r = self._run()
        self.assertEqual(r.returncode, 0)

    def test_unwritable_tmp_leaves_live_untouched(self):
        # review fix #1 (rule #5): if the _TMP backup can't be written, the live file must NOT be
        # overwritten (cp --remove-destination would unlink it with no recoverable copy).
        shutil.rmtree(self.h / "Downloads", ignore_errors=True)   # drop setUp's staged-under-_TMP tree
        staged = self.h / "own-staged"                            # staged OUTSIDE _TMP (stays readable)
        (staged / "ES-DE").mkdir(parents=True)
        (staged / "ES-DE" / "es_settings.xml").write_text("NEW")
        live = self.h / "ES-DE"; live.mkdir(); (live / "es_settings.xml").write_text("KEEP")
        (self.h / "Downloads").mkdir()
        (self.h / "Downloads" / "_TMP").write_text("i am a file, not a dir")  # mkdir under it fails
        (self.state / "pending-restore-apply").write_text(str(staged) + "\n")
        r = self._run()
        self.assertEqual(r.returncode, 0, "must still exit 0 (boot proceeds)")
        self.assertEqual((live / "es_settings.xml").read_text(), "KEEP",
                         "live file must survive when its _TMP backup cannot be written")

    def test_broken_marker_never_blocks_boot(self):
        (self.state / "pending-restore-apply").write_text(str(self.h / "nope") + "\n")
        r = self._run()
        self.assertEqual(r.returncode, 0, "a bad marker must not fail the wrapper")
        self.assertFalse((self.state / "pending-restore-apply").exists(), "bad marker cleared")


@unittest.skipUnless(HAVE_RCLONE, "needs rclone (Deck-only)")
class PushGamesUpload(unittest.TestCase):
    """deck-cloud.sh push-games: rclone each live ROM in the NUL plan straight to game-backups/<ts>/, and
    publish the manifest LAST + only when >=1 file uploaded (so a partial set stays unrestorable)."""
    def setUp(self):
        self.base = Path(tempfile.mkdtemp())
        self.roms = self.base / "ROMs"
        (self.roms / "nes").mkdir(parents=True)
        (self.roms / "ps3" / "MyGame").mkdir(parents=True)
        (self.roms / "nes" / "smb.zip").write_bytes(b"MARIO" * 100)
        (self.roms / "ps3" / "MyGame" / "EBOOT.BIN").write_bytes(b"X" * 40)
        self.tricky = self.roms / "nes" / 'Uber "GOTY" & co.zip'   # space + quote + &
        self.tricky.write_bytes(b"WEIRD")
        self.remote = self.base / "remote"
        self.plandir = self.base / "plan"; self.plandir.mkdir()
        (self.plandir / "mad-manifest.json").write_text('{"schema": 1}')   # push-games only uploads it

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def _plan(self, pairs):
        (self.plandir / "plan").write_bytes(
            b"".join(s.encode() + b"\0" + r.encode() + b"\0" for s, r in pairs))

    def _run(self, ts):
        env = dict(os.environ, DECK_CLOUD_RCLONE=str(BIN / "rclone"), DECK_CLOUD_SKIP_CONNCHECK="1",
                   DECK_CLOUD_NO_NICE="1", DECK_CLOUD_STATE_DIR=str(self.base / "state"),
                   DECK_CLOUD_GAMES_BASE_OVERRIDE=str(self.remote))
        return subprocess.run([str(CLOUD), "push-games", ts, str(self.plandir)],
                              env=env, capture_output=True, text=True, timeout=120)

    def test_uploads_tree_and_manifest(self):
        self._plan([(str(self.roms / "nes" / "smb.zip"), "roms/nes/smb.zip"),
                    (str(self.roms / "ps3" / "MyGame"), "roms/ps3/MyGame"),
                    (str(self.tricky), 'roms/nes/Uber "GOTY" & co.zip')])
        p = self._run("20260725T101112")
        self.assertEqual(p.returncode, 0, p.stderr)
        run = self.remote / "20260725T101112"
        self.assertEqual((run / "roms/nes/smb.zip").read_bytes(), b"MARIO" * 100)
        self.assertTrue((run / "roms/ps3/MyGame/EBOOT.BIN").is_file(),
                        "folder ROM copies to roms/ps3/MyGame/ (not doubled)")
        self.assertTrue((run / 'roms/nes/Uber "GOTY" & co.zip').is_file(),
                        "a name with a space/quote/& round-trips via the NUL plan")
        self.assertTrue((run / "mad-manifest.json").is_file(), "manifest published")
        self.assertFalse(self.plandir.exists(), "plan dir cleaned on a clean publish")
        self.assertIn("MAD_SET_SUMMARY uploaded=3 failed=0", p.stdout,
                      "a clean publish reports failed=0 (the UI shows a plain success)")

    def test_all_missing_writes_no_manifest_and_cleans_plan(self):
        self._plan([("/nonexistent/gone.zip", "roms/nes/gone.zip")])
        p = self._run("20260725T120000")
        self.assertNotEqual(p.returncode, 0, "all-missing uploads nothing -> non-zero")
        self.assertFalse((self.remote / "20260725T120000" / "mad-manifest.json").exists(),
                         "no manifest when nothing uploaded (a partial set must be unrestorable)")
        self.assertFalse(self.plandir.exists(), "a failed upload cleans its own plan dir (no orphan)")

    def test_one_missing_src_skipped_others_upload(self):
        self._plan([("/nonexistent/gone.zip", "roms/nes/gone.zip"),
                    (str(self.roms / "nes" / "smb.zip"), "roms/nes/smb.zip")])
        p = self._run("20260725T130000")
        self.assertEqual(p.returncode, 0, p.stderr)
        run = self.remote / "20260725T130000"
        self.assertTrue((run / "roms/nes/smb.zip").is_file(), "the present ROM still uploads")
        self.assertTrue((run / "mad-manifest.json").is_file())
        self.assertIn("MAD_SET_SUMMARY uploaded=1 failed=1", p.stdout,
                      "the partial-upload count is surfaced on stdout so the RPC/UI can warn")


@unittest.skipUnless(HAVE_RCLONE, "needs rclone (Deck-only)")
class PushGamesRoundTrip(unittest.TestCase):
    """Constraint 4: a cloud game-backup is restorable with the UNCHANGED granular engine - the remote
    <ts> tree + manifest are byte-compatible with a local deck-granular-<ts>/ folder, so restore_selection
    works when pointed at the downloaded folder (here the local override base IS that folder)."""
    def setUp(self):
        from unittest import mock
        from lib import es_systems, game_files, granular_backup as gb
        self.mock, self.gb, self.game_files, self.es_systems = mock, gb, game_files, es_systems
        self.base = Path(tempfile.mkdtemp())
        self.roms = self.base / "ROMs"; (self.roms / "nes").mkdir(parents=True)
        self.rom = self.roms / "nes" / "smb.zip"; self.rom.write_bytes(b"MARIO" * 100)
        self.remote = self.base / "remote"
        self.plandir = self.base / "plan"; self.plandir.mkdir()
        self._patches = [
            mock.patch.object(gb.es_collections, "rom_root", lambda: self.roms),
            mock.patch.object(game_files, "resolve_rom",
                              lambda s, st: [str(self.rom)] if (s, st) == ("nes", "smb") else []),
            mock.patch.object(game_files, "resolve_boxart", lambda s, st: {}),
            mock.patch.object(es_systems, "fullname", lambda s: s.upper()),
            mock.patch.object(gb, "es_gamelist_record", lambda s, st: {"name": st}),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        shutil.rmtree(self.base, ignore_errors=True)

    def test_push_then_restore_with_existing_engine(self):
        from lib import backup_manifest as bm
        ts = "20260725T140000"
        manifest, plan = self.gb.plan_selection([{"system": "nes", "stem": "smb"}],
                                                "roms", "ROMs & games", ts)
        bm.write(manifest, bm.manifest_path(self.plandir))
        (self.plandir / "plan").write_bytes(
            b"".join(e["src"].encode() + b"\0" + e["rel"].encode() + b"\0" for e in plan))
        env = dict(os.environ, DECK_CLOUD_RCLONE=str(BIN / "rclone"), DECK_CLOUD_SKIP_CONNCHECK="1",
                   DECK_CLOUD_NO_NICE="1", DECK_CLOUD_STATE_DIR=str(self.base / "state"),
                   DECK_CLOUD_GAMES_BASE_OVERRIDE=str(self.remote))
        p = subprocess.run([str(CLOUD), "push-games", ts, str(self.plandir)],
                           env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(p.returncode, 0, p.stderr)
        downloaded = self.remote / ts        # the local "remote" IS the downloaded folder for the test
        self.assertTrue((downloaded / "mad-manifest.json").is_file())
        self.rom.write_bytes(b"CORRUPT")     # mutate the live copy, then restore from the cloud folder
        rr = self.gb.restore_selection(str(downloaded), [{"system": "nes", "id": "nes:smb"}],
                                       "roms", "20260725T150000", lambda d: None, lambda: False)
        self.assertEqual(rr["restored"], 1, "the cloud folder restores via the UNCHANGED engine")
        self.assertEqual(self.rom.read_bytes(), b"MARIO" * 100)
        self.assertIsNotNone(rr["snapshot"], "rule-5 snapshot of the corrupt live copy was taken")


class PushGamesManifestGate(unittest.TestCase):
    """The resumable-on-failure contract, with a STUB rclone (CI-safe, needs no real rclone): if the ROM
    copies succeed but the manifest publish FAILS, the plan dir must be KEPT so the daemon's auto-resume
    can finish the job (re-copy no-op + re-publish the manifest) and recover the otherwise-orphaned set."""
    def setUp(self):
        self.base = Path(tempfile.mkdtemp())
        self.roms = self.base / "ROMs"; (self.roms / "nes").mkdir(parents=True)
        (self.roms / "nes" / "smb.zip").write_bytes(b"MARIO")
        self.remote = self.base / "remote"
        self.plandir = self.base / "plan"; self.plandir.mkdir()
        (self.plandir / "mad-manifest.json").write_text('{"schema": 1}')
        (self.plandir / "plan").write_bytes(
            str(self.roms / "nes" / "smb.zip").encode() + b"\0roms/nes/smb.zip\0")
        # a stub rclone: `copy` succeeds (real cp), `copyto` (the manifest publish) FAILS.
        self.stub = self.base / "rclone"
        self.stub.write_text(
            "#!/usr/bin/env bash\n"
            'sub="$1"; shift\n'
            'if [ "$sub" = copy ]; then s="$1"; d="$2"; mkdir -p "$d"; cp -r "$s" "$d"/ 2>/dev/null; exit 0; fi\n'
            'if [ "$sub" = copyto ]; then exit 1; fi\n'   # force the manifest publish to fail
            "exit 0\n")
        self.stub.chmod(0o755)

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def test_manifest_failure_keeps_plan_dir_for_resume(self):
        env = dict(os.environ, DECK_CLOUD_RCLONE=str(self.stub), DECK_CLOUD_SKIP_CONNCHECK="1",
                   DECK_CLOUD_NO_NICE="1", DECK_CLOUD_STATE_DIR=str(self.base / "state"),
                   DECK_CLOUD_GAMES_BASE_OVERRIDE=str(self.remote))
        p = subprocess.run([str(CLOUD), "push-games", "20260725T160000", str(self.plandir)],
                           env=env, capture_output=True, text=True, timeout=60)
        self.assertNotEqual(p.returncode, 0, "a failed manifest publish is a non-zero result")
        self.assertFalse((self.remote / "20260725T160000" / "mad-manifest.json").exists(),
                         "the manifest was not published (the set stays unrestorable)")
        self.assertTrue(self.plandir.exists(),
                        "the plan dir is KEPT so auto-resume can finish + recover the uploaded ROMs")


if __name__ == "__main__":
    unittest.main()
