"""CI helpers.

A handful of tests deliberately assert against the LIVE Steam Deck: which
emulators are installed, which systems have present games, and the contents of
live config files (e.g. retroarch.cfg). These are valuable on the Deck but cannot
pass on a bare CI runner, which has none of that state. Decorate them with
``@skip_on_ci`` so the launcher CI job (which sets ``MAD_CI=1``) skips them while
the Deck still runs them.

Do NOT use this to paper over a non-hermetic UNIT test — fix the test to isolate
its inputs instead (see e.g. the rpcs3 override-sidecar isolation in
tests/_harness.run). This is only for tests whose PURPOSE is to check the real
device.
"""
from __future__ import annotations

import os
import unittest

skip_on_ci = unittest.skipIf(
    bool(os.environ.get("MAD_CI")),
    "asserts against live Deck state (installed emulators / present games / live "
    "config); runs on the Deck, skipped under MAD_CI",
)
