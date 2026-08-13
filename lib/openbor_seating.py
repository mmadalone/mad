"""Which real pad becomes which OpenBOR / Ikemen-GO player -- the ONE seating decision.

Lifted out of mad-openbor-pads.py on 2026-08-13. The code is byte-identical to what it
replaced; only _listed's docstring gained a note. It lived there because the pad
MERGER is its only launch-time caller (openbor.sh, and mugen.sh via `--backend mugen`), but
the Preview page has to answer the same question read-only, and every time it answered by
re-deriving the rules it got a different answer than the launch. Three bugs came out of that
re-derivation, the last one reported on screen:

    Preview said  "P1 Xbox 360"                 (pad_classes order, token expanded blindly)
    the launch did "P1 DualSense, P2 Xbox 360"   (the cabinet was UNPLUGGED, so this is the
                                                 list-minus-the-token half of the rule alone)

So the rules get ONE home and both callers import it. The merger keeps the uinput twins, the
pump and the re-attach loop; nothing about them is here. This module creates no devices and
writes nothing, but it is not side-effect free: build_plan reads sysfs through usb_iface_num,
and sdl_filter._hide_deck_when_external reads install.conf and /sys/class/drm. Preview calls
it per request, so that cost is real, if small.

MAX_PADS is OpenBOR's JOY_LIST_TOTAL and belongs to the seating, not to the twins.
"""
from __future__ import annotations

import re

from lib import sdl_filter
from lib.devices import joypads, usb_iface_num, vidpid
from lib.openbor_maps import CLASS_OF_VIDPID
from lib.routing import is_xarcade

MAX_PADS = 4                       # OpenBOR's JOY_LIST_TOTAL


def class_of(dev) -> str | None:
    return CLASS_OF_VIDPID.get(vidpid(dev))


def _node_num(path: str) -> int:
    """The NUMERIC event-node index.

    Never sort these paths as strings: "event258" < "event30" lexically, so a
    string sort seats pads by collation instead of by node. That is not cosmetic
    — a pad's node number changes every time it reconnects (a DualSense that
    re-pairs can jump from event30 to event258), so string order reshuffled the
    player seats between launches. Observed on-device 2026-07-16: the same two
    DualSense pads took different seats on consecutive runs."""
    m = re.search(r"(\d+)$", path)
    return int(m.group(1)) if m else 1 << 30


def _listed(d, pad_classes, xport: str = "") -> bool:
    """Did the user list this pad's FAMILY on the Controllers page?

    The X-Arcade answers to either spelling: the base policy lists it by vid:pid
    (045e:02a1), MAD's own picker writes the "x-arcade" token, and sdl_filter
    already rules that the token simply IS that vid:pid (_to_vidpid). We resolve
    the same way rather than inventing a second, stricter meaning.

    Deliberately NOT gated on is_xarcade(d, xport): this asks which FAMILIES may
    play, not which device is the cabinet. Gating it cost the whole cabinet the
    moment the port identify went stale -- and re-cabling the stick is exactly
    what routing.is_xarcade's own docstring warns makes it stale. Both halves
    then dropped out of the plan, no merger ran, WL came back empty, and
    openbor.sh read that as HANDHELD and wrote the canonical map on a docked
    launch. Found by the 2026-07-17 review (test_a_stale_xarcade_identify_...).

    Consequence, on purpose: listing "x-arcade" also admits a genuine Xbox 360
    pad, because they are the same vid:pid and nothing but the port tells them
    apart. That is the pre-batch behaviour and what the picker's own labels
    promise; seat ORDER still uses the identify (build_plan) WHEN THERE IS ONE --
    with no port identified the cabinet-first rule cannot fire either, and both
    halves fall back to node order. NOTE this is the OPPOSITE call from sdl_filter's blocklist,
    which since 2026-08-13 does gate the token on the cabinet being present -- and
    the difference is deliberate. There, admitting a stray Xbox pad HANDS it the
    game while the pads you listed stay hidden. Here it only decides who may be
    seated, and the cabinet is still seated first."""
    vp = vidpid(d)
    return any(sdl_filter._to_vidpid(c) == vp for c in pad_classes)


def build_plan(devs, pad_classes, xport: str = "") -> list[tuple[object, str]]:
    """Real pads -> the ordered list whose index IS the OpenBOR player slot.

    Order is deterministic and ours (that is the whole point):
      1. the X-Arcade's halves by USB interface — :1.0 is P1, :1.1 is P2. Wine
         used to decide this and got it wrong at random; usb_iface_num is
         replug-stable, so the cabinet's own labelling now always wins.
      2. every other configured family, in `pad_classes` priority order, then
         by enumeration order within a family.
    A pad must be BOTH listed in `pad_classes` and translatable to play. Listing
    is the user's choice, made on MAD's "Player pad families" row, whose help
    says "Pads not listed are hidden from this emulator" — so unchecking one has
    to actually keep it out, or that row is lying. (It was: this used to filter
    on translatability alone, and an unchecked pad still took a seat.)
    Capped at MAX_PADS."""
    # joypads() drops every Steam-virtual pad (28de:11ff), and must keep doing so: the
    # router and the Preview have to ignore those phantoms. But for the MERGER that pad
    # is a real, mergeable player - it is how the Deck's own controls reach userspace in
    # Game Mode (the physical 28de:1205 exposes no gamepad node). Admit it here, and ONLY
    # when the user listed it in pad_classes, so no other consumer changes behaviour and
    # an unlisted Deck pad still takes no seat. Without this the handheld Deck had no
    # merger at all, so stick-as-d-pad only ever worked with an external pad connected.
    cands = joypads(devs) + [d for d in devs if d.is_steam_virtual and d.is_joypad
                             and _listed(d, pad_classes, xport)]
    pads = [d for d in cands
            if class_of(d) and _listed(d, pad_classes, xport)]
    # KEEP vs TAKEOVER, the same model Cemu uses (cemu_seat._seat_plan) and the SAME
    # switch: ES-DE -> Input Device Settings -> "no deckpad if external", which is
    # context-aware (docked defaults ON = hide, handheld defaults OFF = keep).
    #   KEEP (toggle off, or no external pad): the Deck is P1 and externals follow;
    #       28de:11ff leads pad_classes, so rank() seats it first.
    #   TAKEOVER (toggle on AND an external present): the Deck is not seated at all, so
    #       the externals compact from P1 and nothing steals a slot.
    # Read through sdl_filter rather than re-deriving it, so the merger's seating can
    # never disagree with the SDL whitelist the same launch builds.
    _ext = [d for d in pads if not d.is_steam_virtual]
    if _ext and len(_ext) != len(pads) and sdl_filter._hide_deck_when_external():
        pads = _ext
    xa = [d for d in pads if xport and is_xarcade(d, xport)]
    xa.sort(key=lambda d: (usb_iface_num(d.path) if usb_iface_num(d.path) is not None else 9,
                           _node_num(d.path)))
    rest = [d for d in pads if d not in xa]

    def rank(d):
        vp = vidpid(d)
        try:
            i = [c for c in pad_classes if c != "x-arcade"].index(vp)
        except ValueError:
            i = len(pad_classes)
        return (i, _node_num(d.path))

    rest.sort(key=rank)
    return [(d, class_of(d)) for d in (xa + rest)][:MAX_PADS]
