"""Guard: every LONE-section tile the MAD grid can emit must be openable.

GuiMadPageStandalones::open() "collapses" a tile/member that has exactly one
section (and it is not a "toggle") by opening that section directly instead of
showing a one-row chooser. Two dispatchers back that collapse:
  * the free madOpenStandaloneTarget() (GuiMadPageStandaloneSections.cpp) -- most kinds; and
  * explicit special-cases in open() itself for kinds that carry extra payload the
    free function never receives (settings_pergame_menu -> game-first menu leaves;
    grid -> sub-grid tilesJson).
If a builder emits a lone section whose kind is in NEITHER, the free function's
if/else-if chain matches nothing, no page is pushed, and the tile is a silent
no-op on the Deck (this is exactly how the On-the-go "Per-system" grid tile and
the gridified Per-game tiles broke). The golden tests are blind to it (they diff
structure, never dispatch), so this asserts the contract directly.

When this fails: either wire the new kind into GuiMadPageStandalones::open()'s
collapse (and rebuild the fork) or stop emitting it as a lone tile -- then add the
kind here. Keep HANDLED in sync with the two C++ dispatchers.
"""
from __future__ import annotations

import unittest

from lib.madsrv import onthego_cmds as og
from lib.madsrv import retroarch_settings as rs
from lib.madsrv import standalones_cmds as sc
from tests._menu_capture import hermetic

# Kinds madOpenStandaloneTarget() dispatches (GuiMadPageStandaloneSections.cpp:48-113).
_FREE_DISPATCH = {
    "settings", "settings_pergame", "input_pergame", "pads_pergame", "input_map",
    "pads_map", "pads_hide", "gamepad", "model2", "daphne_map", "lindbergh_map",
    "lindbergh_pads", "input_pergame_menu", "retroarch_input", "ra_profiles", "bezels",
    "racontrollers", "ra_systems", "ra_systems_handheld", "priority_scopes",
}
# Kinds GuiMadPageStandalones::open() special-cases in the collapse itself (they
# carry payload the free function cannot receive).
_COLLAPSE_SPECIAL = {"settings_pergame_menu", "grid"}
# Per-game leaf kinds: the collapse routes a lone non-toggle section through
# GuiMadPageStandaloneSections::openLeaf, which dispatches these (they carry the picked game's
# titleid in ctxVal). They appear as lone tiles in the tiled per-game menus (built at runtime, not
# in the three list surfaces below), but openLeaf handles them, so the model lists them here.
_PERGAME_LEAF = {"pergame_settings", "pergame_pads", "pergame_input", "pergame_priority",
                 "pergame_lindbergh_pads", "pergame_lindbergh_map"}
HANDLED = _FREE_DISPATCH | _COLLAPSE_SPECIAL | _PERGAME_LEAF


def _lone_kinds(tiles) -> set:
    """Every kind that reaches open()'s single-section collapse: a tile/member whose
    `sections` has exactly one entry and that entry is not a toggle (mirrors the C++
    guard `secs.size()==1 && secs.front().kind != "toggle"`)."""
    out = set()

    def walk(t):
        secs = t.get("sections")
        if isinstance(secs, list) and len(secs) == 1 and secs[0].get("kind") != "toggle":
            out.add(secs[0].get("kind"))
        # Descend into a grid section's own sub-tiles (the On-the-go Per-system console grid): the
        # len==1 collapse above reaches the grid row itself but never the per-system tiles it holds,
        # so without this the tiled per-system leaf choosers (Wii U / PS2 / ...) go uncovered.
        for s in secs or []:
            if s.get("kind") == "grid" and isinstance(s.get("sections"), list):
                for sub in s["sections"]:
                    walk(sub)
        for m in t.get("members", []) or []:
            walk(m)

    for t in tiles:
        walk(t)
    return out


class LoneSectionDispatch(unittest.TestCase):
    def test_every_lone_section_kind_is_dispatchable(self):
        with hermetic():   # forces every curated system/emulator present -> maximal tile coverage
            emitted = (_lone_kinds(sc._standalones_list({})["tiles"])
                       | _lone_kinds(og._list({})["tiles"])
                       | _lone_kinds(rs._ra_hub_tiles()))
        undispatchable = emitted - HANDLED
        self.assertEqual(
            undispatchable, set(),
            f"lone-section kind(s) {sorted(undispatchable)} reach GuiMadPageStandalones::open()'s "
            f"collapse but are dispatched by neither madOpenStandaloneTarget nor an open() "
            f"special-case -> the tile silently opens nothing on the Deck. Wire it into the C++ "
            f"collapse (and rebuild), or stop emitting it as a lone tile.")


if __name__ == "__main__":
    unittest.main()
