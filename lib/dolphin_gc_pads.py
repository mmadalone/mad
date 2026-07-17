"""Launch-time GameCube "pads -> players" — apply the profile priority to GCPadNew.ini ports.

Called (the DOCKED branch) by the dolphin_gc launch coordinator (lib/dolphin_gc_dock.py). Walks the
stored profile priority; for each profile whose target pad is connected (and not yet consumed by a
higher-priority profile), loads it into the next GameCube port. The coordinator owns the transient
snapshot of GCPadNew.ini and the game-end restore, so this module is pure text + a plan.

Same-pad multiplayer (two identical DualSense) works because each profile carries its own Device
`SDL/0/...` vs `SDL/1/...` index — MAD never invents an index; it just loads the user's profiles.
"""
from __future__ import annotations

from collections import Counter

from lib import devices as dv
from lib import dolphin_profiles
from lib.madsrv import dolphin_gc_pads_cmds as prefs

_PLAYERS = 4


def _connected_index() -> tuple[Counter, dict]:
    """(pool, name_to_vidpid) for the connected real pads.

    `pool` counts pads by vid:pid (two identical pads = 2). `name_to_vidpid` maps every connected
    pad's evdev AND SDL name to its vid:pid. We match a profile to a pad by VID:PID (resolved from
    the profile's Device NAME) rather than by name, because Dolphin `SDL/` profiles store SDL's
    controller name, which differs from the evdev name for renamed pads: a DS4 is evdev
    `Wireless Controller` but SDL `PS4 Controller`, both -> 054c:09cc. vid:pid also makes same-type
    pairs (two DS4) count correctly. SDL enumeration (~1s cold) runs only on this gc launch path."""
    pool: Counter = Counter()
    name_to_vp: dict = {}
    try:
        for d in dv.joypads(dv.enumerate_devices()):
            vp = dv.vidpid(d)
            pool[vp] += 1
            if d.name:
                name_to_vp.setdefault(d.name, vp)              # evdev name -> vid:pid
    except Exception:
        return Counter(), {}
    try:
        for s in dv.sdl_devices():                             # SDL name (e.g. 'PS4 Controller')
            if s.name and s.vidpid:
                name_to_vp.setdefault(s.name, s.vidpid)
    except Exception:
        pass                                                   # evdev-only fallback (renamed pads miss)
    return pool, name_to_vp


def plan_assignment() -> list[tuple[int, str]]:
    """[(port, profile), ...] for the connected profiles in priority order (pure — no file I/O).
    Empty when hands-off, no stored priority, or nothing connected matches. Matches a profile to a
    physical pad by vid:pid (resolved from the profile's Device NAME via the connected pads' evdev +
    SDL names), consuming one instance per assignment so same-type pairs fill distinct ports."""
    if prefs.hands_off():
        return []
    order = prefs.priority()
    if not order:
        return []
    pool, name_to_vp = _connected_index()
    out: list[tuple[int, str]] = []
    port = 1
    for name in order:
        if port > _PLAYERS:
            break
        vp = name_to_vp.get(dolphin_profiles.profile_device(name) or "")
        if vp and pool.get(vp, 0) > 0:
            pool[vp] -= 1                                      # consume one instance of that pad
            out.append((port, name))
            port += 1
    return out


def assign_text(text: str, assign=None) -> tuple[str, list[tuple[int, str]]]:
    """Apply the planned profiles to GCPadNew.ini `text`; return (new_text, applied[(port,name)]).

    `assign` supplies a precomputed plan_assignment() so the caller can resolve ONCE and reuse it
    (dolphin_gc_dock.plan() already resolved it, and plan_assignment does a ~1s cold SDL walk).
    None keeps the old self-resolving behaviour."""
    applied: list[tuple[int, str]] = []
    for port, name in (plan_assignment() if assign is None else assign):
        body = dolphin_profiles.profile_body(name)
        if body is None:
            continue
        nt = dolphin_profiles.apply_profile_body(text, f"GCPad{port}", body)
        if nt is None:                       # that port's [GCPadN] header is absent -> skip
            continue
        text = nt
        applied.append((port, name))
    return text, applied
