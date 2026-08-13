"""Every backup group's EXPLAIN text must agree with whether it is actually pre-ticked.

Why this file exists: on 2026-08-13 Miquel restored a Cemu backup and asked what it
actually contained. It turned out fifteen of the sixteen groups whose description said
"off by default" were in fact pre-ticked ON - including PCSX2 textures (~39 GB) and
Dolphin's Load folder (~33 GB) - and emu_map's own header comment asserted the opposite
of its own data ("OPT-IN groups (default_ticked False)"). Nothing was broken by it: the
backups were MORE complete than advertised, which is the safe direction. But the panel
was telling the user one thing and doing another about multi-gigabyte uploads, and the
only reason it surfaced was somebody reading a manifest by hand.

Settled: every group is ticked by default, big ones included, because a backup that
silently omits expensive-to-rebuild data is the failure mode that matters. This file
fences the DESCRIPTIONS against the FLAGS so the two cannot drift apart again - in
either direction, since a future opt-out group must say so in its own text.

Run:  python3 -m unittest tests.test_group_defaults -v
"""
from __future__ import annotations

import unittest

from lib import emu_map


def _group_defs() -> list:
    """Every (emulator, group key, default flag) triple in the shipped table."""
    out = []
    for e in emu_map.EMULATORS:
        for g in e.get("groups", []):
            out.append((e["key"], g["key"], bool(g.get("default"))))
    return out


class ExplainMatchesDefault(unittest.TestCase):
    """The words the user reads must match the tick they get."""

    # Phrases that promise the group is NOT pre-ticked. A description carrying one of
    # these while the flag says True is the exact 2026-08-13 defect.
    OFF_CLAIMS = ("off by default", "opt-in", "opt in", "not backed up by default")

    def test_no_group_claims_off_while_ticked_on(self):
        offenders = []
        for emu, gkey, dflt in _group_defs():
            meta = emu_map.GROUP_INFO.get(gkey) or {}
            explain = str(meta.get("explain", "")).lower()
            if dflt and any(c in explain for c in self.OFF_CLAIMS):
                offenders.append(f"{emu}.{gkey}")
        self.assertEqual(
            offenders, [],
            "these groups are PRE-TICKED but their explain text says they are not: "
            + ", ".join(offenders)
            + " - fix the text in emu_map.GROUP_INFO, or flip the group's 'default'")

    def test_no_group_claims_ticked_while_off(self):
        # The mirror: an opt-out group must not advertise itself as ticked.
        offenders = []
        for emu, gkey, dflt in _group_defs():
            meta = emu_map.GROUP_INFO.get(gkey) or {}
            explain = str(meta.get("explain", "")).lower()
            if not dflt and "ticked by default" in explain:
                offenders.append(f"{emu}.{gkey}")
        self.assertEqual(
            offenders, [],
            "these groups are NOT pre-ticked but their explain says they are: "
            + ", ".join(offenders))


class BigGroupsAreTicked(unittest.TestCase):
    """The settled policy: nothing expensive-to-rebuild is silently skipped."""

    BIG = ("shader", "textures", "mods", "wii_nand", "hdd", "states")

    def test_every_heavyweight_group_is_ticked_by_default(self):
        off = [f"{emu}.{gkey}" for emu, gkey, dflt in _group_defs()
               if gkey in self.BIG and not dflt]
        self.assertEqual(
            off, [],
            "settled 2026-08-13: the heavyweight groups ship TICKED so a backup is "
            "complete unless the user deliberately unticks. These are off: " + ", ".join(off))

    def test_the_same_group_key_is_consistent_across_emulators(self):
        # pcsx2.textures was ticked while pcsx2x6.textures was not, for no stated reason.
        # A group key means the same thing everywhere or the leaf teaches the user wrong.
        seen: dict = {}
        for emu, gkey, dflt in _group_defs():
            seen.setdefault(gkey, {}).setdefault(dflt, []).append(emu)
        split = {g: v for g, v in seen.items() if len(v) > 1}
        self.assertEqual(
            split, {},
            "these group keys default differently depending on the emulator: "
            + "; ".join(f"{g}: " + " vs ".join(f"{d}={','.join(e)}" for d, e in v.items())
                        for g, v in split.items()))


class EveryGroupHasAnExplain(unittest.TestCase):
    def test_no_group_reaches_the_panel_without_a_description(self):
        # The leaf shows explain text beside every row; a missing one renders blank and
        # gives the user nothing to decide a multi-GB tick on.
        missing = sorted({gkey for _, gkey, _ in _group_defs()
                          if not str((emu_map.GROUP_INFO.get(gkey) or {}).get("explain", "")).strip()})
        self.assertEqual(missing, [], f"group keys with no explain text: {missing}")


if __name__ == "__main__":
    unittest.main()
