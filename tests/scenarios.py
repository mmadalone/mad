"""
Shared (sdl, devs, pins) fixture battery for the pad-assignment golden tests.

Each scenario is hardware-agnostic: it lists the SDL devices present (by class)
and which player ports are pinned. ``build()`` turns a scenario into the exact
``(sdl, devs, pins)`` a backend ``assign()`` receives — ``devs`` mirror ``sdl``
one-for-one (same vid:pid, path = /dev/input/eventN) so a pin to ``devs[i]``
resolves back to ``sdl[i]`` via ``sdl_index_of``/``class_index``.

The battery is corner-heavy on PURPOSE — it includes the two latent divergences
found while reading the code so the golden diff makes any behaviour change
visible:
  * pin_over_manage  — pcsx2 resolves pins WITHOUT the manage guard, so an
    over-manage pin suppresses the handheld fallback; xemu/eden/rpcs3 don't.
  * pin_two_to_same  — pcsx2's interleaved drop vs eden/rpcs3's batch drop
    differ when two players pin the SAME physical pad.
And the xemu pin-collision bug "D":
  * pin_p2_lone_ds5  — one pad pinned to port2: xemu (buggy) leaves it on port1
    too; the fix drops port1.
  * pin_p2_two_ds5   — two identical pads, pin one to port2: the fix MUST keep
    both ports (legitimate two-identical-pads case).
"""
from __future__ import annotations

from tests._fakes import dev, sd

# Device classes (vid:pid, SDL name). DS5/DS4 are the PlayStation pad_classes;
# DECK is the handheld_class; BIT is a non-PlayStation pad.
DECK = "28de:1205"
DS5 = "054c:0ce6"
DS4 = "054c:09cc"
BIT = "2dc8:6101"

_NAME = {
    DECK: "Steam Deck Controller",
    DS5: "DualSense Wireless Controller",
    DS4: "DualShock4 Wireless Controller",
    BIT: "8BitDo Pro 2",
}

# pad_classes (priority order) + handheld_class used by every test backend cfg.
PAD_CLASSES = [DS5, DS4]
HANDHELD = DECK
MANAGE = 2


def _guid(vidpid: str) -> str:
    """Deterministic 32-hex SDL GUID, identical for the same class (what xemu
    keys on) — two same-class pads share it by construction."""
    return (vidpid.replace(":", "") * 8)[:32]


# Each scenario: (name, [class, ...] for sdl in index order, {port: devs_index})
SCENARIOS = [
    ("deck_only",        [DECK],            {}),
    ("one_ps",           [DECK, DS5],       {}),
    ("two_distinct",     [DECK, DS5, DS4],  {}),
    ("two_identical",    [DECK, DS5, DS5],  {}),
    ("non_ps_no_deck",   [BIT],             {}),
    ("empty_sdl",        [],                {}),
    ("pin_p1_ds4",       [DECK, DS5, DS4],  {1: 2}),
    ("pin_p2_lone_ds5",  [DECK, DS5],       {2: 1}),
    ("pin_p2_two_ds5",   [DECK, DS5, DS5],  {2: 2}),
    ("pin_over_manage",  [DECK],            {5: 0}),
    ("pin_swap",         [DECK, DS5, DS4],  {1: 2, 2: 1}),
    ("pin_two_to_same",  [DECK, DS5, DS4],  {1: 1, 2: 1}),
]


def build(classes, pins_by_port):
    """Return (sdl, devs, pins) for a scenario spec."""
    sdl = [sd(i, c, _guid(c), _NAME[c]) for i, c in enumerate(classes)]
    devs = [dev(c, f"/dev/input/event{i}", _NAME[c]) for i, c in enumerate(classes)]
    pins = {port: devs[idx] for port, idx in pins_by_port.items()}
    return sdl, devs, pins
