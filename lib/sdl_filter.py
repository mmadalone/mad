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
_XARCADE_VP = "045e:02a1"

# A whitelist that deliberately matches NO device. SDL has no "allow nothing" token and an
# empty string reads as "no opinion" to every caller (the shell wrappers only export a
# non-empty value), so "hide every pad" has to be spelled as a vid:pid nothing can have.
MATCH_NOTHING = "0x0000/0x0000"

# The Steam Deck's built-in pad presents two ways: the real controller (28de:1205,
# raw evdev) and Steam's virtual gamepad (28de:11ff, "Microsoft X-Box 360 pad N").
# joypads() DROPS 28de:11ff from enumeration (Device.is_steam_virtual), so it never
# lands on a blocklist on its own — which is exactly why it used to leak into OpenBOR
# and steal a player slot. Both are listed here so the "hide the Deck pad once an
# external pad is connected" toggle can force them out explicitly.
DECK_PAD_CLASSES = ("28de:1205", "28de:11ff")
_DECK_VPS = set(DECK_PAD_CLASSES)
_TRUTHY = {"1", "on", "yes", "true", "auto"}


def _undocked() -> bool:
    """Is the Deck PHYSICALLY in your hands? The external-display check (plus the
    MAD_FORCE_CONTEXT test hook, which deck_state consults first). Fail-safe TRUE.

    Two deliberate departures from how every other module asks this question, because this
    one decides whether the player has a controller at all rather than which layout to load:

    * NOT handheld_input.context(), which _hide_deck_when_external below uses: that first
      requires the on-the-go FEATURE to be enabled and answers "docked" for anyone who never
      opted in, even with the Deck in their hands. Right for a seating PREFERENCE, wrong here.
    * NOT deck_state.resolve_force([handheld]), the pattern ~15 other modules use --
      lib/daphne_input.py among them, which is worth knowing because it means Daphne's own
      keymap swap answers this question differently from its pad filter. That folds in the
      On-the-go page's "Detection" control -- whose options read "Auto (physical display) /
      Force handheld / Force docked" and which people set for a stable watt cap and
      resolution. Picking "Force docked" must not take your controller away while the Deck is
      in your hands, and "Force handheld" must not quietly switch off a rule you asked for.
      Both were measured doing exactly that on 2026-08-13.

    Fail-safe TRUE (= undocked = keep the pad). An exception here must not resolve to
    "hide every controller"; the rest of this module fails toward today's behaviour and so
    does this."""
    try:
        from . import deck_state
        return bool(deck_state.is_handheld(None))
    except Exception:
        return True


def _hide_deck_when_external() -> bool:
    """Whether to hide the Steam Deck's built-in pad once an external pad is connected -- the
    ES-DE -> Input Device Settings switch, now CONTEXT-AWARE (the physical display via deck_state):

      * DOCKED  -> HIDE_DECK_PAD_WHEN_EXTERNAL          (default ON = today's behaviour: the
                   external pad(s) are the players; the Deck must not steal a slot).
      * HANDHELD -> HIDE_DECK_PAD_WHEN_EXTERNAL_HANDHELD (default OFF = keep the Deck: undocked, the
                   Deck's own pad is your controller even with an external pad attached).

    An absent file / key reads as the shown default (docked on, handheld off). Only an explicit
    0/off/no/false (docked) or 1/on/yes/true (handheld) overrides. Back-compat: an install with only
    the docked key keeps docked behaviour and gains keep-the-Deck handheld with no migration."""
    try:
        from . import install_conf
    except Exception:
        return True
    handheld = False
    try:
        from . import handheld_input
        # handheld_input.context() applies the on-the-go ENABLED gate (like every on-the-go consumer),
        # so a user who never opted in resolves to "docked" = today's hide-the-Deck behaviour even when
        # physically undocked. Only on-the-go-enabled + undocked (or MAD_FORCE_CONTEXT) reads handheld.
        handheld = handheld_input.context() == "handheld"
    except Exception:
        handheld = False
    if handheld:
        return install_conf.get("HIDE_DECK_PAD_WHEN_EXTERNAL_HANDHELD", "0").strip().lower() in _TRUTHY
    return install_conf.get("HIDE_DECK_PAD_WHEN_EXTERNAL", "1").strip().lower() in _TRUTHY


def _to_vidpid(c: str) -> str:
    """A pad_classes entry as the vid:pid SDL matches (x-arcade token -> 045e:02a1)."""
    return "045e:02a1" if c in _XARCADE_TOKENS else c


def _scan() -> tuple[set[str], bool]:
    """(present classes, every connected 045e:02a1 is PROVABLY not the cabinet).

    The second value is the whole X-Arcade problem in one bool. "x-arcade" in the first set
    means the identified cabinet is connected -- certainty. Its absence does NOT mean the
    cabinet is away: the user may simply never have pressed "Identify X-Arcade" (there is no
    [hardware] section in the shipped policy at all, and the Preview page's CLEAR button
    removes it again), or may have re-cabled the stick since. Treating that uncertainty as
    "the cabinet is absent" is what made a connected, listed cabinet block itself and left
    Daphne with no controller at all -- measured 2026-08-13, caught in review.

    So we ask the cab directly. devices.usb_product returns 'X-Arcade 2' for the cabinet and
    'Xbox 360 Wireless Receiver for Windows' for a genuine Microsoft receiver, and "" for
    anything it cannot read. Used ONLY as negative evidence: `ruled_out` is True only when
    there is at least one 045e:02a1 connected AND every one of them positively names itself
    something other than an X-Arcade. No reading, or no 045e at all, is never a ruling."""
    pads = joypads(enumerate_devices())
    present = {vidpid(d) for d in pads}
    xa_pads = [d for d in pads if vidpid(d) == _XARCADE_VP]
    ruled_out = False
    try:
        from .devices import usb_product
        from .policy import load_merged
        from .routing import is_xarcade, xarcade_port
        xport = xarcade_port(load_merged())
        if any(is_xarcade(d, xport) for d in pads):
            present.add("x-arcade")
        elif xa_pads:
            ruled_out = all(
                (lambda p: bool(p) and not p.startswith("X-Arcade"))(usb_product(d.path))
                for d in xa_pads)
    except Exception:
        pass                      # no evidence either way -> ruled_out stays False
    return present, ruled_out


def _present_classes() -> set[str]:
    """Just the class half of _scan(), for callers that do not weigh the X-Arcade evidence."""
    return _scan()[0]


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

    No "hide the Deck pad" TOGGLE guard here ON PURPOSE: a present player family already
    wins (the Deck is never returned), and the only Deck path is the solo-handheld fallback —
    which the toggle must NOT suppress, or handheld play would lose its controller. The
    physical dock gate below is a different rule and does apply: docked with nothing you
    listed connected, there is no fallback, matching ignore_nonplayers so this backend's
    whitelist and blocklist can never disagree. (Live-dead today: the Deck classes are
    dropped from `present` by joypads(), so this fallback has no reachable input — it is
    gated for the invariant, not for an observed case.)

    Docked with nothing you listed connected returns MATCH_NOTHING rather than "". The two
    are opposites at the shell: hypseus-pin.sh only exports _EXCEPT when the string is
    non-empty, so "" leaves SDL unfiltered and the blocklist -- a snapshot of the devices
    that existed a second earlier -- becomes the only guard. A Bluetooth pad finishing its
    connect during the ~1.2 s launch window then plays, so "nothing plays" came down to
    whether your DualSense woke before or after the router ran. A whitelist that matches no
    device makes it deterministic."""
    present, xa_ruled_out = _scan()
    token_plays = bool(_XARCADE_VP in present and not xa_ruled_out)
    for c in pad_classes:
        if c in present or (c in _XARCADE_TOKENS and token_plays):
            return _fmt([c])
    if handheld_class and handheld_class in present and _undocked():
        return _fmt([handheld_class])
    if pad_classes and not _undocked():
        return MATCH_NOTHING
    return ""


def ignore_nonplayers(pad_classes, handheld_class: str = "") -> str:
    """BLOCKLIST for SDL_GAMECONTROLLER_IGNORE_DEVICES — hide every connected pad
    that is NOT a configured PLAYER family (`pad_classes`).

    Semantics: Steam Deck pad, Sinden guns, and any device not in `pad_classes`
    drop out; the handheld pad (`handheld_class`) is kept ONLY when no real player
    pad is present AND the Deck is physically undocked. Empty string = nothing to hide.

    ★ DOCKED WITH NOTHING YOU LISTED CONNECTED = NOTHING PLAYS (user decision 2026-08-13).
    Until then the Deck's own pad reached Daphne purely by omission: joypads() drops
    28de:11ff from `present`, so it could never land on this blocklist, and it walked
    through the (empty) whitelist. The picker row promises "pads not listed are hidden from
    this emulator", so a pad you did not tick must not inherit the game just because the
    cabinet is unplugged. UNDOCKED is the opposite case and is untouched: there the Deck's
    own pad IS your controller, so it stays. You can always get out either way -- the
    hold-to-quit combo (quit-combo-watcher.py) co-reads the pads' evdev nodes directly and
    never goes through SDL, so it fires even when the game itself sees no controller.

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
    present, xa_ruled_out = _scan()
    # The x-arcade token is a PLAYER when the identified cabinet is connected, and also when
    # a 045e:02a1 is connected that we cannot prove is something else -- see _scan. Expanding
    # it unconditionally (the old rule) let a plain Xbox 360 receiver play Daphne while the
    # DualSense was blocked and only "X-Arcade" was ticked, measured 2026-08-13. Refusing it
    # unconditionally was worse: a cabinet nobody had Identified blocked ITSELF, and the
    # docked rule below then hid everything, so Daphne launched with no controller at all.
    # Certainty in one direction only: block that vid:pid solely when every 045e present
    # names itself something other than an X-Arcade.
    token_plays = bool(_XARCADE_VP in present and not xa_ruled_out)
    has_player = any(c in present for c in pad_classes) or (
        token_plays and any(c in _XARCADE_TOKENS for c in pad_classes))
    player_vps = {_to_vidpid(c) for c in pad_classes
                  if c not in _XARCADE_TOKENS or token_plays}
    block = [c for c in present if ":" in c and c not in player_vps]
    if not has_player and handheld_class and _undocked():
        block = [c for c in block if c != handheld_class]   # solo handheld: keep it
    if pad_classes and not has_player and not _undocked():
        # Docked and not one listed family connected -> hide the Deck's own pad too, so
        # nothing plays (see the star note above). Skipped when the backend lists no player
        # families at all, where hiding everything would be an escalation rather than a
        # promise kept. A backend that lists a Deck class as a PLAYER keeps it, via
        # player_vps below.
        block = list(block) + [c for c in DECK_PAD_CLASSES if c not in player_vps]
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
