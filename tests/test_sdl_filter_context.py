"""Context-aware Deck-pad hide (lib/sdl_filter._hide_deck_when_external).

DOCKED reads HIDE_DECK_PAD_WHEN_EXTERNAL (default on); HANDHELD reads
HIDE_DECK_PAD_WHEN_EXTERNAL_HANDHELD (default off = keep the Deck). Back-compat: an install with
only the docked key keeps docked behaviour and gains keep-the-Deck handheld with no migration.

Run:  python3 -m unittest tests.test_sdl_filter_context -v
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lib import install_conf, sdl_filter


class SdlFilterContext(unittest.TestCase):
    def setUp(self):
        self.conf = Path(tempfile.mktemp())
        self._saved = os.environ.get("MAD_INSTALL_CONF")
        os.environ["MAD_INSTALL_CONF"] = str(self.conf)

    def tearDown(self):
        os.environ.pop("MAD_FORCE_CONTEXT", None)
        if self._saved is None:
            os.environ.pop("MAD_INSTALL_CONF", None)
        else:
            os.environ["MAD_INSTALL_CONF"] = self._saved
        try:
            self.conf.unlink()
        except OSError:
            pass

    def _hide(self, ctx):
        os.environ["MAD_FORCE_CONTEXT"] = ctx
        return sdl_filter._hide_deck_when_external()

    def test_defaults_absent_file(self):
        self.assertTrue(self._hide("docked"))       # docked hides by default (today's behaviour)
        self.assertFalse(self._hide("handheld"))    # handheld keeps the Deck by default

    def test_backcompat_docked_key_only(self):
        install_conf.set_value("HIDE_DECK_PAD_WHEN_EXTERNAL", "1")
        self.assertTrue(self._hide("docked"))
        self.assertFalse(self._hide("handheld"))    # no migration: handheld still keeps

    def test_docked_off(self):
        install_conf.set_value("HIDE_DECK_PAD_WHEN_EXTERNAL", "0")
        self.assertFalse(self._hide("docked"))
        self.assertFalse(self._hide("handheld"))

    def test_handheld_override_on(self):
        install_conf.set_value("HIDE_DECK_PAD_WHEN_EXTERNAL_HANDHELD", "1")
        self.assertTrue(self._hide("handheld"))     # explicit on -> hide handheld too
        self.assertTrue(self._hide("docked"))       # docked default on, independent axis

    def test_nonutf8_conf_degrades(self):
        # REGRESSION (review blocker): a corrupt / non-UTF-8 install.conf must NOT throw at launch
        # (it used to propagate UnicodeDecodeError into the SDL-filter launch callers); degrade to defaults.
        self.conf.write_bytes(b"HIDE_DECK_PAD_WHEN_EXTERNAL=1\n\xff\xfe not utf-8\n")
        self.assertTrue(self._hide("docked"))        # docked default (hide)
        self.assertFalse(self._hide("handheld"))     # handheld default (keep)

    def test_optout_undocked_resolves_docked(self):
        # REGRESSION (review blocker): a user who never enabled on-the-go, physically undocked, must
        # resolve to DOCKED (today's hide-the-Deck), NOT the handheld keep-the-Deck path.
        os.environ.pop("MAD_FORCE_CONTEXT", None)
        with mock.patch("lib.policy.load_merged", lambda: {"handheld": {"enabled": False}}):
            self.assertTrue(sdl_filter._hide_deck_when_external())   # opt-out -> docked -> hide (default)


if __name__ == "__main__":
    unittest.main()
