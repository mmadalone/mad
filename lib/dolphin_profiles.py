"""Dolphin GameCube controller PROFILES (Profiles/GCPad/<name>.ini).

A profile is a single `[Profile]` section = the full SaveConfig body Dolphin writes for a pad
(Device + every binding + Calibration). "Loading" a profile into a port is a byte-safe REPLACE of
that port's `[GCPadN]` section BODY with the profile's `[Profile]` body -- exactly what Dolphin's
own "Load Profile -> Save" produces (verified against Dolphin master InputConfig, 2026-07-08).
There is no `Profile=` key in GCPadNew.ini; profiles are apply-and-forget.

Pure text helpers (no live-file writes here) so the callers (the buffered input page + the launch
binder) stay in control of I/O + backups.
"""
from __future__ import annotations

import re
from pathlib import Path

_DIR = Path.home() / ".var/app/org.DolphinEmu.dolphin-emu/config/dolphin-emu/Profiles/GCPad"


def profiles_dir() -> Path:
    return _DIR


def list_profiles() -> list[str]:
    """Sorted profile names (file stems) present in Profiles/GCPad/, or [] if none."""
    try:
        return sorted(p.stem for p in _DIR.glob("*.ini") if p.is_file())
    except OSError:
        return []


def _section_body(text: str, section: str) -> str | None:
    """The BODY of [section] (everything after the header line up to the next [section] / EOF),
    or None if the header is absent. Mirrors cfgutil._ini_span but standalone (no import cycle)."""
    m = re.search(rf'(?m)^\[[ \t]*{re.escape(section)}[ \t]*\][^\n]*\n', text)
    if not m:
        return None
    start = m.end()
    nm = re.search(r'(?m)^\[', text[start:])
    return text[start:(start + nm.start()) if nm else len(text)]


def profile_body(name: str) -> str | None:
    """The `[Profile]` body of Profiles/GCPad/<name>.ini (newline-terminated), or None if the
    profile file or its [Profile] section is missing."""
    p = _DIR / f"{name}.ini"
    if not p.is_file():
        return None
    with p.open("r", encoding="utf-8", errors="replace", newline="") as fh:
        body = _section_body(fh.read(), "Profile")
    if body is None:
        return None
    return body if body.endswith("\n") else body + "\n"   # one profile ships without a trailing LF


def profile_device(name: str) -> str | None:
    """The target pad NAME from a profile's `Device` line (the part after `<source>/<index>/`), or
    None if the profile / its Device line is absent. Dolphin `Device = <source>/<index>/<name>`; the
    name equals devices.Device.name (evdev == SDL2 string), so callers can match it to connected
    pads. e.g. `SDL/0/DualSense Wireless Controller` -> `DualSense Wireless Controller`."""
    body = profile_body(name)
    if body is None:
        return None
    m = re.search(r'(?m)^Device[ \t]*=[ \t]*([^\r\n]*)', body)
    if not m:
        return None
    dev = m.group(1).strip()
    parts = dev.split("/", 2)
    return parts[2] if len(parts) == 3 else dev   # tolerate a bare/odd Device string


def apply_profile_body(gc_text: str, section: str, body: str) -> str | None:
    """Replace [section]'s BODY in gc_text with `body` (the profile's [Profile] body), keeping the
    `[GCPadN]` header + everything outside the section byte-for-byte. None if the section is absent."""
    m = re.search(rf'(?m)^\[[ \t]*{re.escape(section)}[ \t]*\][^\n]*\n', gc_text)
    if not m:
        return None
    start = m.end()
    nm = re.search(r'(?m)^\[', gc_text[start:])
    end = (start + nm.start()) if nm else len(gc_text)
    return gc_text[:start] + body + gc_text[end:]
