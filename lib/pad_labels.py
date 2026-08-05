"""Controller display labels — the single home for pad friendly-naming.

Every surface that lists controllers (MAD pages, pickers, previews, testers)
must produce its labels through this module. History: the "X-Arcade shown as
Xbox 360" mislabel was re-fixed per page ~8 times because each new page grew
its own labeling copy — don't add another one.

Leaf module: imports only lib.devices + lib.routing (no madsrv/RPC), so both
the mad-backend daemon and hook-side CLIs (e.g. ``python3 -m lib.lindbergh_pads``)
can use it.

Three labeling contexts:
- evdev ``Device`` in hand -> ``device_label(d, xport)``: the full treatment,
  including the X-Arcade P1/P2 half split (the only context with the USB
  interface number).
- SDL index only -> recover the evdev twin via ``madsrv.device_cmds
  .evdev_by_sdl_index`` (SDL pads carry no USB port), then
  ``pad_label(...)`` with the twin's port.
- name string only (e.g. parsing a stored emulator ini) -> ``pad_name``/
  ``KNOWN_PADS`` at best. This context can NEVER say "X-Arcade": the stick is
  byte-identical to a real Xbox 360 pad and only the USB-port check tells them
  apart, so label it as the class name.

``xport`` (the identified X-Arcade USB port) is always a PARAMETER — callers
pass ``routing.xarcade_port(policy.load_merged())``. It is never loaded here,
preserving the daemon's no-cache policy contract (see lib/routing.py).
"""
from __future__ import annotations

from .devices import Device, port_of, usb_iface_num
from .routing import is_xarcade

# Cosmetic vid:pid -> friendly label, for display only (every lookup falls back
# to the raw vid:pid). NOT a routing input.
KNOWN_PADS = {"054c:0ce6": "DualSense", "054c:09cc": "DualShock 4",
              # The three DS4-family classes need DISTINCT labels: every full-label
              # surface (the handheld/fallback pad picker, the PCSX2 device-visibility
              # rows, priority lists) builds its options from KNOWN_PADS, so identical
              # strings would render as indistinguishable duplicate choices.
              "054c:05c4": "DualShock 4 (v1)",   # DS4 v1 (matches routing._DS4_PIDS)
              "054c:0ba0": "DualShock 4 (adapter)",   # DS4 USB wireless adapter
              "057e:0330": "Wii U Pro", "28de:1205": "Steam Deck",
              "28de:11ff": "Steam Deck (SI)",
              "2dc8:2810": "8BitDo FC30", "2dc8:3820": "8BitDo N30 Pro",
              "045e:02a1": "Xbox 360"}   # X-Arcade in Xbox mode shares this id; only the
                                         # IDENTIFIED-port one is shown as X-Arcade (pad_label)
# Compact labels so several toggles fit one row: KNOWN_PADS' label, shortened where a
# tighter form helps. Derived from KNOWN_PADS so a new pad is added in ONE place (add a
# _PAD_SHORT_OVERRIDE entry only if its full label is too long for a toggle row).
_PAD_SHORT_OVERRIDE = {"054c:09cc": "DS4", "054c:05c4": "DS4 v1", "054c:0ba0": "DS4 dongle",
                       "057e:0330": "WiiU Pro", "28de:1205": "Deck",
                       "28de:11ff": "Deck(SI)", "2dc8:2810": "8BitDo", "2dc8:3820": "8BitDo N30"}
PAD_SHORT = {vp: _PAD_SHORT_OVERRIDE.get(vp, lbl) for vp, lbl in KNOWN_PADS.items()}
PAD_SHORT["x-arcade"] = "X-Arcade"   # the IDENTIFIED X-Arcade (port-resolved), distinct from a raw 045e:02a1


def pad_name(vidpid: str) -> str:
    """Friendly controller name for a 'vvvv:pppp' class, or '' if unknown."""
    return KNOWN_PADS.get((vidpid or "").strip().lower(), "")


def pad_label(vid: int, vidpid: str, name: str, port: str, xport: str) -> str:
    """Port-aware friendly label: the 045e pad at the identified USB port is the
    X-Arcade; every other pad gets KNOWN_PADS/name. For SDL pads, recover ``port``
    from the evdev twin (they carry no USB port of their own)."""
    if xport and vid == 0x045E and port and port == xport:
        return "X-Arcade"
    return KNOWN_PADS.get(vidpid, name)


def device_label(d: Device, xport: str) -> str:
    """Friendly label for an evdev Device — pad_label plus the X-Arcade half
    split: the stick's two byte-identical nodes (same name/vid:pid/phys) are told
    apart ONLY by USB bInterfaceNumber (0=P1, 1=P2 — replug-stable, unlike
    event-node order); an unreadable interface stays plain "X-Arcade"."""
    if is_xarcade(d, xport):
        iface = usb_iface_num(d.path)
        return f"X-Arcade P{iface + 1}" if iface in (0, 1) else "X-Arcade"
    return KNOWN_PADS.get(f"{d.vid:04x}:{d.pid:04x}", d.name)


# ── friendly names from a STORED config string (not a live device) ────────────
# Same rule as everything else here: a surface that shows a controller name must get it from this
# module. These two exist because the emulator configs record a pad as an opaque id/guid string, and
# the pages that display them used to each carry their own decoder (ryujinx_input_cmds and
# yuzu_input both had a private `_configured_pad`). Parsing WHICH key holds the id stays with the
# caller — that is page knowledge; turning it into a name is label knowledge, and lives here.
def vidpid_from_sdl_guid(guid: str) -> str:
    """'vvvv:pppp' parsed from an SDL2 joystick GUID (vendor at bytes 4-5, product
    at bytes 8-9, both little-endian — independent of the bus/CRC/version bytes, so
    it works for Eden's CRC-zeroed GUIDs too). '' if the GUID is too short.

    Historically lived in lib/mad_config, which re-exports it; it moved here so `pad_from_sdl_guid`
    below could use it without mad_config importing pad_labels and pad_labels importing back."""
    g = (guid or "").strip().lower()
    if len(g) < 20:
        return ""
    return f"{g[10:12]}{g[8:10]}:{g[18:20]}{g[16:18]}"


def pad_from_sdl_guid(guid: str) -> str:
    """Friendly name for a pad recorded as an SDL joystick GUID (the Yuzu forks' binding values),
    or '' when the guid is unusable."""
    vidpid = vidpid_from_sdl_guid(guid)
    return pad_name(vidpid) if vidpid else ""


def pad_from_ryujinx_id(rid: str) -> str:
    """Friendly name for a pad recorded as a Ryujinx `id`
    ('<idx>-<8 hex>-<vid>-0000-<pid byte-swapped>-<12 hex>'), or '' if unbound/unparseable.
    Often '' for the migrated standalones — the launch wrapper assigns the real pad per launch."""
    parts = (rid or "").split("-")
    if len(parts) >= 5 and len(parts[2]) == 4 and len(parts[4]) == 4:
        return pad_name(f"{parts[2]}:{parts[4][2:4]}{parts[4][0:2]}")
    return ""
