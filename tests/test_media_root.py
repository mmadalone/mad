"""
Tests for esde_settings.media_root() — the downloaded-media resolver added so the
maintenance CLIs stop hardcoding /run/media/deck/1tbDeck/downloaded_media.

Load-bearing: test_legacy_mediadirectory_unchanged proves that on a machine whose
ES-DE MediaDirectory points at the SD card, media_root() returns that EXACT path,
so the maintainer's tools behave byte-identically after parameterization.

Run: python3 -m unittest tests.test_media_root -v
"""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from lib import esde_settings

_KEYS = ("MAD_MEDIA_ROOT", "MAD_INSTALL_CONF", "ESDE_APPDATA_DIR", "ESDE_RESOURCES")


def _write_settings(appdata: Path, **kv: str) -> None:
    s = appdata / "settings"
    s.mkdir(parents=True, exist_ok=True)
    body = "".join(f'<string name="{k}" value="{v}" />\n' for k, v in kv.items())
    (s / "es_settings.xml").write_text('<?xml version="1.0"?>\n' + body, encoding="utf-8")


class MediaRoot(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in _KEYS}
        for k in _KEYS:
            os.environ.pop(k, None)
        self.tmp = Path(tempfile.mkdtemp())
        self.appdata = self.tmp / "ESDE"
        self.appdata.mkdir()
        self._orig = (esde_settings.APPDATA, esde_settings.SETTINGS)
        esde_settings.APPDATA = self.appdata
        esde_settings.SETTINGS = self.appdata / "settings" / "es_settings.xml"
        # Neutralise any real install.conf in the repo so it can't leak in.
        os.environ["MAD_INSTALL_CONF"] = str(self.tmp / "no-such-install.conf")

    def tearDown(self):
        esde_settings.APPDATA, esde_settings.SETTINGS = self._orig
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    # --- the non-regression guarantee ---
    def test_legacy_mediadirectory_unchanged(self):
        _write_settings(self.appdata, MediaDirectory="/run/media/deck/1tbDeck/downloaded_media")
        self.assertEqual(esde_settings.media_root(),
                         Path("/run/media/deck/1tbDeck/downloaded_media"))

    def test_default_when_unset(self):
        _write_settings(self.appdata, Theme="x")            # no MediaDirectory
        self.assertEqual(esde_settings.media_root(), self.appdata / "downloaded_media")

    def test_default_when_no_settings_file(self):
        self.assertEqual(esde_settings.media_root(), self.appdata / "downloaded_media")

    def test_env_override_wins(self):
        _write_settings(self.appdata, MediaDirectory="/run/media/deck/1tbDeck/downloaded_media")
        os.environ["MAD_MEDIA_ROOT"] = "/tmp/envmedia"
        self.assertEqual(esde_settings.media_root(), Path("/tmp/envmedia"))

    def test_install_conf_override(self):
        _write_settings(self.appdata, MediaDirectory="/run/media/deck/1tbDeck/downloaded_media")
        conf = self.tmp / "install.conf"
        conf.write_text("MEDIA_ROOT=/tmp/confmedia\n")
        os.environ["MAD_INSTALL_CONF"] = str(conf)
        self.assertEqual(esde_settings.media_root(), Path("/tmp/confmedia"))

    def test_env_beats_install_conf(self):
        conf = self.tmp / "install.conf"
        conf.write_text("MEDIA_ROOT=/tmp/confmedia\n")
        os.environ["MAD_INSTALL_CONF"] = str(conf)
        os.environ["MAD_MEDIA_ROOT"] = "/tmp/envmedia"
        self.assertEqual(esde_settings.media_root(), Path("/tmp/envmedia"))

    def test_home_token_expands(self):
        _write_settings(self.appdata, MediaDirectory="%HOME%/media")
        self.assertEqual(esde_settings.media_root(), Path.home() / "media")

    def test_tilde_expands(self):
        _write_settings(self.appdata, MediaDirectory="~/somemedia")
        self.assertEqual(esde_settings.media_root(), Path.home() / "somemedia")

    def test_espath_token_resolves_to_exe_dir(self):
        # ES-DE's real MediaDirectory token: %ESPATH% -> the ES-DE binary dir.
        exe = self.tmp / "usr" / "bin"
        exe.mkdir(parents=True)
        orig = esde_settings._esde_exe_dir
        esde_settings._esde_exe_dir = lambda: exe
        try:
            _write_settings(self.appdata, MediaDirectory="%ESPATH%/media")
            self.assertEqual(esde_settings.media_root(), exe / "media")
        finally:
            esde_settings._esde_exe_dir = orig

    def test_espath_falls_back_when_exe_dir_absent(self):
        # No discoverable ES-DE binary dir -> never return a literal-token path.
        orig = esde_settings._esde_exe_dir
        esde_settings._esde_exe_dir = lambda: None
        try:
            _write_settings(self.appdata, MediaDirectory="%ESPATH%/media")
            self.assertEqual(esde_settings.media_root(), self.appdata / "downloaded_media")
        finally:
            esde_settings._esde_exe_dir = orig

    def test_unknown_token_falls_back_to_default(self):
        _write_settings(self.appdata, MediaDirectory="%BOGUS%/media")
        self.assertEqual(esde_settings.media_root(), self.appdata / "downloaded_media")

    def test_esde_exe_dir_derived_from_resources(self):
        # .../usr/share/es-de/resources  ->  .../usr/bin
        res = self.tmp / "usr" / "share" / "es-de" / "resources"
        (res / "systems").mkdir(parents=True)
        (self.tmp / "usr" / "bin").mkdir(parents=True)
        os.environ["ESDE_RESOURCES"] = str(res)
        self.assertEqual(esde_settings._esde_exe_dir(), self.tmp / "usr" / "bin")


class ResolverLegacy(unittest.TestCase):
    """default==legacy lock for the OTHER resolvers the maintenance scripts route
    their destructive reads/writes through (rom_root / gamelists / RetroArch base).
    The whole Phase-2 'paths come from ES-DE, unchanged on the maintainer's machine'
    claim rests on these, so pin them."""

    def setUp(self):
        from lib import es_collections, retroarch_cfg
        self.es_collections = es_collections
        self.retroarch_cfg = retroarch_cfg
        self.tmp = Path(tempfile.mkdtemp())
        appdata = self.tmp / "ESDE"
        (appdata / "settings").mkdir(parents=True)
        # ROMDirectory empty == the maintainer's live config (the ~/ROMs fallback branch).
        (appdata / "settings" / "es_settings.xml").write_text(
            '<?xml version="1.0"?>\n<string name="ROMDirectory" value="" />\n')
        self._orig = (esde_settings.APPDATA, esde_settings.SETTINGS,
                      es_collections.ESDE, es_collections.SETTINGS)
        esde_settings.APPDATA = appdata
        esde_settings.SETTINGS = appdata / "settings" / "es_settings.xml"
        es_collections.ESDE = appdata
        es_collections.SETTINGS = appdata / "settings" / "es_settings.xml"
        es_collections.rom_root.cache_clear()

    def tearDown(self):
        (esde_settings.APPDATA, esde_settings.SETTINGS,
         self.es_collections.ESDE, self.es_collections.SETTINGS) = self._orig
        self.es_collections.rom_root.cache_clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_rom_root_empty_setting_is_home_roms(self):
        # The exact branch the maintainer's machine depends on.
        self.assertEqual(self.es_collections.rom_root(), Path.home() / "ROMs")

    def test_gamelists_under_appdata(self):
        self.assertEqual(esde_settings.APPDATA / "gamelists", self.tmp / "ESDE" / "gamelists")

    def test_retroarch_base_is_flatpak_config_retroarch(self):
        self.assertTrue(str(self.retroarch_cfg.RA_CONFIG_BASE.parent).endswith(
            "org.libretro.RetroArch/config/retroarch"))


if __name__ == "__main__":
    unittest.main()
