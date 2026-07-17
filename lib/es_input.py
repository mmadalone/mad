"""ES-DE's OWN configured input devices (~/ES-DE/settings/es_input.xml).

WHY: the controller-family list MAD offers must come from what ES-DE actually has configured, not
from a hardcoded list. `mad_config.KNOWN_FAMILIES` was seven names frozen in source, so a pad the
user configured in ES-DE but that nobody had added to that list was simply un-offerable, and a name
in the list that the user had never configured was offered anyway.

HOW: es_input.xml carries one <inputConfig deviceName= deviceGUID=> per configured device, and an
SDL joystick GUID encodes vid:pid at fixed offsets. Those ids go through routing.family_of -- THE
one matcher, shared with `ports` resolution -- so the family list and the router can never disagree
about what a pad is.

    <inputConfig deviceName="DualSense Wireless Controller"
                 deviceGUID="030057564c050000e60c000000006800" ...>
                             ^^^^ ^^^^ ^^^^ ^^^^
                             bus  crc  vid  pid      (each little-endian)

Decoded live on this rig 2026-07-17: DualSense 054c:0ce6, X360/X-Arcade 045e:02a1,
Wii U Pro 057e:0330, Steam Deck 28de:11ff, plus a Keyboard (deviceGUID "-1", not a pad).

THE X-ARCADE CANNOT BE DERIVED FROM THIS FILE. In Xbox mode the cab is 045e:02a1, byte identical to
a real Xbox 360 pad; ONLY its USB port ([hardware].xarcade_port) separates them, and es_input.xml
carries no port. So a configured 045e yields BOTH tokens when an X-Arcade is identified -- omit that
and the cabinet vanishes from the picker.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Optional

from . import esde_settings
from .routing import family_of


class _Pad:
    """The three attributes family_of reads. es_input.xml has no evdev Device to hand it."""

    __slots__ = ("name", "vid", "pid")

    def __init__(self, name: str, vid: int, pid: int):
        self.name, self.vid, self.pid = name, vid, pid


def _path():
    # Read esde_settings.APPDATA at CALL time, not import time: it is env-derived
    # ($ESDE_APPDATA_DIR) and tests patch it.
    return esde_settings.APPDATA / "settings" / "es_input.xml"


def guid_vidpid(guid: str) -> Optional[tuple[int, int]]:
    """(vid, pid) from an SDL joystick GUID, or None if it carries none.

    The keyboard's GUID is the literal "-1", and a GUID with a zero vendor is a bus-only id (some
    virtual/dinput devices), neither of which names a physical pad."""
    g = (guid or "").strip()
    if len(g) != 32:
        return None
    try:
        vid = int(g[10:12] + g[8:10], 16)      # bytes 8-11, little-endian
        pid = int(g[18:20] + g[16:18], 16)     # bytes 16-19, little-endian
    except ValueError:
        return None
    if not vid:
        return None
    return vid, pid


def devices() -> list[tuple[str, int, int]]:
    """[(deviceName, vid, pid)] for every PAD ES-DE has configured. [] when the file is missing or
    unreadable, so every caller degrades to its own fallback rather than raising into a page."""
    try:
        root = ET.parse(_path()).getroot()
    except Exception:
        return []
    out: list[tuple[str, int, int]] = []
    for cfg in root:
        vp = guid_vidpid(cfg.attrib.get("deviceGUID", ""))
        if vp is None:
            continue                            # the keyboard, or a GUID with no vendor
        out.append((cfg.attrib.get("deviceName", ""), vp[0], vp[1]))
    return out


def families(xport: str = "") -> list[str]:
    """Family tokens for what ES-DE has configured, in file order, deduped.

    `xport` is [hardware].xarcade_port. When it is set and a 045e pad is configured, BOTH
    "X-Arcade" and "Xbox" are offered: es_input.xml cannot tell them apart (see the module note),
    so offering only the vid:pid answer would drop the cabinet. Empty xport means no X-Arcade is
    identified and every 045e is just an Xbox pad -- the same default xarcade_port() returns.

    [] when the file is missing/unreadable OR when nothing configured maps to a known family; the
    caller falls back to KNOWN_FAMILIES. Callers must treat [] as "I don't know", never as "none".
    """
    out: list[str] = []
    for name, vid, pid in devices():
        fam = family_of(_Pad(name, vid, pid))
        if fam and fam not in out:
            out.append(fam)
        if xport and vid == 0x045e and "X-Arcade" not in out:
            out.append("X-Arcade")
    return out
