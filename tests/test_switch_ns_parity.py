"""Frozen RPC namespace surface for the Switch fork pages (Citron / Eden / Ryujinx dock).

The C++ panel addresses these pages by bare namespace strings (GuiMadPageEmuSettings
concatenates ns + ".get" etc.), so the clone-consolidation refactors must not add, drop
or rename a single method. This fence imports every module that registers into the owned
prefixes and compares the EXACT set (equality, not subset: a rename shows up as one name
dropped plus one stray added). It must stay green through every fold.

Run: python3 -m unittest tests.test_switch_ns_parity -v
"""
import unittest

from lib.madsrv import (rpc, yuzu_profile_cmds,  # noqa: F401  (register the namespaces)
                        citron_addons_cmds, citron_cheats_cmds, citron_dock_cmds,
                        citron_games, citron_hotkeys_cmds, citron_pergame,
                        citron_pg_input_cmds, citron_settings,
                        eden_addons_cmds, eden_cheats_cmds, eden_cmds, eden_dock_cmds,
                        eden_hotkeys_cmds, eden_pergame, eden_pg_input_cmds, eden_settings,
                        ryujinx_dock_cmds)

_PREFIXES = ("citron.", "citron_", "eden.", "eden_", "ryujinx_dock.")


def _gs(*names):
    """<ns>.get + <ns>.set for each namespace."""
    return [f"{n}.{v}" for n in names for v in ("get", "set")]


FROZEN = frozenset(
    ["citron.games", "eden.games"]
    + _gs(
        # MAD-owned dock toggles (one per fork, incl. Ryujinx)
        "citron_dock", "eden_dock", "ryujinx_dock",
        # add-ons / cheats / per-game input profiles
        "citron_addons", "citron_cheats", "citron_pg_input",
        "eden_addons", "eden_cheats", "eden_pg_input",
        # global settings pages (Eden has the extra gfxext page by design)
        "citron_general", "citron_system", "citron_cpu",
        "citron_gfx", "citron_gfxadv", "citron_audio",
        "eden_general", "eden_system", "eden_cpu",
        "eden_gfx", "eden_gfxadv", "eden_gfxext", "eden_audio",
        # per-game settings pages
        "citron_pg_system", "citron_pg_cpu", "citron_pg_gfx",
        "citron_pg_gfxadv", "citron_pg_audio", "citron_pg_linux",
        "eden_pg_system", "eden_pg_cpu", "eden_pg_gfx",
        "eden_pg_gfxadv", "eden_pg_gfxext", "eden_pg_audio", "eden_pg_linux",
        # input profile pickers (yuzu_profile_cmds registers all four)
        "citron_input_docked", "citron_input_handheld",
        "eden_input_docked", "eden_input_handheld")
    + [f"{ns}.{v}" for ns in ("citron_hk", "eden_hk")
       for v in ("input_get", "input_set", "input_clear", "input_save", "input_cancel")]
)   # 2 + 39*2 + 10 = 90 methods


class SwitchNamespaceParity(unittest.TestCase):
    def test_exact_method_set(self):
        got = {n for n in rpc._METHODS if n.startswith(_PREFIXES)}
        missing, stray = FROZEN - got, got - FROZEN
        self.assertEqual(
            (missing, stray), (set(), set()),
            "the Switch RPC surface changed: the C++ panel and the menu goldens address "
            "these names as bare strings, so a fold/refactor must keep them byte-identical "
            f"(missing={sorted(missing)}, stray={sorted(stray)})")

    def test_all_slow(self):
        # Every page method is registered slow=True today (worker pool, not the stdin
        # thread). Pinned so a fold cannot silently drop the flag.
        for n in sorted(FROZEN):
            self.assertTrue(rpc._METHODS[n][1], f"{n} lost slow=True")

    def test_none_cached(self):
        # None of these opt into the staterev response cache today (the hotkey pages are
        # buffered; the buffer is the source of truth). A fold must not add cache= deps.
        for n in sorted(FROZEN):
            self.assertEqual(rpc._METHODS[n][2], (), f"{n} gained cache deps")

    def test_hotkey_buffers_are_per_fork(self):
        # Staged hotkey edits must never cross forks: each module owns its own InputBuffer.
        self.assertIsNot(citron_hotkeys_cmds._buf, eden_hotkeys_cmds._buf)

    def test_dock_notes_byte_stable(self):
        # The three dock notes are one template + the fork display name. Pinned
        # byte-for-byte BEFORE the dock fold so the fold's templating provably
        # reproduces them.
        for ns, name in (("citron_dock", "Citron"), ("eden_dock", "Eden"),
                         ("ryujinx_dock", "Ryujinx")):
            note = rpc._METHODS[f"{ns}.get"][0]({})["note"]
            self.assertEqual(
                note,
                f"Automatically set {name}'s docked/handheld mode at launch to match "
                "your setup: docked (an external screen is connected) uses a 1080p base, "
                "handheld (the Steam Deck's built-in screen) uses a 720p base. Detected "
                "from the physical display; if the On-the-go feature is off it falls "
                "back to controller presence. Turn off to keep whatever you set inside "
                f"{name}.")


if __name__ == "__main__":
    unittest.main()
