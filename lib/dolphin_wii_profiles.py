"""Dolphin Wii Remote PROFILES (Profiles/Wiimote/<name>.ini) for Classic-Controller emulation.

Like lib/dolphin_profiles (GameCube) but targeting `[WiimoteN]` blocks, with two Wii-specific twists:
  * the exported `[Profile]` bodies carry NO `Source` line (Dolphin stores Source only in
    WiimoteNew.ini), so `apply_cc_body` injects `Source = 1` (Emulated) ahead of the profile body;
  * unused slots are turned OFF via `disable_slot` (`Source = 0`) so Dolphin never enables a stray
    emulated Wii Remote (mirrors dolphin-wii-mode.sh leaving slot 2 off to avoid the "disabled" spam).

Reuses the section-generic text helpers in lib/dolphin_profiles (`_section_body`/`apply_profile_body`).
Pure text (no live-file writes) so the launch coordinator owns the I/O + the transient snapshot.
"""
from __future__ import annotations

import re
from pathlib import Path

from lib import dolphin_profiles

_DIR = Path.home() / ".var/app/org.DolphinEmu.dolphin-emu/config/dolphin-emu/Profiles/Wiimote"
_CLASSIC_RE = re.compile(r'(?mi)^Extension[ \t]*=[ \t]*Classic\b')
_DEVICE_RE = re.compile(r'(?m)^Device[ \t]*=[ \t]*([^\r\n]*)')


def profiles_dir() -> Path:
    return _DIR


def list_profiles() -> list[str]:
    """Sorted names of the CLASSIC-CONTROLLER profiles in Profiles/Wiimote/ (body has
    `Extension = Classic`), excluding the Sinden gun profiles and the unwired Wii_* pointer profiles."""
    out: list[str] = []
    try:
        entries = sorted(_DIR.glob("*.ini"))
    except OSError:
        return []
    for p in entries:
        stem = p.stem
        if stem.startswith("Sinden") or stem.startswith("Wii_") or not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if _CLASSIC_RE.search(text):
            out.append(stem)
    return out


def profile_body(name: str) -> str | None:
    """The `[Profile]` body of Profiles/Wiimote/<name>.ini (newline-terminated), or None if the
    profile file or its [Profile] section is missing."""
    p = _DIR / f"{name}.ini"
    if not p.is_file():
        return None
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    body = dolphin_profiles._section_body(text, "Profile")
    if body is None:
        return None
    return body if body.endswith("\n") else body + "\n"


def profile_device(name: str) -> str | None:
    """The target pad NAME from the profile's `Device` line (the part after `<source>/<index>/`), or
    None. Same shape as dolphin_profiles.profile_device but for the Wiimote profile dir."""
    body = profile_body(name)
    if body is None:
        return None
    m = _DEVICE_RE.search(body)
    if not m:
        return None
    dev = m.group(1).strip()
    parts = dev.split("/", 2)
    return parts[2] if len(parts) == 3 else dev        # tolerate a bare/odd Device string


def apply_cc_body(text: str, section: str, body: str) -> str | None:
    """Load a Classic-Controller profile `body` into [section] of WiimoteNew.ini, injecting
    `Source = 1` (Emulated) ahead of it because the [Profile] body carries none. `Extension = Classic`
    is already in the body. Returns None if [section]'s header is absent (caller keeps the old text)."""
    src = body if body.endswith("\n") else body + "\n"
    return dolphin_profiles.apply_profile_body(text, section, "Source = 1\n" + src)


def disable_slot(text: str, section: str) -> str:
    """Turn [section] OFF: replace its body with just `Source = 0` (None), dropping any stale mappings
    from the prior personality. No-op (returns `text`) if the header is absent."""
    nt = dolphin_profiles.apply_profile_body(text, section, "Source = 0\n")
    return nt if nt is not None else text
