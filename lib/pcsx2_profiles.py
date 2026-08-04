"""Resolve a PCSX2 (PS2) docked/handheld input profile stem from policy + the per-game store.

PCSX2's native profiles live as ``~/.config/PCSX2/inputprofiles/<stem>.ini`` and are
AUTHORED in PCSX2's own desktop UI; MAD only PICKS one per context. The picks live in
``[backends.pcsx2].profile_docked`` / ``.profile_handheld`` (global) and in the per-game
store entry's sparse ``"profiles": {"docked": ..., "handheld": ...}`` dict
(``~/Emulation/storage/pcsx2/pergame-input.json``, see lib/madsrv/pcsx2_pergame_input_cmds).

Resolution at launch is per-context: per-game -> global -> None. ``None`` = leave the
resting ``[PadN]`` layout untouched (today's behaviour). HANDHELD NEVER INHERITS DOCKED —
an unset handheld chain resolves to None, never to the docked profile (the codebase-wide
context convention; a docked DS4 layout silently applied to the Deck pad would mis-bind).

CRITICAL: the chosen profile is applied by COPYING its [PadN] bodies into the global ini
transiently at launch (pcsx2_cfg.apply_profile_bodies). Native ``[Pad] InputProfileName``
is NEVER written — PCSX2 would load the profile INSTEAD of [PadN] and silently bypass the
launch-time pad calibration.

Leaf module: imports only lib.handheld_input, so the launch hot path stays cheap.
"""
from __future__ import annotations

from pathlib import Path

from . import handheld_input

_DEFAULT_INI = "~/.config/PCSX2/inis/PCSX2.ini"


def valid_stem(s) -> bool:
    """A safe profile stem: non-empty string, no path separators, no leading dot."""
    if not isinstance(s, str):
        return False
    s = s.strip()
    if not s or s.startswith("."):
        return False
    return "/" not in s and "\\" not in s and ".." not in s


def profiles_dir(pcsx2_cfg: dict) -> Path:
    """The ``inputprofiles`` dir derived from the backend's ``config_file`` (sibling of
    ``inis/``), so a Flatpak/portable tree resolves too."""
    cfg_file = _DEFAULT_INI
    if isinstance(pcsx2_cfg, dict):
        v = pcsx2_cfg.get("config_file")
        if isinstance(v, str) and v.strip():
            cfg_file = v.strip()
    return Path(cfg_file).expanduser().parent.parent / "inputprofiles"


def list_stems(profiles: Path) -> list[str]:
    """Sorted stems of ``<dir>/*.ini`` (the pickable profiles); ``[]`` on any I/O error."""
    try:
        return sorted(p.stem for p in Path(profiles).glob("*.ini") if p.is_file())
    except OSError:
        return []


def _clean(v) -> str | None:
    """A husk-tolerant stem read: non-string / blank / path-y values degrade to None."""
    if not isinstance(v, str):
        return None
    v = v.strip()
    return v if v and valid_stem(v) else None


def global_profile(pcsx2_cfg: dict, context) -> str | None:
    """The global ``profile_docked`` / ``profile_handheld`` stem for ``context``, or None."""
    if not isinstance(pcsx2_cfg, dict):
        return None
    ctx = handheld_input.normalize(context)
    return _clean(pcsx2_cfg.get("profile_handheld" if ctx == "handheld" else "profile_docked"))


def pergame_profile(entry, context) -> str | None:
    """The per-game ``profiles[context]`` stem from a store entry, or None (husk-tolerant)."""
    if not isinstance(entry, dict):
        return None
    profiles = entry.get("profiles")
    if not isinstance(profiles, dict):
        return None
    return _clean(profiles.get(handheld_input.normalize(context)))


def resolve(entry, pcsx2_cfg: dict, context) -> str | None:
    """The stem to apply for this launch: per-game -> global -> None. No cross-context
    fallback (see the module docstring: handheld never inherits docked)."""
    return pergame_profile(entry, context) or global_profile(pcsx2_cfg, context)


def profile_path(profiles: Path, stem: str) -> Path | None:
    """``<dir>/<stem>.ini`` if the stem is safe and the file exists, else None."""
    if not valid_stem(stem):
        return None
    try:
        p = Path(profiles) / f"{stem.strip()}.ini"
        return p if p.is_file() else None
    except OSError:
        return None
