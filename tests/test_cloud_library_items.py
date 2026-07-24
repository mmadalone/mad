"""Cloud backup building blocks.

1. deck-backup.sh --list-library-items: emits "<key>\t<path>" for EXISTING big-library
   (Tier-B) categories only, and omits absent ones. This is deck-cloud.sh's single
   source of truth for what the "Sync library now" button uploads; the config-archive
   --list-items deliberately omits these paths, so a separate mode exists.

2. deck-cloud.sh status/toggle basics (skipped where the rclone binary is not
   installed, e.g. CI - they live under ~/Emulation/tools/bin on the Deck).

Run:  python3 -m unittest tests.test_cloud_library_items -v
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKUP = ROOT / "deck-backup.sh"
CLOUD = ROOT / "deck-cloud.sh"
BIN = Path.home() / "Emulation" / "tools" / "bin"
HAVE_BINS = (BIN / "rclone").exists()   # rclone-only now (restic was dropped)


def _rows(stdout: str) -> dict:
    """Parse key<TAB>path lines, dropping deck-backup.sh's [backup] stdout log noise."""
    out = {}
    for line in stdout.splitlines():
        if "\t" in line:
            k, p = line.split("\t", 1)
            out[k] = p
    return out


class LibraryItemList(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        (self.home / "ROMs" / "nes").mkdir(parents=True)      # ROM_ROOT exists
        # bezelproject / cores / storage game-data deliberately NOT created

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def _run(self) -> dict:
        env = dict(os.environ, HOME=str(self.home), BACKUP_DEST=str(self.home / "dest"))
        p = subprocess.run([str(BACKUP), "--list-library-items"], env=env,
                           capture_output=True, text=True, timeout=60)
        return _rows(p.stdout)

    def test_existing_category_is_emitted_with_its_path(self):
        rows = self._run()
        self.assertIn("roms", rows)
        self.assertTrue(Path(rows["roms"]).is_dir(),
                        f"roms path should be a real dir: {rows.get('roms')!r}")

    def test_absent_category_is_omitted(self):
        # BEZEL_DIR = $HOME/Emulation/tools/bezelproject is strictly $HOME-relative and
        # was not created under this temp HOME, so it must not appear.
        self.assertNotIn("bezels", self._run())

    def test_output_is_key_tab_path_only(self):
        # every emitted data row must be exactly key<TAB>path with an existing path
        for k, p in self._run().items():
            self.assertRegex(k, r"^[a-z0-9]+$")
            self.assertTrue(p.startswith("/"), f"{k} path not absolute: {p!r}")


@unittest.skipUnless(HAVE_BINS, "rclone not installed (CI); Deck-only smoke")
class CloudEngineBasics(unittest.TestCase):
    def setUp(self):
        self.state = Path(tempfile.mkdtemp())
        # point creds at an absent path so is_connected is deterministically
        # "not connected" regardless of any real S4 setup on this machine.
        self.env = dict(os.environ, DECK_CLOUD_STATE_DIR=str(self.state),
                        DECK_CLOUD_CREDS=str(self.state / "no-such-creds"))

    def tearDown(self):
        shutil.rmtree(self.state, ignore_errors=True)

    def _cloud(self, *args, expect_rc=None):
        p = subprocess.run([str(CLOUD), *args], env=self.env,
                           capture_output=True, text=True, timeout=60)
        if expect_rc is not None:
            self.assertEqual(p.returncode, expect_rc, p.stderr)
        return p

    def test_status_when_not_connected(self):
        rows = _rows(self._cloud("status").stdout)
        self.assertEqual(rows.get("connected"), "0")
        self.assertEqual(rows.get("bucket"), "steamdeck")
        self.assertIn("precious", (rows.get("precious") or ""))

    def test_push_precious_skips_cleanly_when_not_connected(self):
        # the hook/timer path: must return 0 and NOT error when unconfigured
        self._cloud("push-precious", expect_rc=0)

    def test_onexit_toggle_round_trips(self):
        self._cloud("set-toggle", "onexit", "on", expect_rc=0)
        self.assertTrue((self.state / "onexit.enabled").exists())
        self.assertEqual(_rows(self._cloud("status").stdout).get("onexit_enabled"), "1")
        self._cloud("set-toggle", "onexit", "off", expect_rc=0)
        self.assertFalse((self.state / "onexit.enabled").exists())

    def test_autoresume_toggle_round_trips(self):
        # default ON (no file); set off -> status 0; set on -> status 1.
        self.assertEqual(_rows(self._cloud("status").stdout).get("autoresume_enabled"), "1",
                         "auto-resume defaults ON")
        self._cloud("set-toggle", "autoresume", "off", expect_rc=0)
        self.assertEqual(_rows(self._cloud("status").stdout).get("autoresume_enabled"), "0")
        self._cloud("set-toggle", "autoresume", "on", expect_rc=0)
        self.assertEqual(_rows(self._cloud("status").stdout).get("autoresume_enabled"), "1")

    def test_default_server_is_global(self):
        st = _rows(self._cloud("status").stdout)
        self.assertEqual(st.get("server"), "global")
        self.assertIn("s3.g.s4.mega.io", st.get("endpoint", ""))

    def test_set_server_round_trips(self):
        # --no-probe keeps this offline (no S4 hit); the file + status must reflect the switch.
        self._cloud("set-server", "barcelona", "--no-probe", expect_rc=0)
        self.assertEqual((self.state / "server").read_text().strip(), "barcelona")
        st = _rows(self._cloud("status").stdout)
        self.assertEqual(st.get("server"), "barcelona")
        self.assertIn("eu-barcelona.megas4.com", st.get("endpoint", ""))
        self._cloud("set-server", "global", "--no-probe", expect_rc=0)
        self.assertEqual(_rows(self._cloud("status").stdout).get("server"), "global")

    def test_push_precious_honors_disabled_category(self):
        # A disabled Tier-A category must become --no-<cat> in the deck-backup flags (so the
        # headless on-exit/timer backups honor the selection too). A stub dumps the flags.
        base = Path(tempfile.mkdtemp())
        try:
            state = base / "state"; state.mkdir()
            src = base / "src"; src.mkdir(); (src / "f.bin").write_bytes(b"x" * 1000)
            argdump = base / "args"
            stub = base / "stub.sh"
            stub.write_text('#!/usr/bin/env bash\ncase "$*" in *--list-items*) '
                            'echo "$*" > "' + str(argdump) + '"; printf "%s\\n" "' + str(src) + '";; esac\n')
            stub.chmod(0o755)
            env = dict(os.environ, DECK_CLOUD_STATE_DIR=str(state), DECK_CLOUD_SKIP_CONNCHECK="1",
                       DECK_CLOUD_RCLONE=str(BIN / "rclone"), DECK_CLOUD_BACKUP_SCRIPT=str(stub),
                       DECK_CLOUD_PRECIOUS_BASE_OVERRIDE=str(base / "dest"),
                       DECK_CLOUD_PRECIOUS_VERS_OVERRIDE=str(base / "vers"))

            def run(*a):
                return subprocess.run([str(CLOUD), *a], env=env, capture_output=True, text=True, timeout=60)

            run("set-category", "emu", "off")
            run("push-precious", "--force")
            args = argdump.read_text()
            self.assertIn("--no-emu", args, args)
            self.assertIn("--esde", args, "other Tier-A categories stay on")
        finally:
            shutil.rmtree(base, ignore_errors=True)

    def test_restore_precious_to_live(self):
        # In-place restore: overwrite live by CONTENT, old -> _TMP (rule #5); tooling excluded;
        # ES-DE STAGED into _staged-apply (not applied live) since ES-DE rewrites its own config on
        # exit (rule #3); a pending-restore-apply marker arms the wrapper to apply it on next boot.
        base = Path(tempfile.mkdtemp())
        try:
            home = base / "home"; (home / "bios").mkdir(parents=True); (home / "Downloads").mkdir()
            (home / "bios" / "x.bin").write_text("OLD")
            state = home / ".config" / "deck-cloud"
            prec = base / "prec"; (prec / "bios").mkdir(parents=True)
            (prec / "Applications").mkdir(); (prec / "ES-DE").mkdir()
            (prec / "bios" / "x.bin").write_text("NEW")
            (prec / "Applications" / "es.txt").write_text("APP")
            (prec / "ES-DE" / "settings.xml").write_text("CFG")
            env = dict(os.environ, HOME=str(home), DECK_CLOUD_RCLONE=str(BIN / "rclone"),
                       DECK_CLOUD_STATE_DIR=str(state),
                       DECK_CLOUD_SKIP_CONNCHECK="1", DECK_CLOUD_PRECIOUS_BASE_OVERRIDE=str(prec))
            subprocess.run([str(CLOUD), "restore-precious", "--to-live"], env=env,
                           capture_output=True, text=True, timeout=60)
            self.assertEqual((home / "bios" / "x.bin").read_text(), "NEW", "restored over live")
            self.assertFalse((home / "Applications" / "es.txt").exists(), "tooling must be excluded")
            self.assertFalse((home / "ES-DE" / "settings.xml").exists(),
                             "ES-DE must NOT be restored in place (rule #3)")
            staged = list((home / "Downloads").glob(
                "_TMP/cloud-restore-*/_staged-apply/ES-DE/settings.xml"))
            self.assertTrue(staged and staged[0].read_text() == "CFG", "ES-DE staged into _staged-apply")
            saved = list((home / "Downloads").glob("_TMP/cloud-restore-*/bios/x.bin"))
            self.assertTrue(saved and saved[0].read_text() == "OLD", "old file preserved in _TMP")
            marker = state / "pending-restore-apply"
            self.assertTrue(marker.exists(), "wrapper apply marker must be armed")
            self.assertTrue(marker.read_text().strip().endswith("_staged-apply"),
                            "marker points at the staged tree")
        finally:
            shutil.rmtree(base, ignore_errors=True)

    def test_restore_library_to_live_recreates_symlink(self):
        # backup a category whose front-door is a symlink (~/ROMs style), then restore --to-live
        # from-scratch must put files at the target AND recreate the symlink (from the manifest).
        base = Path(tempfile.mkdtemp())
        sd = base / "sd" / "ROMs"; sd.mkdir(parents=True); (sd / "game.rom").write_text("G1")
        (base / "home").mkdir()
        fd = base / "home" / "ROMs"; fd.symlink_to(sd)
        libbase = base / "libbase"; libbase.mkdir()
        stub = base / "stub.sh"
        stub.write_text('#!/usr/bin/env bash\ncase "$*" in *--list-library-items*) '
                        'printf "roms\\t%s\\n" "' + str(sd) + '";; esac\n')
        stub.chmod(0o755)
        env = dict(os.environ,
                   DECK_CLOUD_RCLONE=str(BIN / "rclone"), DECK_CLOUD_SKIP_CONNCHECK="1",
                   DECK_CLOUD_STATE_DIR=str(base / "state"), DECK_CLOUD_BACKUP_SCRIPT=str(stub),
                   DECK_CLOUD_LIB_BASE_OVERRIDE=str(libbase), DECK_CLOUD_FRONTDOOR_ROMS=str(fd),
                   HOME=str(base))

        def run(*a):
            return subprocess.run([str(CLOUD), *a], env=env, capture_output=True, text=True, timeout=60)
        try:
            run("sync-library")
            self.assertTrue((libbase / "library-symlinks.tsv").exists(), "manifest not written")
            fd.unlink()                                   # simulate a from-scratch Deck
            run("restore-library", "roms", "--to-live")
            self.assertTrue(fd.is_symlink(), "front-door symlink not recreated")
            self.assertEqual((fd / "game.rom").read_text(), "G1", "files not restored via the link")
        finally:
            shutil.rmtree(base, ignore_errors=True)

    def test_restore_library_recreates_nested_symlinks(self):
        # ~/ROMs contains a nested symlink (ps2 -> an internal dir) whose target is backed up
        # separately. sync must RECORD it (manifest @row) WITHOUT duplicating the target data, and
        # restore must recreate it AS a symlink (not dereference it into a real dir).
        base = Path(tempfile.mkdtemp())
        try:
            internal = base / "home" / "Emulation" / "roms" / "ps2"; internal.mkdir(parents=True)
            (internal / "game.iso").write_text("PS2")
            sd = base / "sd" / "ROMs"; sd.mkdir(parents=True)
            (sd / "nes").mkdir(); (sd / "nes" / "g.nes").write_text("N1")   # a real SD system
            (sd / "ps2").symlink_to(internal)                               # the nested symlink
            fd = base / "home" / "ROMs"; fd.symlink_to(sd)
            libbase = base / "libbase"; libbase.mkdir()
            stub = base / "stub.sh"
            stub.write_text('#!/usr/bin/env bash\ncase "$*" in *--list-library-items*) '
                            'printf "roms\\t%s\\n" "' + str(sd) + '";; esac\n')
            stub.chmod(0o755)
            env = dict(os.environ, DECK_CLOUD_RCLONE=str(BIN / "rclone"), DECK_CLOUD_SKIP_CONNCHECK="1",
                       DECK_CLOUD_NO_NICE="1", DECK_CLOUD_STATE_DIR=str(base / "state"),
                       DECK_CLOUD_BACKUP_SCRIPT=str(stub), DECK_CLOUD_LIB_BASE_OVERRIDE=str(libbase),
                       DECK_CLOUD_FRONTDOOR_ROMS=str(fd), HOME=str(base))

            def run(*a):
                return subprocess.run([str(CLOUD), *a], env=env, capture_output=True, text=True, timeout=90)

            run("sync-library")
            man = (libbase / "library-symlinks.tsv").read_text()
            self.assertIn("@ps2", man, f"nested link not recorded: {man!r}")
            self.assertIn(str(internal), man, "nested link target not recorded")
            # the ps2 TARGET data must NOT be duplicated into the library (no dereference).
            self.assertFalse((libbase / "roms" / "ps2" / "game.iso").exists(),
                             "ps2 target must not be copied into the library (would duplicate the internal dir)")
            self.assertTrue((libbase / "roms" / "nes" / "g.nes").exists(), "real SD files should upload")
            # simulate a fresh SD: drop the nested link + the real file, then restore.
            (sd / "ps2").unlink(); (sd / "nes" / "g.nes").unlink()
            run("restore-library", "roms", "--to-live")
            self.assertEqual((sd / "nes" / "g.nes").read_text(), "N1", "real SD files not restored")
            self.assertTrue((sd / "ps2").is_symlink(), "nested ps2 must be restored AS a symlink")
            self.assertEqual(os.readlink(sd / "ps2"), os.path.realpath(internal),
                             "ps2 must point back at the internal dir")
        finally:
            shutil.rmtree(base, ignore_errors=True)

    def test_restore_library_hyphenated_category_roundtrips(self):
        # rpcs3games -> subdir 'rpcs3-games' on S4 (remap), and restore --to-live targets the live
        # path from --list-library-items (no front-door). Guards the subdir-remap + non-roms target.
        base = Path(tempfile.mkdtemp())
        try:
            live = base / "storage" / "rpcs3" / "dev_hdd0" / "game"; live.mkdir(parents=True)
            (live / "g.bin").write_text("G1")
            libbase = base / "libbase"; libbase.mkdir()
            stub = base / "stub.sh"
            stub.write_text('#!/usr/bin/env bash\ncase "$*" in *--list-library-items*) '
                            'printf "rpcs3games\\t%s\\n" "' + str(live) + '";; esac\n')
            stub.chmod(0o755)
            env = dict(os.environ, DECK_CLOUD_RCLONE=str(BIN / "rclone"),
                       DECK_CLOUD_SKIP_CONNCHECK="1", DECK_CLOUD_STATE_DIR=str(base / "state"),
                       DECK_CLOUD_BACKUP_SCRIPT=str(stub), DECK_CLOUD_LIB_BASE_OVERRIDE=str(libbase),
                       HOME=str(base))

            def run(*a):
                return subprocess.run([str(CLOUD), *a], env=env, capture_output=True, text=True, timeout=60)

            run("sync-library")
            self.assertTrue((libbase / "rpcs3-games" / "g.bin").exists(), "synced to the remapped subdir")
            (live / "g.bin").unlink()  # wipe the live file, then restore in place
            run("restore-library", "rpcs3games", "--to-live")
            self.assertEqual((live / "g.bin").read_text(), "G1", "restored to the live path via _livedir")
        finally:
            shutil.rmtree(base, ignore_errors=True)

    def test_push_precious_excludes_logs_and_reacquirable_bulk(self):
        # The cloud precious mirror must DROP logs + the re-downloadable subdirs (RetroArch
        # online-updater set, EmuDeck caches) while KEEPING real config/saves. Uses real rclone
        # to a local dest so the actual --exclude patterns are exercised end-to-end.
        base = Path(tempfile.mkdtemp())
        try:
            src = base / "src"
            (src / "logs").mkdir(parents=True); (src / "logs" / "a.log").write_text("L")
            (src / "assets").mkdir(); (src / "assets" / "x.png").write_text("A")   # RA online-updater
            (src / "backend").mkdir(); (src / "backend" / "b.sh").write_text("B")  # EmuDeck backend
            (src / "config").mkdir(); (src / "config" / "cfg").write_text("C")     # precious
            (src / "keep.srm").write_text("S")                                     # precious save
            (src / "stray.log").write_text("SL")                                   # stray log file
            stub = base / "backup.sh"
            stub.write_text('#!/usr/bin/env bash\ncase "$*" in *--list-items*) '
                            'printf "%s\\n" "' + str(src) + '";; esac\n')
            stub.chmod(0o755)
            dest = base / "dest"
            env = dict(os.environ, HOME=str(base), DECK_CLOUD_SKIP_CONNCHECK="1",
                       DECK_CLOUD_NO_NICE="1", DECK_CLOUD_RCLONE=str(BIN / "rclone"),
                       DECK_CLOUD_BACKUP_SCRIPT=str(stub), DECK_CLOUD_STATE_DIR=str(base / "state"),
                       DECK_CLOUD_PRECIOUS_BASE_OVERRIDE=str(dest),
                       DECK_CLOUD_PRECIOUS_VERS_OVERRIDE=str(base / "vers"))
            subprocess.run([str(CLOUD), "push-precious", "--force"], env=env,
                           capture_output=True, text=True, timeout=120)
            got = dest / "src"   # src is $HOME-relative -> dest/src
            self.assertTrue((got / "config" / "cfg").exists(), "real config must be kept")
            self.assertTrue((got / "keep.srm").exists(), "real save must be kept")
            self.assertFalse((got / "logs" / "a.log").exists(), "logs/ must be excluded")
            self.assertFalse((got / "stray.log").exists(), "stray .log must be excluded")
            self.assertFalse((got / "assets" / "x.png").exists(), "RA assets/ must be excluded")
            self.assertFalse((got / "backend" / "b.sh").exists(), "EmuDeck backend/ must be excluded")
        finally:
            shutil.rmtree(base, ignore_errors=True)

    def test_push_precious_skips_config_emudeck_entirely(self):
        # ~/.config/EmuDeck is dropped WHOLESALE from the cloud precious set (~272MB of re-acquirable
        # EmuDeck backend + Electron cache); _cloud_skip_item skips the whole item. Local backup keeps it.
        base = Path(tempfile.mkdtemp())
        try:
            emu = base / ".config" / "EmuDeck"
            (emu / "backend").mkdir(parents=True)
            (emu / "backend" / "b.sh").write_text("B")
            (emu / "settings.json").write_text("{}")
            stub = base / "backup.sh"
            stub.write_text('#!/usr/bin/env bash\ncase "$*" in *--list-items*) '
                            'printf "%s\\n" "' + str(emu) + '";; esac\n')
            stub.chmod(0o755)
            dest = base / "dest"
            env = dict(os.environ, HOME=str(base), DECK_CLOUD_SKIP_CONNCHECK="1",
                       DECK_CLOUD_NO_NICE="1", DECK_CLOUD_RCLONE=str(BIN / "rclone"),
                       DECK_CLOUD_BACKUP_SCRIPT=str(stub), DECK_CLOUD_STATE_DIR=str(base / "state"),
                       DECK_CLOUD_PRECIOUS_BASE_OVERRIDE=str(dest),
                       DECK_CLOUD_PRECIOUS_VERS_OVERRIDE=str(base / "vers"))
            r = subprocess.run([str(CLOUD), "push-precious", "--force"], env=env,
                               capture_output=True, text=True, timeout=120)
            self.assertIn("skipping re-acquirable", r.stdout + r.stderr)
            self.assertFalse((dest / ".config" / "EmuDeck").exists(),
                             "the whole ~/.config/EmuDeck item must be skipped from the cloud")
        finally:
            shutil.rmtree(base, ignore_errors=True)

    def test_push_precious_does_not_follow_wine_prefix_drive_symlinks(self):
        # A Wine/Proton prefix maps DOS drive letters to real filesystems via symlinks in
        # dosdevices/ (d:->the SD card = the whole ROM library, z:->/). push-precious copies with
        # -L, so without the exclude it would chase those and upload ROMs/ISOs + crawl root. The
        # dosdevices + drive_c/windows excludes must prune them while keeping genuine data.
        base = Path(tempfile.mkdtemp())
        try:
            sd = base / "sdcard" / "ROMs" / "3do"; sd.mkdir(parents=True)
            (sd / "game.iso").write_bytes(b"ISO" * 1000)          # what d: points at
            src = base / "storage"
            dd = src / "game" / "prefix" / "pfx" / "dosdevices"; dd.mkdir(parents=True)
            (dd / "d:").symlink_to(base / "sdcard")               # drive letter -> the SD card
            win = src / "game" / "prefix" / "pfx" / "drive_c" / "windows"; win.mkdir(parents=True)
            (win / "notepad.exe").write_bytes(b"WIN")             # wine Windows (Proton symlinks)
            (src / "realsave.srm").write_bytes(b"SAVE")           # genuine precious data
            stub = base / "backup.sh"
            stub.write_text('#!/usr/bin/env bash\ncase "$*" in *--list-items*) '
                            'printf "%s\\n" "' + str(src) + '";; esac\n')
            stub.chmod(0o755)
            dest = base / "dest"
            env = dict(os.environ, HOME=str(base), DECK_CLOUD_SKIP_CONNCHECK="1",
                       DECK_CLOUD_NO_NICE="1", DECK_CLOUD_RCLONE=str(BIN / "rclone"),
                       DECK_CLOUD_BACKUP_SCRIPT=str(stub), DECK_CLOUD_STATE_DIR=str(base / "state"),
                       DECK_CLOUD_PRECIOUS_BASE_OVERRIDE=str(dest),
                       DECK_CLOUD_PRECIOUS_VERS_OVERRIDE=str(base / "vers"))
            subprocess.run([str(CLOUD), "push-precious", "--force"], env=env,
                           capture_output=True, text=True, timeout=120)
            got = dest / "storage"
            self.assertTrue((got / "realsave.srm").exists(), "genuine precious data must be kept")
            self.assertEqual(list(got.rglob("*.iso")), [], "no ISO may ride in via the d: drive symlink")
            self.assertEqual(list(got.rglob("dosdevices")), [], "dosdevices/ must not be traversed")
            self.assertFalse((got / "game" / "prefix" / "pfx" / "drive_c" / "windows"
                              / "notepad.exe").exists(), "wine drive_c/windows must be excluded")
        finally:
            shutil.rmtree(base, ignore_errors=True)

    def test_cloud_sizes_applies_excludes_and_skips(self):
        # cloud-sizes must report the REAL post-filter upload bytes: excluded subdirs (logs/,
        # assets/) drop out, the always-on core is subtracted (disjoint, like deck-backup
        # --sizes), and a skip item (*.AppImage) never counts. So a category's number is far
        # below its raw on-disk size.
        base = Path(tempfile.mkdtemp())
        try:
            src = base / "storage"; src.mkdir()
            (src / "cfg.ini").write_bytes(b"C" * 100_000)                            # kept
            (src / "logs").mkdir(); (src / "logs" / "x.log").write_bytes(b"L" * 500_000)     # excluded
            (src / "assets").mkdir(); (src / "assets" / "a.bin").write_bytes(b"A" * 500_000)  # excluded
            appimg = base / "Applications" / "z.AppImage"
            appimg.parent.mkdir(); appimg.write_bytes(b"Z" * 500_000)                # core + skip
            # stub deck-backup: core enumeration (all --no-*) -> the AppImage; emu -> AppImage + src.
            # So src is emu-only; the AppImage is core (subtracted) AND a skip item.
            stub = base / "backup.sh"
            stub.write_text(
                '#!/usr/bin/env bash\ncase "$*" in\n'
                '  *"--no-esde --no-emu --no-saves --no-bios"*) printf "%s\\n" "'
                + str(appimg) + '" ;;\n'
                '  *" --emu "*) printf "%s\\n" "' + str(appimg) + '" "' + str(src) + '" ;;\n'
                'esac\n')
            stub.chmod(0o755)
            env = dict(os.environ, DECK_CLOUD_RCLONE=str(BIN / "rclone"), DECK_CLOUD_NO_NICE="1",
                       DECK_CLOUD_BACKUP_SCRIPT=str(stub), DECK_CLOUD_STATE_DIR=str(base / "state"))
            p = subprocess.run([str(CLOUD), "cloud-sizes"], env=env,
                               capture_output=True, text=True, timeout=90)
            sizes = {}
            for line in p.stdout.splitlines():
                if "\t" in line:
                    k, v = line.split("\t", 1)
                    if v.strip().isdigit():
                        sizes[k] = int(v)
            self.assertIn("emu", sizes)
            # only the kept cfg.ini (~100k) counts: logs/ + assets/ excluded, the AppImage is
            # both core (subtracted) and a skip item -> ~100k, not the ~1.6M on disk.
            self.assertGreater(sizes["emu"], 50_000, "the kept config must count")
            self.assertLess(sizes["emu"], 400_000, "logs/assets/AppImage must be excluded")
            self.assertEqual(sizes.get("esde", 0), 0, "esde enumerates nothing here")
        finally:
            shutil.rmtree(base, ignore_errors=True)


class CloudPreciousFiltering(unittest.TestCase):
    """Cloud-only precious filtering + manual/background priority - all via a STUB rclone that
    dumps its argv (no real rclone, no ionice), so these RUN on CI and guard the behavior."""

    def _run_push(self, base, paths, *op, list_flag="--list-items"):
        """Stub deck-backup emits `paths`; stub rclone appends its argv to a dump. Returns the
        dumped argv text. `op` = the deck-cloud.sh args (e.g. 'push-precious','--force')."""
        dump = base / "rclone-args.txt"
        rc = base / "rclone"
        rc.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "' + str(dump) + '"\nexit 0\n')
        rc.chmod(0o755)
        emit = " ".join('"%s"' % p for p in paths)
        bk = base / "backup.sh"
        bk.write_text('#!/usr/bin/env bash\ncase "$*" in *' + list_flag + '*) '
                      'printf "%s\\n" ' + emit + ';; esac\n')
        bk.chmod(0o755)
        env = dict(os.environ, HOME=str(base), DECK_CLOUD_SKIP_CONNCHECK="1",
                   DECK_CLOUD_NO_NICE="1", DECK_CLOUD_RCLONE=str(rc),
                   DECK_CLOUD_BACKUP_SCRIPT=str(bk), DECK_CLOUD_STATE_DIR=str(base / "state"),
                   DECK_CLOUD_PRECIOUS_BASE_OVERRIDE=str(base / "dest"),
                   DECK_CLOUD_PRECIOUS_VERS_OVERRIDE=str(base / "vers"),
                   DECK_CLOUD_LIB_BASE_OVERRIDE=str(base / "lib"))
        subprocess.run([str(CLOUD), *op], env=env, capture_output=True, text=True, timeout=60)
        return dump.read_text() if dump.exists() else ""

    def test_reacquirable_whole_items_are_skipped(self):
        base = Path(tempfile.mkdtemp())
        try:
            appimg = base / "Applications" / "ES-DE-MAD.AppImage"
            appimg.parent.mkdir(parents=True); appimg.write_bytes(b"BIG")
            skraper = base / "Emulation" / "tools" / "Skraper-1.1.1"
            skraper.mkdir(parents=True); (skraper / "t.dll").write_bytes(b"x")
            keep = base / "saves"; keep.mkdir(); (keep / "g.srm").write_bytes(b"S")
            args = self._run_push(base, [appimg, skraper, keep], "push-precious", "--force")
            self.assertIn(str(keep), args, "a normal precious path must still be uploaded")
            self.assertNotIn(str(appimg), args, "the AppImage (GitHub release) must be skipped")
            self.assertNotIn("Skraper-1.1.1", args, "the Skraper tool must be skipped")
        finally:
            shutil.rmtree(base, ignore_errors=True)

    def test_manual_vs_background_transfer_count(self):
        base = Path(tempfile.mkdtemp())
        try:
            keep = base / "saves"; keep.mkdir(); (keep / "g.srm").write_bytes(b"S")
            fg = self._run_push(base, [keep], "push-precious", "--force")
            self.assertIn("--transfers 32", fg, "manual 'Back up now' (--force) uploads at 32")
            shutil.rmtree(base / "state", ignore_errors=True)
            (base / "rclone-args.txt").unlink(missing_ok=True)
            bg = self._run_push(base, [keep], "push-precious")   # no --force = timer/hook
            self.assertIn("--transfers 16", bg, "background push stays gentle at 16")
        finally:
            shutil.rmtree(base, ignore_errors=True)

    def test_sync_library_runs_foreground(self):
        base = Path(tempfile.mkdtemp())
        try:
            lib = base / "roms"; lib.mkdir(); (lib / "g.rom").write_bytes(b"R")
            args = self._run_push(base, ["roms\t%s" % lib], "sync-library",
                                  list_flag="--list-library-items")
            self.assertIn("--transfers 32", args, "'Sync library now' is manual -> foreground")
        finally:
            shutil.rmtree(base, ignore_errors=True)


class CloudServerPickerOffline(unittest.TestCase):
    """Server-table + key-rejection + the RCLONE_CONFIG_* export mechanism - all rclone-free
    (list-servers/set-server validate before touching rclone; the export test injects a stub
    rclone), so unlike CloudEngineBasics these RUN on CI and guard the picker from regressions.
    """

    def setUp(self):
        self.state = Path(tempfile.mkdtemp())
        self.env = dict(os.environ, DECK_CLOUD_STATE_DIR=str(self.state),
                        DECK_CLOUD_CREDS=str(self.state / "no-such-creds"))

    def tearDown(self):
        shutil.rmtree(self.state, ignore_errors=True)

    def _cloud(self, *args, expect_rc=None):
        p = subprocess.run([str(CLOUD), *args], env=self.env,
                           capture_output=True, text=True, timeout=60)
        if expect_rc is not None:
            self.assertEqual(p.returncode, expect_rc, p.stderr)
        return p

    def test_lists_eight_servers_with_one_current(self):
        rows = [l.split("\t") for l in self._cloud("list-servers").stdout.splitlines()
                if "\t" in l]
        self.assertEqual(len(rows), 8, rows)
        current = [r for r in rows if len(r) == 5 and r[4] == "1"]
        self.assertEqual(len(current), 1, "exactly one server must be current")
        self.assertEqual(current[0][0], "global", "the default current server is global")

    def test_set_server_rejects_unknown(self):
        p = self._cloud("set-server", "atlantis", "--no-probe")
        self.assertNotEqual(p.returncode, 0, "an unknown server key must be rejected")

    def test_categories_list_and_toggle(self):
        rows = [l.split("\t") for l in self._cloud("list-categories").stdout.splitlines()
                if "\t" in l]
        self.assertEqual(len(rows), 13, rows)   # 4 Tier A + 9 Tier B (roms, romsint, openbor, ...)
        self.assertTrue(all(r[3] == "1" for r in rows if len(r) == 4), "all default ON")
        self._cloud("set-category", "emu", "off", expect_rc=0)
        state = {r[1]: r[3] for r in
                 (l.split("\t") for l in self._cloud("list-categories").stdout.splitlines())
                 if len(r) == 4}
        self.assertEqual(state.get("emu"), "0", "emu now off")
        self.assertEqual(state.get("saves"), "1", "saves still on")
        self.assertNotEqual(self._cloud("set-category", "bogus", "on").returncode, 0)

    def test_progress_parser(self):
        import sys as _sys
        _sys.path.insert(0, str(ROOT))
        from lib.madsrv.cloud_cmds import _parse_progress
        j = ('{"level":"notice","msg":"x","stats":{"bytes":50,"totalBytes":100,"speed":10.0,'
             '"eta":5,"transferring":[{"name":"a.bin","bytes":5,"size":10,"percentage":50,'
             '"speed":2.0}]}}')
        prog, disp = _parse_progress(j)
        self.assertIsNotNone(prog)
        self.assertEqual(prog["overall_pct"], 50)
        self.assertEqual(prog["transfers"][0]["name"], "a.bin")
        self.assertIn("50%", disp)
        # a plain engine log line passes straight through (no progress)
        p, d = _parse_progress("[cloud] backing up")
        self.assertIsNone(p)
        self.assertEqual(d, "[cloud] backing up")

    def test_chosen_server_endpoint_reaches_rclone_env(self):
        # The whole switching mechanism: prove the chosen server actually reaches rclone as
        # RCLONE_CONFIG_S4_ENDPOINT/_REGION (with the region PAIRED to the endpoint). A stub
        # 'rclone' dumps its environment; 'probe' invokes it with the resolved server.
        base = Path(tempfile.mkdtemp())
        try:
            state = base / "state"; state.mkdir(parents=True)
            creds = base / "creds"
            creds.write_text("aws_access_key_id=AK\naws_secret_access_key=SK\n")
            dump = base / "rclone-env.txt"
            stub = base / "rclone"
            stub.write_text('#!/usr/bin/env bash\nenv > "' + str(dump) + '"\nexit 0\n')
            stub.chmod(0o755)
            env = dict(os.environ, HOME=str(base), DECK_CLOUD_STATE_DIR=str(state),
                       DECK_CLOUD_CREDS=str(creds), DECK_CLOUD_RCLONE=str(stub))

            def run(*a):
                return subprocess.run([str(CLOUD), *a], env=env, capture_output=True,
                                      text=True, timeout=60)

            run("set-server", "barcelona", "--no-probe")
            run("probe")  # invokes the stub rclone, which dumps its environment
            text = dump.read_text()
            self.assertIn("RCLONE_CONFIG_S4_ENDPOINT=https://s3.eu-barcelona.megas4.com", text)
            self.assertIn("RCLONE_CONFIG_S4_REGION=eu-barcelona", text)
        finally:
            shutil.rmtree(base, ignore_errors=True)


class CloudTransferControl(unittest.TestCase):
    """Interrupted-transfer marker lifecycle + pause/resume/stop/cancel + cloud.active, driven
    directly against cloud_cmds with a stub engine (plain bash - no rclone needed). The stream
    thread writes protocol JSON to sys.stdout, so the stream tests redirect it away."""

    def setUp(self):
        import sys as _sys
        _sys.path.insert(0, str(ROOT))
        from lib.madsrv import cloud_cmds
        self.cc = cloud_cmds
        self.base = Path(tempfile.mkdtemp())
        self.prev = os.environ.get("DECK_CLOUD_STATE_DIR")
        os.environ["DECK_CLOUD_STATE_DIR"] = str(self.base / "state")

    def tearDown(self):
        import io, contextlib
        with contextlib.redirect_stdout(io.StringIO()):
            try:
                self.cc._cloud_cancel({})   # kill any leftover stub stream
            except Exception:
                pass
            self._wait_idle()
        if self.prev is None:
            os.environ.pop("DECK_CLOUD_STATE_DIR", None)
        else:
            os.environ["DECK_CLOUD_STATE_DIR"] = self.prev
        shutil.rmtree(self.base, ignore_errors=True)

    def _stub(self, body):
        s = self.base / "engine.sh"
        s.write_text("#!/usr/bin/env bash\n" + body + "\n")
        s.chmod(0o755)
        return s

    def _wait_idle(self, timeout=6):
        import time
        end = time.time() + timeout
        while time.time() < end and self.cc._cloud_active({}).get("running"):
            time.sleep(0.05)
        time.sleep(0.15)   # let run()'s finally release _RUN_ACTIVE after clearing _ACTIVE

    def _wait_running(self, timeout=4):
        # wait until the child PROCESS is actually spawned + alive (cloud.active flips 'running'
        # the instant _stream_op sets _ACTIVE, which can be before the thread spawns the child).
        import time
        end = time.time() + timeout
        while time.time() < end:
            s = self.cc._ACTIVE.get("stream")
            if s is not None and s._proc is not None and s._proc.poll() is None:
                time.sleep(0.15)   # let it run past 'echo go' into the sleep
                return
            time.sleep(0.05)

    def test_marker_and_title_helpers(self):
        self.assertIsNone(self.cc._read_marker())
        self.cc._write_marker(["push-precious", "--force"])
        self.assertEqual(self.cc._read_marker(), ["push-precious", "--force"])
        self.assertFalse(self.cc._is_restore(["push-precious"]))
        self.assertTrue(self.cc._is_restore(["restore-library", "roms", "--to-live"]))
        self.assertEqual(self.cc._op_title(["sync-library"]), "Syncing library")
        self.cc._clear_marker()
        self.assertIsNone(self.cc._read_marker())

    def test_active_reports_pending_restore(self):
        # a leftover restore marker -> cloud.active flags pending_restore (for the confirm modal)
        self.cc._write_marker(["restore-library", "roms", "--to-live"])
        act = self.cc._cloud_active({})
        self.assertFalse(act["running"])
        self.assertTrue(act["pending"])
        self.assertTrue(act["pending_restore"])

    def test_clean_finish_clears_marker(self):
        import io, contextlib
        stub = self._stub("echo hi\nexit 0")
        with contextlib.redirect_stdout(io.StringIO()):
            r = self.cc._stream_op([str(stub), "push-precious", "--force"])
            self.assertIn("stream", r)
            self._wait_idle()
        self.assertIsNone(self.cc._read_marker(), "a clean finish clears the marker")

    def test_stop_keeps_cancel_clears(self):
        import io, contextlib
        with contextlib.redirect_stdout(io.StringIO()):
            self.cc._stream_op([str(self._stub("echo go\nsleep 30")), "sync-library"])
            self._wait_running()
            self.assertTrue(self.cc._cloud_active({})["running"])
            self.cc._cloud_stop({})      # STOP keeps the marker
            self._wait_idle()
        self.assertIsNotNone(self.cc._read_marker(), "stop keeps the marker (resumable)")
        with contextlib.redirect_stdout(io.StringIO()):
            self.cc._stream_op([str(self._stub("echo go\nsleep 30")), "sync-library"])
            self._wait_running()
            self.cc._cloud_cancel({})    # CANCEL clears it
            self._wait_idle()
        self.assertIsNone(self.cc._read_marker(), "cancel clears the marker")

    def test_pause_resume_signals(self):
        import io, contextlib
        with contextlib.redirect_stdout(io.StringIO()):
            self.cc._stream_op([str(self._stub("echo go\nsleep 30")), "sync-library"])
            self._wait_running()
            paused = self.cc._cloud_pause({})
            resumed = self.cc._cloud_resume({})
            self.cc._cloud_cancel({})
            self._wait_idle()
        self.assertTrue(paused["paused"], "pause reports paused")
        self.assertFalse(resumed["paused"], "resume clears paused")


if __name__ == "__main__":
    unittest.main()
