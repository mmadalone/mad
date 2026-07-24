"""Post-update reapply hardening (live incident 2026-07-24):

  * The PTY child that runs deck-post-update.sh under sudo MUST strip Steam's Game Mode overlay from
    LD_PRELOAD (_clean_env). The 32-bit gameoverlayrenderer.so can't load into 64-bit sudo, so ld.so
    floods the PTY with 'wrong ELF class' errors that drown sudo's prompt and make a CORRECT password
    read as rejected (the page looped 3x, "wrong password too many times").
  * The streamed log strips ANSI / terminal control noise (_strip_ansi), so pacman's colour + progress
    codes render as readable text, not "[?25l[1;34m..." garbage in the panel's plain renderer.

Run:  python3 -m unittest tests.test_postupdate_ptyenv -v
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.madsrv import postupdate_cmds as pu  # noqa: E402


class CleanEnv(unittest.TestCase):
    def _clean(self, ld):
        old = os.environ.get("LD_PRELOAD")
        try:
            if ld is None:
                os.environ.pop("LD_PRELOAD", None)
            else:
                os.environ["LD_PRELOAD"] = ld
            return pu._clean_env()
        finally:
            if old is None:
                os.environ.pop("LD_PRELOAD", None)
            else:
                os.environ["LD_PRELOAD"] = old

    def test_both_overlays_removed_entirely(self):
        env = self._clean(":/x/ubuntu12_32/gameoverlayrenderer.so:/x/ubuntu12_64/gameoverlayrenderer.so")
        self.assertNotIn("LD_PRELOAD", env)

    def test_keeps_unrelated_preloads(self):
        env = self._clean("/x/gameoverlayrenderer.so:/opt/mylib.so")
        self.assertIn("LD_PRELOAD", env)
        self.assertIn("mylib.so", env["LD_PRELOAD"])
        self.assertNotIn("gameoverlayrenderer", env["LD_PRELOAD"])

    def test_no_ld_preload_is_fine(self):
        env = self._clean(None)
        self.assertNotIn("LD_PRELOAD", env)  # nothing to strip, none added


class StripAnsi(unittest.TestCase):
    def test_strips_pacman_colour(self):
        self.assertEqual(pu._strip_ansi("\x1b[?25l\x1b[1;34m::\x1b[0;1m Sync"), ":: Sync")

    def test_strips_cursor_moves_and_clears(self):
        self.assertEqual(pu._strip_ansi("\x1b[3F core up to date\x1b[K\x1b[2E next"),
                         " core up to date next")

    def test_preserves_plain_text(self):
        s = "ERROR: ld.so: object '/x/gameoverlayrenderer.so' ... ELFCLASS32: ignored."
        self.assertEqual(pu._strip_ansi(s), s)

    def test_strips_carriage_return(self):
        self.assertEqual(pu._strip_ansi("progress 50%\r"), "progress 50%")


if __name__ == "__main__":
    unittest.main()
