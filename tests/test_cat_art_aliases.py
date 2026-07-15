"""Guard: every _CAT_ART_ALIAS entry must resolve to a real theme icon.

The category-tile icon map exists precisely because the theme's files use the
artist's shorthand (render-display.png, jvs-controls_.png, ...) that the auto-slug
does not match, so correctness depends on EXACT filenames. The menu golden cannot
see this: its stub resolve_art returns the first candidate verbatim without touching
disk, so a renamed/dropped shorthand icon would ship a blank tile with every test
still green. This checks the alias targets against the real active theme, so such a
regression fails loudly. Device-gated (needs the installed theme); skipped on CI.
"""
from __future__ import annotations

import unittest

from lib.madsrv import standalones_cmds as sc
from lib.madsrv.systems_cmds import console_art, resolve_art
from tests._ci import skip_on_ci


class CatArtAliases(unittest.TestCase):
    @skip_on_ci
    def test_every_alias_target_resolves(self):
        missing = []
        for slug, target in sc._CAT_ART_ALIAS.items():
            if target.startswith("console:"):
                hit = console_art(target[len("console:"):])
            else:
                hit = resolve_art([f"icons/{target}.png", f"{target}.png"])
            if not hit:
                missing.append((slug, target))
        self.assertEqual(
            missing, [],
            f"_CAT_ART_ALIAS target(s) do not resolve to any theme file: {missing} -- the shorthand "
            f"icon was renamed/removed, so those category tiles render blank. Fix the target or the "
            f"theme file (~/ES-DE/themes/pixel-es-de/router-config/icons/).")


if __name__ == "__main__":
    unittest.main()
