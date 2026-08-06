"""Detect when the deployed SCRIPTS are older than the ES-DE build expects.

THE PROBLEM. MAD ships in two halves that update independently: the C++ AppImage
updates itself through ES-DE's in-app updater, while the scripts on `main` arrive
only when someone runs `git pull` / install.sh by hand. Nothing enforces that they
match. At CI run 50 the panel shipped features whose backend methods were not yet
deployed, so those features simply errored on any Deck with older scripts.

THE CONTRACT. Two hand-authored integers, because the two branches share no counter:

  SCRIPTS_REV                       (tracked, branch `main`)  - what IS deployed
  MAD_SCRIPTS_MIN_REV               (branch `deck-patches`)   - what the binary NEEDS

The binary writes its value to `.esde-scripts-expect` in the launchers dir at
startup; this module reads both and compares. Deliberately NOT the VERSION file:
that is a human release marker bumped a handful of times ever, while this must bump
on every backend method the panel calls unconditionally. Conflating them guarantees
one of the two stops meaning anything.

Bump SCRIPTS_REV on `main` FIRST, then MAD_SCRIPTS_MIN_REV on `deck-patches`. CI
refuses to publish a build whose minimum exceeds what is on `main`.

THREE DELIBERATE SILENCES - each one is "we do not know", never "you are fine":

  no expect file      -> None. The running binary predates this feature, so it has
                         made no demand and we must not invent one. This is what
                         lets the scripts half ship dormant, ahead of the C++.
  unparseable         -> None. A corrupt file is not evidence of skew.
  scripts AHEAD       -> None. Pushing scripts before the binary is the REQUIRED
                         order; nagging about it would punish the discipline this
                         whole mechanism exists to create.

Only `expected > deployed` is a real finding.

MISSING FILE MEANS ZERO, ON PURPOSE. A checkout predating SCRIPTS_REV genuinely is
older than any build that demands one, so absent-reads-as-0 is correct, not a bug.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

LAUNCHERS = Path(__file__).resolve().parent.parent

# Overridable so tests never touch the real tree (the house pattern: MAD_POSTUPDATE_FLAG,
# DECK_POSTUPDATE_SCRIPT, DECK_CLOUD_LAUNCHERS_DIR).
REV_FILE = Path(os.environ.get("MAD_SCRIPTS_REV_FILE", str(LAUNCHERS / "SCRIPTS_REV")))
EXPECT_FILE = Path(os.environ.get("MAD_SCRIPTS_EXPECT_FILE",
                                  str(LAUNCHERS / ".esde-scripts-expect")))

# Stable line prefix. deck-post-update.sh --check prints this so it reaches the MAD
# panel's "Missing now:" list, and esde-health-check.sh filters on EXACTLY this prefix
# so the line can never arm .post-update-pending. Changing it means changing both.
SKEW_PREFIX = "Scripts older than this ES-DE build:"


def _read_int(path: Path) -> Optional[int]:
    """First whitespace-separated token as a non-negative int, else None."""
    try:
        raw = path.read_text(encoding="utf-8", errors="replace").split()
    except OSError:
        return None
    if not raw:
        return None
    try:
        value = int(raw[0])
    except ValueError:
        return None
    return value if value >= 0 else None


def scripts_rev() -> int:
    """What is deployed. Absent or unreadable counts as 0 - genuinely ancient."""
    value = _read_int(REV_FILE)
    return 0 if value is None else value


def expected_rev() -> Optional[int]:
    """What the running binary demands, or None when it has made no demand."""
    return _read_int(EXPECT_FILE)


def skew() -> Optional[tuple]:
    """(deployed, expected) when the scripts are behind, else None."""
    expected = expected_rev()
    if expected is None:
        return None
    deployed = scripts_rev()
    return (deployed, expected) if deployed < expected else None


def skew_line() -> Optional[str]:
    """One human line for --check, or None. Names the fix, not just the problem."""
    found = skew()
    if found is None:
        return None
    deployed, expected = found
    return (f"{SKEW_PREFIX} rev {deployed} < {expected} needed "
            f"- run install.sh from Desktop Mode to update them")


if __name__ == "__main__":
    line = skew_line()
    if line:
        print(line)
