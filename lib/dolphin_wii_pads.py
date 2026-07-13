"""Launch-time Wii "Classic Controller pads -> players" -- apply the CC profile priority to
WiimoteNew.ini `[Wiimote1..4]`.

Called (the DOCKED, no-DolphinBar branch) by the Wii-source coordinator (lib/dolphin_wii_source).
Walks the stored profile priority; for each profile whose target pad is connected (and not yet
consumed by a higher-priority profile), loads it into the next Wii Remote slot (Source=1, Classic).
Every slot in 1..4 that is NOT filled is turned OFF (Source=0) so a resting Sinden/emulated slot
never lingers beside the Classic Controllers. The coordinator owns the transient snapshot + restore,
so this module is pure text + a plan.

Same-pad multiplayer (two identical DualSense) works because each profile carries its own Device
`SDL/0/...` vs `SDL/1/...` index -- MAD never invents an index; it just loads the user's profiles.
"""
from __future__ import annotations

from collections import Counter

from lib import devices as dv
from lib import dolphin_wii_profiles
from lib.madsrv import dolphin_wii_pads_cmds as prefs

_PLAYERS = 4


def _connected_index() -> tuple[Counter, dict]:
    """(pool, name_to_vidpid) for the connected real pads. `pool` counts pads by vid:pid (two
    identical pads = 2). `name_to_vidpid` maps every connected pad's evdev AND SDL name to its
    vid:pid, so a profile's Device NAME resolves to a vid:pid regardless of which backend named it
    (a DS4 is evdev 'Wireless Controller' / SDL 'PS4 Controller', both -> 054c:09cc)."""
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
    """[(slot, profile), ...] for the connected profiles in priority order (pure -- no file I/O).
    Empty when hands-off, no stored priority, or nothing connected matches. Matches by vid:pid
    (resolved from the profile's Device NAME), consuming one instance per assignment so same-type
    pairs fill distinct slots."""
    if prefs.hands_off():
        return []
    order = prefs.priority() or prefs.docked_default()   # out-of-the-box: all docked CC profiles
    if not order:
        return []
    pool, name_to_vp = _connected_index()
    out: list[tuple[int, str]] = []
    slot = 1
    for name in order:
        if slot > _PLAYERS:
            break
        vp = name_to_vp.get(dolphin_wii_profiles.profile_device(name) or "")
        if vp and pool.get(vp, 0) > 0:
            pool[vp] -= 1                                      # consume one instance of that pad
            out.append((slot, name))
            slot += 1
    return out


def assign_text(text: str) -> tuple[str, list[tuple[int, str]]]:
    """Apply the planned CC profiles to WiimoteNew.ini `text`, then turn OFF every unfilled slot in
    1..4. Returns (new_text, applied[(slot, name)]). `applied` empty -> nothing connected matched."""
    applied: list[tuple[int, str]] = []
    filled: set[int] = set()
    for slot, name in plan_assignment():
        body = dolphin_wii_profiles.profile_body(name)
        if body is None:
            continue
        nt = dolphin_wii_profiles.apply_cc_body(text, f"Wiimote{slot}", body)
        if nt is None:                       # that slot's [WiimoteN] header is absent -> skip
            continue
        text = nt
        filled.add(slot)
        applied.append((slot, name))
    if applied:                              # only reshape the slots when we actually placed a pad
        for slot in range(1, _PLAYERS + 1):
            if slot not in filled:
                text = dolphin_wii_profiles.disable_slot(text, f"Wiimote{slot}")
    return text, applied
