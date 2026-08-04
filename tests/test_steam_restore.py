"""Steam-category restore safety + the full backup->restore round-trip.

Pins: _steam_restore_target's two namespaces and every refusal reason (forged rels,
shortcut_missing when the appid vanished from Steam, appid_mismatch when the live
launcher renumbered, gamedir containment), the per-GAME running guard (AppId=<appid>
on Steam's reaper cmdline - never "close Steam"), and a real tmp-tree round-trip
through backup_game_assets/restore_game_assets with the rule-5 snapshot landing
beside compatdata.

Run: python3 -m unittest tests.test_steam_restore -v
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lib import game_files, granular_backup as gb, proc_guard, steam_shortcuts as ss

APPID = 4108777888


def _providers(home: Path, live: bool = True, launcher_appid=APPID, game_dir=None):
    """Patch steam_shortcuts' path/lookup providers onto a fake $HOME tree."""
    shortcuts = {APPID: {"name": "The Punisher", "exe": "", "start_dir": ""}} if live else {}
    croot = home / ".local/share/Steam/steamapps/compatdata"
    return (mock.patch.object(ss, "home", return_value=home),
            mock.patch.object(ss, "compatdata_root", return_value=croot),
            mock.patch.object(ss, "nonsteam_shortcuts", return_value=shortcuts),
            mock.patch.object(ss, "launcher_appid", return_value=launcher_appid),
            mock.patch.object(ss, "game_dir", return_value=game_dir))


class SteamRestoreTarget(unittest.TestCase):
    def _target(self, rel, stem="Punisher", **kw):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            croot = home / ".local/share/Steam/steamapps/compatdata"
            (croot / str(APPID)).mkdir(parents=True)
            gd = home / "games" / "OutRun2006"
            gd.mkdir(parents=True)
            kw.setdefault("game_dir", gd)
            ps = _providers(home, **kw)
            with ps[0], ps[1], ps[2], ps[3], ps[4]:
                target, root, why = gb._steam_restore_target(rel, stem)
            return target, root, why, str(croot.resolve()), str(gd.resolve())

    def test_compatdata_target_resolves_under_the_prefix_root(self):
        t, root, why, croot, _ = self._target(
            f"steam/compatdata/{APPID}/pfx/drive_c/users/steamuser/Documents")
        self.assertEqual(why, "")
        self.assertEqual(root, croot)                        # snapshot beside the prefixes
        self.assertTrue(t.startswith(croot + "/"))
        self.assertIn(f"/{APPID}/pfx/", t + "/")

    def test_gamedir_target_resolves_inside_the_live_game_dir(self):
        t, root, why, _, gd = self._target("steam/gamedir/games/OutRun2006/data")
        self.assertEqual(why, "")
        self.assertTrue(t.startswith(gd))
        self.assertEqual(root, str(Path(t).parent))          # $HOME-parent-unwritable fix

    def test_the_game_folder_row_itself_restores(self):
        # Review 2026-08-04 (confirmed by round trip): the gamedir group backs up ONE
        # folder row whose rel IS the bound, so its restore resolves to target == root.
        # Refusing that equality made the "Game Folder" restore a silent no-op - 22 GB
        # backed up that could never come back.
        t, root, why, _, gd = self._target("steam/gamedir/games/OutRun2006")
        self.assertEqual(why, "")
        self.assertEqual(t, gd)
        self.assertEqual(root, gd)                           # snapshot beside the folder

    def test_forged_rels_are_refused(self):
        for rel in (f"steam/compatdata/{APPID}x/pfx",        # non-digit appid
                    "steam/compatdata",                       # no appid at all
                    "steam/elsewhere/x",                      # unknown namespace
                    "steam/gamedir"):                         # no path
            _, _, why = self._target(rel)[:3]
            self.assertIn(why, ("unsafe_path", "target_escapes_root"), rel)

    def test_vanished_shortcut_is_shortcut_missing(self):
        # Steam does NOT guarantee a recreated shortcut keeps its appid: refuse, and
        # the user restores the launcher first / recreates the shortcut.
        _, _, why = self._target(f"steam/compatdata/{APPID}/pfx", live=False)[:3]
        self.assertEqual(why, "shortcut_missing")

    def test_renumbered_launcher_is_appid_mismatch(self):
        _, _, why = self._target(f"steam/compatdata/{APPID}/pfx",
                                 launcher_appid=APPID + 5)[:3]
        self.assertEqual(why, "appid_mismatch")

    def test_gamedir_outside_the_live_dir_is_refused(self):
        _, _, why = self._target("steam/gamedir/other/place")[:3]
        self.assertEqual(why, "outside_game_dir")

    def test_gamedir_without_a_live_launcher_is_refused(self):
        _, _, why = self._target("steam/gamedir/games/OutRun2006",
                                 launcher_appid=None)[:3]
        self.assertEqual(why, "shortcut_missing")

    def test_compatdata_without_a_live_launcher_is_refused_too(self):
        # Symmetric with gamedir: the appid merely existing in shortcuts.vdf does not
        # prove THIS backup's game still owns it.
        _, _, why = self._target(f"steam/compatdata/{APPID}/pfx",
                                 launcher_appid=None)[:3]
        self.assertEqual(why, "shortcut_missing")

    def test_prefix_restores_into_the_library_that_holds_it(self):
        # Steam creates a prefix in whichever library is the default install target, so
        # the target (and the rule-5 snapshot beside it) must follow the prefix to the SD
        # library. Assuming the home root would write a second, dead prefix in $HOME -
        # and put the snapshot on a different filesystem from the target.
        ss._LIBRARY_CACHE["entry"] = (None, [])
        try:
            with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as sd:
                home, extra = Path(tmp), Path(sd)
                vdf = home / ".local/share/Steam/steamapps/libraryfolders.vdf"
                vdf.parent.mkdir(parents=True, exist_ok=True)
                vdf.write_text('"libraryfolders"\n{\n\t"0"\n\t{\n\t\t"path"\t\t"%s"\n\t}\n}\n'
                               % extra)
                sd_croot = extra / "steamapps" / "compatdata"
                (sd_croot / str(APPID)).mkdir(parents=True)
                ps = _providers(home, game_dir=home / "games")
                with ps[0], ps[1], ps[2], ps[3], ps[4]:
                    t, root, why = gb._steam_restore_target(
                        f"steam/compatdata/{APPID}/pfx/drive_c", "Punisher")
                self.assertEqual(why, "")
                self.assertEqual(root, str(sd_croot.resolve()))
                self.assertTrue(t.startswith(str(sd_croot.resolve()) + "/"), t)
                self.assertFalse(t.startswith(str(home.resolve()) + "/"), t)
        finally:
            ss._LIBRARY_CACHE["entry"] = (None, [])

    def test_nonascii_digit_appid_is_refused_not_crashed(self):
        # str.isdigit() accepts U+00B2; int() rejects it. The guard must refuse, never
        # raise (an uncaught ValueError would abort the whole restore).
        _, _, why = self._target("steam/compatdata/4\u00b2/pfx")[:3]
        self.assertEqual(why, "unsafe_path")


class UnmountedLibrary(unittest.TestCase):
    """The library_unmounted guard: a prefix whose recorded holding library is
    registered in libraryfolders.vdf but absent on disk must REFUSE, not silently
    write a second, dead prefix into $HOME. Every other hint shape behaves as before:
    "home", None (old manifest), a deregistered library, or a mounted library."""

    def _vdf(self, home: Path, *libs):
        vdf = home / ".local/share/Steam/steamapps/libraryfolders.vdf"
        vdf.parent.mkdir(parents=True, exist_ok=True)
        body = "".join('\t"%d"\n\t{\n\t\t"path"\t\t"%s"\n\t}\n' % (i, p)
                       for i, p in enumerate(libs))
        vdf.write_text('"libraryfolders"\n{\n' + body + '}\n')

    def _target(self, home: Path, croot_hint, home_prefix: bool = False, clib_hint=None):
        if home_prefix:
            (home / ".local/share/Steam/steamapps/compatdata" / str(APPID)).mkdir(
                parents=True, exist_ok=True)
        ss._LIBRARY_CACHE["entry"] = (None, [])
        try:
            ps = _providers(home, game_dir=home / "games")
            with ps[0], ps[2], ps[3], ps[4]:   # real compatdata_root() derives from home()
                return gb._steam_restore_target(
                    f"steam/compatdata/{APPID}/pfx/drive_c", "Punisher",
                    croot_hint=croot_hint, clib_hint=clib_hint)
        finally:
            ss._LIBRARY_CACHE["entry"] = (None, [])

    def test_registered_but_absent_library_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            gone = home / "sdcard"                       # listed in the vdf, NOT on disk
            self._vdf(home, gone)
            t, root, why = self._target(
                home, str(gone / "steamapps" / "compatdata"))
            self.assertEqual((t, root, why), (None, None, "library_unmounted"))

    def test_a_deregistered_library_falls_back_to_home(self):
        # The library is gone from libraryfolders.vdf: the user removed it for good,
        # so the restore lands where Steam now creates prefixes - the home root.
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            self._vdf(home)                              # vdf lists nothing extra
            t, _, why = self._target(
                home, str(home / "oldcard" / "steamapps" / "compatdata"))
            self.assertEqual(why, "")
            self.assertTrue(t.startswith(str(home.resolve()) + "/"))

    def test_a_home_hint_and_no_hint_behave_as_before(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            self._vdf(home)
            for hint in ("home", None):
                t, _, why = self._target(home, hint)
                self.assertEqual(why, "", hint)
                self.assertTrue(t.startswith(str(home.resolve()) + "/"), hint)

    def test_a_live_home_prefix_wins_over_a_stale_sd_hint(self):
        # The game was re-run under Proton since the backup: the prefix EXISTS in the
        # home library now, so the hint is stale and the live resolution wins.
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            gone = home / "sdcard"
            self._vdf(home, gone)
            t, _, why = self._target(
                home, str(gone / "steamapps" / "compatdata"), home_prefix=True)
            self.assertEqual(why, "")
            self.assertIn("/.local/share/Steam/steamapps/compatdata/", t)

    def test_a_mounted_library_with_the_prefix_gone_falls_back(self):
        # The card is here but the prefix was deleted from it: nothing to wait for,
        # restore where Steam would recreate it.
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            sd = home / "sdcard"
            (sd / "steamapps" / "compatdata").mkdir(parents=True)   # mounted, no prefix
            self._vdf(home, sd)
            t, _, why = self._target(home, str(sd / "steamapps" / "compatdata"))
            self.assertEqual(why, "")
            self.assertTrue(t.startswith(str(home.resolve()) + "/"))

    def test_a_symlink_spelled_library_still_refuses_after_a_reboot(self):
        # Review 2026-08-04: a vdf entry can be a SYMLINK to the SD mountpoint (the
        # pre-SteamOS-3.5 mmcblk0p1 compat links). The marker records the RESOLVED
        # root while mounted; after a card-out reboot the symlink itself is gone
        # (/run is tmpfs), so nothing bridges the two spellings and the realpath
        # match alone silently missed - the recorded RAW spelling (clib) closes it.
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            link = home / "run" / "mmcblk0p1"                 # the vdf's spelling
            mount = home / "run" / "sd_mount"                 # what it resolved to
            link.parent.mkdir(parents=True)
            self._vdf(home, link)
            resolved_croot = str(mount / "steamapps" / "compatdata")
            # card out AND rebooted: neither the mountpoint nor the symlink exists
            t, root, why = self._target(home, resolved_croot, clib_hint=str(link))
            self.assertEqual((t, root, why), (None, None, "library_unmounted"))

    def test_an_absolute_home_spelling_is_treated_as_home(self):
        # Review 2026-08-04: only a foreign/hand-edited manifest spells the home root
        # absolutely (our writer emits "home") - the boot drive can never be
        # unmounted, so this must restore, not refuse, on a fresh Deck whose home
        # compatdata does not exist yet.
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            self._vdf(home)
            t, _, why = self._target(
                home, str(home / ".local/share/Steam/steamapps/compatdata"))
            self.assertEqual(why, "")
            self.assertTrue(t.startswith(str(home.resolve()) + "/"))

    def test_backup_records_the_holding_root_on_compatdata_items(self):
        # plan_game_assets stamps croot on every steam/compatdata item using the REAL
        # compatdata_root_marker against the live layout: "home" when the prefix lives
        # (or would be created) in the home library, the absolute croot when it lives
        # on another library. gamedir items carry no croot.
        from lib import backup_manifest, game_files

        def canned(system, stem, systems=None, **kw):
            base = f"steam/compatdata/{APPID}"
            return [
                {"key": "saves", "label": "Saves", "category": "steam", "present": True,
                 "size": 1, "files": [{"src": "/x/docs", "kind": "folder", "size": 1,
                                       "rel": f"{base}/pfx/drive_c/users/steamuser/Documents"}]},
                {"key": "gamedir", "label": "Game Folder", "category": "steam",
                 "present": True, "size": 1,
                 "files": [{"src": "/x/gd", "kind": "folder", "size": 1,
                            "rel": "steam/gamedir/Games/Punisher"}]},
            ]

        for holding in ("home", "sd"):
            with tempfile.TemporaryDirectory() as tmp:
                home = Path(tmp)
                sd = home / "sdcard"
                self._vdf(home, sd)
                croot = (home / ".local/share/Steam/steamapps/compatdata") \
                    if holding == "home" else (sd / "steamapps" / "compatdata")
                (croot / str(APPID)).mkdir(parents=True)
                want = "home" if holding == "home" else str(
                    Path(os.path.realpath(str(sd))) / "steamapps" / "compatdata")
                ss._LIBRARY_CACHE["entry"] = (None, [])
                try:
                    with mock.patch.object(ss, "home", return_value=home), \
                         mock.patch.object(game_files, "resolve_game_assets", canned), \
                         mock.patch.object(gb, "es_gamelist_record",
                                           return_value={"name": "The Punisher"}):
                        m, plan = gb.plan_game_assets(
                            [{"system": "steam", "stem": "Punisher",
                              "keys": ["saves", "gamedir"]}], "20260804-000000")
                finally:
                    ss._LIBRARY_CACHE["entry"] = (None, [])
                by_rel = {it["rel"]: it
                          for it in backup_manifest.items(m, "steam", "steam")}
                compat = [it for r, it in by_rel.items()
                          if r.startswith("steam/compatdata/")]
                self.assertTrue(compat, holding)
                for it in compat:
                    self.assertEqual(it.get("croot"), want, (holding, it["rel"]))
                    if holding == "sd":
                        # the raw vdf spelling rides along (clib) so a symlink-spelled
                        # library still matches after a card-out reboot
                        self.assertEqual(it.get("clib"), os.path.normpath(str(sd)),
                                         it["rel"])
                    else:
                        self.assertNotIn("clib", it, "home carries no spelling")
                gd = by_rel.get("steam/gamedir/Games/Punisher")
                self.assertIsNotNone(gd, holding)
                self.assertNotIn("croot", gd, "gamedir items carry no croot")


class SymlinkEscape(unittest.TestCase):
    """THE write-side containment invariant. Every Proton prefix ships symlinked dirs
    (pfx/dosdevices/z: -> /, c: -> ../drive_c, d:/e: -> /run/media/...), so LEXICAL
    containment is not enough: a forged or foreign manifest rel walking THROUGH one
    resolves outside the prefix while still starting with the root string, and both the
    restore write AND rule-5's move-aside would land there."""

    def _target(self, rel, extra=None):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            croot = home / ".local/share/Steam/steamapps/compatdata"
            cd = croot / str(APPID)
            dos = cd / "pfx/dosdevices"
            dos.mkdir(parents=True)
            (dos / "z:").symlink_to("/")                     # the real Proton layout
            (cd / "pfx/drive_c").mkdir(parents=True)
            (dos / "c:").symlink_to("../drive_c")
            gd = home / "games" / "OutRun2006"
            (gd / "data").mkdir(parents=True)
            (gd / "escape").symlink_to("/etc")
            ps = _providers(home, game_dir=gd)
            with ps[0], ps[1], ps[2], ps[3], ps[4]:
                return gb._steam_restore_target(rel, "Punisher")

    def test_traversal_through_the_z_symlink_is_refused(self):
        t, _r, why = self._target(f"steam/compatdata/{APPID}/pfx/dosdevices/z:/etc/passwd")
        self.assertIsNone(t)
        self.assertEqual(why, "target_escapes_root")

    def test_traversal_through_a_gamedir_symlink_is_refused(self):
        t, _r, why = self._target("steam/gamedir/games/OutRun2006/escape/shadow")
        self.assertIsNone(t)
        self.assertEqual(why, "outside_game_dir")

    def test_the_symlink_ITSELF_still_restores(self):
        # Restoring the z: link (a legitimate backup item) writes AT its own location -
        # its parent is a real dir inside the prefix - so it must be allowed.
        t, _r, why = self._target(f"steam/compatdata/{APPID}/pfx/dosdevices/z:")
        self.assertEqual(why, "")
        self.assertTrue(t.endswith("/pfx/dosdevices/z:"), t)

    def test_a_path_under_the_c_symlink_resolves_inside_the_prefix(self):
        # c: -> ../drive_c stays INSIDE the prefix, so this is not an escape.
        t, _r, why = self._target(f"steam/compatdata/{APPID}/pfx/dosdevices/c:/save.dat")
        self.assertEqual(why, "")
        self.assertIn("/drive_c/", t)

    def test_lutrisprefix_is_bounded_by_the_live_lutris_prefix(self):
        from lib import lutris_games as lg
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            pfx = home / "Games" / "tf"
            (pfx / "drive_c/users/deck").mkdir(parents=True)
            (pfx / "dosdevices").mkdir()
            (pfx / "dosdevices" / "z:").symlink_to("/")
            ps = _providers(home)
            with ps[0], ps[1], ps[2], ps[3], ps[4], \
                 mock.patch.object(ss, "lutris_game_id", return_value=117), \
                 mock.patch.object(lg, "prefix_for", return_value=pfx.resolve()):
                ok = gb._steam_restore_target(
                    "steam/lutrisprefix/Games/tf/drive_c/users/deck/Documents", "Deadpool")
                escape = gb._steam_restore_target(
                    "steam/lutrisprefix/Games/tf/dosdevices/z:/etc/x", "Deadpool")
                outside = gb._steam_restore_target(
                    "steam/lutrisprefix/Games/other/x", "Deadpool")
        t, root, why = ok
        self.assertEqual(why, "")
        self.assertTrue(t.startswith(str(pfx.resolve()) + "/"))
        self.assertEqual(root, str(pfx.resolve()))      # rule-5 snapshot beside ~/Games
        self.assertEqual(escape[2], "outside_game_dir") # symlink traversal refused
        self.assertEqual(outside[2], "outside_game_dir")

    def test_lutriscfg_restores_into_the_lutris_games_dir(self):
        from lib import lutris_games as lg
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            data = home / "lutris-data"
            (data / "games").mkdir(parents=True)
            ps = _providers(home)
            with ps[0], ps[1], ps[2], ps[3], ps[4], \
                 mock.patch.object(lg, "data_dir", return_value=data):
                t, root, why = gb._steam_restore_target(
                    "steam/lutriscfg/deadpool-1.yml", "Deadpool")
                bad = gb._steam_restore_target("steam/lutriscfg/evil.sh", "Deadpool")
        self.assertEqual(why, "")
        self.assertTrue(t.endswith("/lutris-data/games/deadpool-1.yml"))
        self.assertEqual(root, str((data / "games").resolve()))
        self.assertEqual(bad[2], "unsafe_path")   # only .yml files belong there

    def test_lutrisprefix_refuses_when_the_prefix_is_gone(self):
        from lib import lutris_games as lg
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            home.mkdir(parents=True)
            ps = _providers(home)
            with ps[0], ps[1], ps[2], ps[3], ps[4], \
                 mock.patch.object(ss, "lutris_game_id", return_value=117), \
                 mock.patch.object(lg, "prefix_for", return_value=None):
                _t, _r, why = gb._steam_restore_target(
                    "steam/lutrisprefix/Games/tf/x", "Deadpool")
        self.assertEqual(why, "lutris_prefix_missing")

    def test_gamedir_bound_falls_back_to_the_lutris_game_dir(self):
        # A Lutris shortcut's Steam StartDir is /usr/bin (useless); the bound comes
        # from the Lutris config instead.
        from lib import lutris_games as lg
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            gd = home / "games" / "Deadpool"
            gd.mkdir(parents=True)
            ps = _providers(home, game_dir=None)      # the SHORTCUT gives no game dir
            with ps[0], ps[1], ps[2], ps[3], ps[4], \
                 mock.patch.object(ss, "lutris_game_id", return_value=117), \
                 mock.patch.object(lg, "game_dir_for", return_value=gd.resolve()):
                t, root, why = gb._steam_restore_target(
                    "steam/gamedir/games/Deadpool/save.dat", "Deadpool")
        self.assertEqual(why, "")
        self.assertTrue(t.endswith("/games/Deadpool/save.dat"))
        self.assertEqual(root, str(gd.resolve()))

    def test_gamedir_snapshot_is_anchored_at_the_game_dir(self):
        # NOT at the target's dirname: for a game folder that is a direct child of $HOME
        # that dirname is $HOME, whose parent (/home) is root-owned on SteamOS, and the
        # rule-5 snapshot would silently fail to be created.
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            gd = home / "OutRun"                             # direct child of $HOME
            gd.mkdir(parents=True)
            ps = _providers(home, game_dir=gd)
            with ps[0], ps[1], ps[2], ps[3], ps[4]:
                t, root, why = gb._steam_restore_target("steam/gamedir/OutRun/x.sav",
                                                        "Punisher")
        self.assertEqual(why, "")
        self.assertEqual(root, str(gd.resolve()))            # snapshot lands in $HOME
        self.assertNotEqual(root, str(home.resolve()))


class RunningGuard(unittest.TestCase):
    def test_steam_app_running_pattern_is_pgrep_ere(self):
        # POSIX ERE: no \b - the appid is anchored with (space|end) instead.
        with mock.patch.object(proc_guard, "process_running",
                               return_value=False) as pr:
            proc_guard.steam_app_running(4108)
        pr.assert_called_once_with(r"AppId=4108( |$)")

    def _manifest(self):
        m = gb.backup_manifest.new_manifest("granular", created="20260101T000000")
        gb.backup_manifest.add_item(
            m, category="steam", category_label="Steam (non-Steam games)",
            system="steam", system_label="Valve Steam",
            item=gb.backup_manifest.make_item(
                id=f"steam/compatdata/{APPID}/pfx", name="The Punisher",
                src="/x", rel=f"steam/compatdata/{APPID}/pfx", kind="folder",
                size=1, stem="Punisher",
                extra={"game": "steam:Punisher", "asset": "prefix"}))
        return m

    def test_running_game_refuses_restore(self):
        with mock.patch.object(proc_guard, "steam_app_running", return_value=True):
            with self.assertRaisesRegex(RuntimeError, "The Punisher"):
                gb._refuse_running_steam(self._manifest(),
                                         [("steam", f"steam/compatdata/{APPID}/pfx")])

    def test_idle_game_passes(self):
        with mock.patch.object(proc_guard, "steam_app_running", return_value=False):
            gb._refuse_running_steam(self._manifest(),
                                     [("steam", f"steam/compatdata/{APPID}/pfx")])


class RoundTrip(unittest.TestCase):
    """backup_game_assets -> restore_game_assets over a real tmp compatdata tree."""

    def test_prefix_and_saves_round_trip_with_rule5_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            croot = home / ".local/share/Steam/steamapps/compatdata"
            cd = croot / str(APPID)
            docs = cd / "pfx/drive_c/users/steamuser/Documents"
            docs.mkdir(parents=True)
            (docs / "save.dat").write_text("original save")
            (cd / "version").write_text("9")
            (cd / "pfx.lock").write_text("")
            dest = Path(tmp) / "backups"
            dest.mkdir()
            games = [{"system": "steam", "stem": "Punisher",
                      "keys": ["saves", "prefix"]}]
            nsg = {"Punisher": {"appid": APPID, "rgid": 1, "alive": True,
                                "name": "The Punisher", "sh": "/x/Punisher.sh"}}
            lines: list = []

            def emit(ev):
                lines.append(ev)

            ps = _providers(home)
            with ps[0], ps[1], ps[2], ps[3], ps[4], \
                 mock.patch.object(ss, "nonsteam_games", return_value=nsg), \
                 mock.patch.object(ss, "is_lutris", return_value=False), \
                 mock.patch.object(ss, "compatdata_dir", return_value=cd), \
                 mock.patch.object(gb, "es_gamelist_record",
                                   return_value={"name": "The Punisher"}), \
                 mock.patch.object(proc_guard, "steam_app_running", return_value=False):
                out = gb.backup_game_assets(games, str(dest), "20260101T000000",
                                            emit, lambda: False)
                backupdir = Path(out["path"])
                # pfx.lock must not be in the backup
                self.assertFalse(
                    (backupdir / f"steam/compatdata/{APPID}/pfx.lock").exists())
                self.assertTrue(
                    (backupdir / f"steam/compatdata/{APPID}/pfx/drive_c/users/"
                                 "steamuser/Documents/save.dat").is_file())

                # damage the live save, then restore
                (docs / "save.dat").write_text("corrupted")
                res = gb.restore_game_assets(str(backupdir), games,
                                             "20260102T000000", emit, lambda: False)

            self.assertGreaterEqual(res["restored"], 1)
            self.assertGreaterEqual(res["replaced"], 1)
            self.assertEqual((docs / "save.dat").read_text(), "original save")
            # rule-5 snapshot beside compatdata (same fs, outside every scan tree)
            snaps = list(croot.parent.glob(gb.SNAPSHOT_PREFIX + "*"))
            self.assertTrue(snaps, "no rule-5 snapshot beside compatdata")
            self.assertEqual(res["restart_scope"], "none")

    def test_restore_refuses_while_the_game_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            cd = home / ".local/share/Steam/steamapps/compatdata" / str(APPID)
            (cd / "pfx").mkdir(parents=True)
            (cd / "version").write_text("9")
            dest = Path(tmp) / "backups"
            dest.mkdir()
            games = [{"system": "steam", "stem": "Punisher", "keys": ["prefix"]}]
            nsg = {"Punisher": {"appid": APPID, "rgid": 1, "alive": True,
                                "name": "The Punisher", "sh": "/x/P.sh"}}
            ps = _providers(home)
            with ps[0], ps[1], ps[2], ps[3], ps[4], \
                 mock.patch.object(ss, "nonsteam_games", return_value=nsg), \
                 mock.patch.object(ss, "is_lutris", return_value=False), \
                 mock.patch.object(ss, "compatdata_dir", return_value=cd), \
                 mock.patch.object(gb, "es_gamelist_record",
                                   return_value={"name": "The Punisher"}):
                out = gb.backup_game_assets(games, str(dest), "20260101T000000",
                                            lambda ev: None, lambda: False)
                with mock.patch.object(proc_guard, "steam_app_running",
                                       return_value=True):
                    with self.assertRaisesRegex(RuntimeError, "The Punisher"):
                        gb.restore_game_assets(Path(out["path"]).as_posix(), games,
                                               "20260102T000000", lambda ev: None,
                                               lambda: False)


class PlanParity(unittest.TestCase):
    """Cloud parity is structural: cloud.push_game_assets calls the SAME
    plan_game_assets - pin the plan rels once and both transports agree."""

    def test_plan_rels_are_the_restore_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            cd = home / ".local/share/Steam/steamapps/compatdata" / str(APPID)
            docs = cd / "pfx/drive_c/users/steamuser/Documents"
            docs.mkdir(parents=True)
            (docs / "s.dat").write_text("x")
            (cd / "version").write_text("9")
            games = [{"system": "steam", "stem": "Punisher",
                      "keys": ["saves", "prefix"]}]
            nsg = {"Punisher": {"appid": APPID, "rgid": 1, "alive": True,
                                "name": "The Punisher", "sh": "/x/P.sh"}}
            ps = _providers(home)
            with ps[0], ps[1], ps[2], ps[3], ps[4], \
                 mock.patch.object(ss, "nonsteam_games", return_value=nsg), \
                 mock.patch.object(ss, "is_lutris", return_value=False), \
                 mock.patch.object(ss, "compatdata_dir", return_value=cd), \
                 mock.patch.object(gb, "es_gamelist_record",
                                   return_value={"name": "The Punisher"}):
                _m, plan = gb.plan_game_assets(games, "20260101T000000")
        rels = sorted(p["rel"] for p in plan)
        base = f"steam/compatdata/{APPID}"
        self.assertIn(f"{base}/pfx", rels)                # the full-prefix folder row
        self.assertIn(f"{base}/version", rels)
        self.assertNotIn(f"{base}/pfx.lock", rels)
        # NESTED-REL DEDUPE: the saves rows live INSIDE the ticked pfx folder, so they are
        # not planned again - the steamuser dirs are copied (and counted) exactly once.
        self.assertNotIn(f"{base}/pfx/drive_c/users/steamuser/Documents", rels)
        for p in plan:
            self.assertEqual(p["category"], "steam")

    def test_saves_only_selection_still_plans_the_save_rows(self):
        # Without the prefix ticked there is no ancestor, so the saves rows plan normally.
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            cd = home / ".local/share/Steam/steamapps/compatdata" / str(APPID)
            docs = cd / "pfx/drive_c/users/steamuser/Documents"
            docs.mkdir(parents=True)
            (docs / "s.dat").write_text("x")
            games = [{"system": "steam", "stem": "Punisher", "keys": ["saves"]}]
            nsg = {"Punisher": {"appid": APPID, "rgid": 1, "alive": True,
                                "name": "The Punisher", "sh": "/x/P.sh"}}
            ps = _providers(home)
            with ps[0], ps[1], ps[2], ps[3], ps[4], \
                 mock.patch.object(ss, "nonsteam_games", return_value=nsg), \
                 mock.patch.object(ss, "is_lutris", return_value=False), \
                 mock.patch.object(ss, "compatdata_dir", return_value=cd), \
                 mock.patch.object(gb, "es_gamelist_record",
                                   return_value={"name": "The Punisher"}):
                _m, plan = gb.plan_game_assets(games, "20260101T000000")
        self.assertEqual([p["rel"] for p in plan],
                         [f"steam/compatdata/{APPID}/pfx/drive_c/users/steamuser/Documents"])


if __name__ == "__main__":
    unittest.main()
