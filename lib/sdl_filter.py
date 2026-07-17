"""
SDL device-filter helper for emulators that bind controllers by raw SDL
enumeration order (Supermodel: JOY1/JOY2) and cannot pin a pad by config.

The trick (already used by `supermodel-native.sh`, and the SDL-standard
mechanism) is `SDL_GAMECONTROLLER_IGNORE_DEVICES_EXCEPT` — a whitelist of
`0xVID/0xPID` pairs. By keeping ONLY the chosen player pads visible to SDL, the
emulator sees them as JOY1/JOY2 in order, with every other device (Steam Deck
virtual pad, Sinden, other controllers) filtered out.

`keep_except_list()` builds that whitelist from the backend's `pad_classes`
(PS4 treated like DualSense), restricted to what's actually connected, falling
back to the handheld class when no player pad is present so the game stays
playable solo. Requires Steam Input OFF on the ES-DE shortcut (the user's setup)
so SDL sees the raw pad vid:pids the whitelist matches.
"""
from __future__ import annotations

from .devices import enumerate_devices, joypads, vidpid


# The "x-arcade" token in a backend's pad_classes means "the IDENTIFIED X-Arcade"
# (a 045e:02a1 at [hardware].xarcade_port), distinct from a raw "045e:02a1" (= any
# Xbox-looking 045e pad). SDL can only match by vid:pid, so the token maps to
# 045e:02a1 on the wire; the real distinction lives in PRESENCE (port-aware, below).
_XARCADE_TOKENS = ("x-arcade", "xarcade")

# The Steam Deck's built-in pad presents two ways: the real controller (28de:1205,
# raw evdev) and Steam's virtual gamepad (28de:11ff, "Microsoft X-Box 360 pad N").
# joypads() DROPS 28de:11ff from enumeration (Device.is_steam_virtual), so it never
# lands on a blocklist on its own — which is exactly why it used to leak into OpenBOR
# and steal a player slot. Both are listed here so the "hide the Deck pad once an
# external pad is connected" toggle can force them out explicitly.
DECK_PAD_CLASSES = ("28de:1205", "28de:11ff")
_DECK_VPS = set(DECK_PAD_CLASSES)
_TRUTHY = {"1", "on", "yes", "true", "auto"}


def _hide_deck_when_external() -> bool:
    """Whether the ES-DE -> Input Device Settings switch "hide the Steam Deck gamepad once
    an external pad is connected" is on. Stored as HIDE_DECK_PAD_WHEN_EXTERNAL in
    install.conf; DEFAULT ON — an absent file OR an absent key reads as on, matching the
    switch's default, so the OpenBOR fix applies on existing installs before the key is
    seeded. Only an explicit 0/off/no/false turns it off."""
    try:
        from . import install_conf
        return install_conf.get("HIDE_DECK_PAD_WHEN_EXTERNAL", "1").strip().lower() in _TRUTHY
    except Exception:
        return True


def _to_vidpid(c: str) -> str:
    """A pad_classes entry as the vid:pid SDL matches (x-arcade token -> 045e:02a1)."""
    return "045e:02a1" if c in _XARCADE_TOKENS else c


def _present_classes() -> set[str]:
    pads = joypads(enumerate_devices())
    present = {vidpid(d) for d in pads}
    # Mark the "x-arcade" token present ONLY when the user-identified X-Arcade is
    # actually connected (port match) — so a backend listing "x-arcade" routes the
    # real stick, not any random 045e pad. Loaded here so callers need no new args.
    try:
        from .routing import is_xarcade, xarcade_port
        from .policy import load_merged
        xport = xarcade_port(load_merged())
        if xport and any(is_xarcade(d, xport) for d in pads):
            present.add("x-arcade")
    except Exception:
        pass
    return present


def _fmt(classes) -> str:
    # "054c:09cc" -> "0x054c/0x09cc"; the x-arcade token -> 045e:02a1; dedup, order.
    out, seen = [], set()
    for c in classes:
        c = _to_vidpid(c)
        if c in seen:
            continue
        seen.add(c)
        vid, pid = c.split(":")
        out.append(f"0x{vid}/0x{pid}")
    return ",".join(out)


def handheld_allow(handheld_class: str) -> str:
    """Whitelist for a launch with NO player pads: just the configured handheld pad.

    Unlike keep_first_present/keep_except_list this does NOT gate on the pad being
    "present": the Deck's own pad is deliberately excluded from joypads()
    (is_steam_virtual), so it can never look present, and gating on that is why
    `handheld_class` was inert for openbor while a hardcoded literal did its job.
    The caller knows there are no player pads; the point is which pad to let in.

    Empty in -> empty out, and the CALLER must treat that as "use your own
    fallback": an empty SDL whitelist means HIDE EVERY PAD, never "allow all"."""
    return _fmt([handheld_class]) if handheld_class else ""


def keep_first_present(pad_classes, handheld_class: str = "") -> str:
    """Whitelist for a STRICT per-system priority chain: expose ONLY the first
    class in `pad_classes` that is connected (all of its devices — so a 2-side
    X-Arcade or two same-model pads still give P1+P2), else the handheld class,
    else "". Unlike keep_except_list (which exposes ALL listed pads at once, for
    fixed JOY1/JOY2 emulators like Supermodel), this guarantees the top-priority
    *present* family is the only thing the game sees — so it becomes Player 1
    regardless of SDL enumeration order. Used by the sdl_priority=true backends
    (hypseus/daphne); OpenBOR is sdl_priority=false and goes through keep_except_list().

    No "hide the Deck pad" guard here ON PURPOSE: a present player family already wins
    (the Deck is never returned), and the only Deck path is the solo-handheld fallback —
    which the toggle must NOT suppress, or handheld play would lose its controller."""
    present = _present_classes()
    for c in pad_classes:
        if c in present:
            return _fmt([c])
    if handheld_class and handheld_class in present:
        return _fmt([handheld_class])
    return ""


def ignore_nonplayers(pad_classes, handheld_class: str = "") -> str:
    """BLOCKLIST for SDL_GAMECONTROLLER_IGNORE_DEVICES — hide every connected pad
    that is NOT a configured PLAYER family (`pad_classes`).

    Semantics: Steam Deck pad, Sinden guns, and any device not in `pad_classes`
    drop out; the handheld pad (`handheld_class`) is kept ONLY when no real player
    pad is present. Empty string = nothing to hide.

    LIVE — do NOT "clean this up". Consumer: the router's `sdl-ignore-list` mode,
    which hypseus-pin.sh calls for daphne on every launch.

    ★ THE BLOCKLIST MECHANISM IS NOT DEAD WEIGHT — an earlier version of this
    docstring said it was, and that sentence caused a real outage. The whitelist
    (`_EXCEPT`) does win over this IGNORE list under Proton, but ONLY for ORDINARY
    pads: winebus EXEMPTS Steam's virtual Deck pad (28de:11ff), which walks straight
    past the whitelist and, holding the lowest node, steals port 0 and shifts every
    other player up a seat. An explicit blocklist is the ONLY thing that hides it.
    openbor.sh's blocklist was deleted as "dead code" on 2026-07-16 on exactly that
    reasoning and it BROKE docked seating; restored the same day (`1714eef`), and it
    now hardcodes the 28de pair on the merger path rather than calling this helper.
    28de:11ff EXISTS ONLY INSIDE GAME MODE, so a headless test will "prove" the
    whitelist sufficient and be wrong. See deck-docs/openbor.md, "winebus" section."""
    present = _present_classes()
    has_player = any(c in present for c in pad_classes)
    # Map tokens to vid:pid for the block test so the X-Arcade's 045e:02a1 is NOT
    # blocked when "x-arcade" is a player; only ever block real vid:pid entries.
    player_vps = {_to_vidpid(c) for c in pad_classes}
    block = [c for c in present if ":" in c and c not in player_vps]
    if not has_player and handheld_class:
        block = [c for c in block if c != handheld_class]   # solo: keep the handheld
    if has_player and _hide_deck_when_external():
        # Force BOTH Deck classes out even though joypads() filtered 28de:11ff from
        # `present`. This IS what hides the phantom Deck pad: the whitelist does not,
        # because winebus exempts 28de:11ff from it (see the docstring above).
        # Gated on has_player so solo/handheld play (no external) keeps its controller.
        block = list(block) + [c for c in DECK_PAD_CLASSES if c not in player_vps]
    return _fmt(sorted(block))


def keep_except_list(pad_classes, handheld_class: str = "",
                     keep_extra=()) -> str:
    """Whitelist string for SDL_GAMECONTROLLER_IGNORE_DEVICES_EXCEPT: the chosen
    player pads that are connected (+ keep_extra), or the handheld class if no
    player pad is present. Empty string if nothing relevant is connected (caller
    should then leave SDL unfiltered)."""
    present = _present_classes()
    keep = [c for c in pad_classes if c in present]
    has_player = bool(keep)
    if not keep and handheld_class and handheld_class in present:
        keep = [handheld_class]
    keep += [c for c in keep_extra if c in present]
    if has_player and _hide_deck_when_external():
        # An external player pad is present -> never expose the Deck pad (e.g. a Deck
        # class listed in keep_extra). When solo, `keep` is the handheld Deck fallback
        # and has_player is False, so we leave it untouched.
        keep = [c for c in keep if _to_vidpid(c) not in _DECK_VPS]
    return _fmt(keep)
