"""Dolphin Wii Remote PROFILES (Profiles/Wiimote/<name>.ini) — ALL user profiles, with
Classic-Controller filtering for the CC rail.

Profiles are AUTHORED in Dolphin's own UI (the MAD editors were phased out 2026-08-04);
MAD only lists + picks + applies them. Like lib/dolphin_profiles (GameCube) but targeting
`[WiimoteN]` blocks, with two Wii-specific twists:
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
_EXT_RE = re.compile(r'(?mi)^Extension[ \t]*=[ \t]*([^\r\n]+)')
_PROFILE_HEAD = re.compile(r'(?mi)^\[Profile\]')
_DEVICE_RE = re.compile(r'(?m)^Device[ \t]*=[ \t]*([^\r\n]*)')

# The single home for the built-in CC handheld default (was duplicated as a literal in
# dolphin_wii_source + dolphin_wii_pads_cmds).
HANDHELD_DEFAULT = "Steamdeck = classic controller"


def profiles_dir() -> Path:
    return _DIR


def is_sinden(name: str) -> bool:
    """True for the Sinden lightgun profiles (managed by the sinden rail). A Sinden PICK at
    launch must apply the FULL sinden mode (both gun slots + WiimoteContinuousScanning), never
    a bare single-slot body copy — see dolphin_wii_source._apply_single."""
    return str(name).startswith("Sinden")


def list_profiles(extension: str | None = "Classic", include_sinden: bool = False) -> list[str]:
    """Sorted profile names in Profiles/Wiimote/. The DEFAULT keeps every legacy caller
    byte-identical: only Classic-Controller profiles (body has `Extension = Classic`),
    Sinden excluded. The pickers call `list_profiles(None, include_sinden=True)` = every
    user profile regardless of extension, Sinden included (user decision 2026-08-04 — the
    old Classic-only content filter silently hid a new Nunchuk profile).

    Always excluded: the stock EmuDeck `Wii_*` pointer set (six near-duplicate entries
    would flood the picker) and files with no `[Profile]` section (not a profile)."""
    out: list[str] = []
    try:
        entries = sorted(_DIR.glob("*.ini"))
    except OSError:
        return []
    for p in entries:
        stem = p.stem
        if stem.startswith("Wii_") or not p.is_file():
            continue
        if not include_sinden and is_sinden(stem):
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if not _PROFILE_HEAD.search(text):
            continue
        if extension is not None:
            m = _EXT_RE.search(text)
            if not m or m.group(1).strip().lower() != extension.strip().lower():
                continue
        out.append(stem)
    return out


def profile_extension(name: str) -> str:
    """The profile's `Extension =` value ("None" when absent/blank) — for page notes."""
    body = profile_body(name)
    if not body:
        return "None"
    m = _EXT_RE.search(body)
    return (m.group(1).strip() or "None") if m else "None"


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
