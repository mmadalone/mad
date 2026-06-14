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


def keep_first_present(pad_classes, handheld_class: str = "") -> str:
    """Whitelist for a STRICT per-system priority chain: expose ONLY the first
    class in `pad_classes` that is connected (all of its devices — so a 2-side
    X-Arcade or two same-model pads still give P1+P2), else the handheld class,
    else "". Unlike keep_except_list (which exposes ALL listed pads at once, for
    fixed JOY1/JOY2 emulators like Supermodel), this guarantees the top-priority
    *present* family is the only thing the game sees — so it becomes Player 1
    regardless of SDL enumeration order. Used by openbor.sh."""
    present = _present_classes()
    for c in pad_classes:
        if c in present:
            return _fmt([c])
    if handheld_class and handheld_class in present:
        return _fmt([handheld_class])
    return ""


def ignore_nonplayers(pad_classes, handheld_class: str = "") -> str:
    """BLOCKLIST for SDL_GAMECONTROLLER_IGNORE_DEVICES — hide every connected pad
    that is NOT a configured PLAYER family (`pad_classes`), keeping all real
    players so multiplayer (P1-P4) works. Used for Proton/Wine emulators (OpenBOR)
    whose `winebus` IGNORES the `_EXCEPT` whitelist but honors this IGNORE list.

    So the Steam Deck pad, Sinden guns, and any device not in `pad_classes` drop
    out automatically — there is NO hardcoded hide-list; the player set is the
    GUI-editable `[backends.<be>].pad_classes` (router GUI → Backends page). The
    handheld pad (`handheld_class`) is kept ONLY when no real player pad is present
    (so solo handheld play still has a controller). Empty string = nothing to hide."""
    present = _present_classes()
    has_player = any(c in present for c in pad_classes)
    # Map tokens to vid:pid for the block test so the X-Arcade's 045e:02a1 is NOT
    # blocked when "x-arcade" is a player; only ever block real vid:pid entries.
    player_vps = {_to_vidpid(c) for c in pad_classes}
    block = [c for c in present if ":" in c and c not in player_vps]
    if not has_player and handheld_class:
        block = [c for c in block if c != handheld_class]   # solo: keep the handheld
    return _fmt(sorted(block))


def keep_except_list(pad_classes, handheld_class: str = "",
                     keep_extra=()) -> str:
    """Whitelist string for SDL_GAMECONTROLLER_IGNORE_DEVICES_EXCEPT: the chosen
    player pads that are connected (+ keep_extra), or the handheld class if no
    player pad is present. Empty string if nothing relevant is connected (caller
    should then leave SDL unfiltered)."""
    present = _present_classes()
    keep = [c for c in pad_classes if c in present]
    if not keep and handheld_class and handheld_class in present:
        keep = [handheld_class]
    keep += [c for c in keep_extra if c in present]
    return _fmt(keep)
