"""Shared resolution for a controller-FAMILY x launch-CONTEXT input-profile map.

Three backends now key their input profiles the same way -- Cemu (Wii U), Eden and Citron (the
Yuzu-fork Switch emulators) -- because all three decide at LAUNCH which physical pad lands in which
emulator slot. Pinning a profile to a player would therefore mis-bind whenever a different pad
family takes that seat, so the pick is keyed by ``routing.family_of`` name instead:

    [backends.<be>.profile_map.<context>]
    DualSense = "DualSense 1"          # value = the emulator's own named profile STEM

This module owns the lookup, the opt-in handheld mirror, and the "nth same-family pad" derivation,
so those rules exist ONCE. lib/cemu_profiles.py and lib/yuzu_profiles.py are thin, backend-flavoured
wrappers over it (they differ only in the profile-file suffix: Cemu ``.xml``, Yuzu forks ``.ini``).

Everything is husk-tolerant: a hand-edited non-dict map, a non-dict context slice or a non-string
value degrades to ``None`` rather than raising, because every caller here is on the launch hot path
and an unset pick must always mean "leave that slot's resting config untouched".

Leaf module: imports only lib.handheld_input, so hooks and the binder stay cheap.
"""
from __future__ import annotations

import re
from pathlib import Path

from . import handheld_input

_TRAILING_NUM_RE = re.compile(r"^(.*?)(\d+)\s*$")

MIRROR_KEY = "handheld_mirrors_docked"


def valid_stem(s) -> bool:
    """A safe profile stem: non-empty string, no path separators, no ``..``, no leading dot.
    Mirrors pcsx2_profiles.valid_stem -- these stems are joined onto a directory."""
    if not isinstance(s, str):
        return False
    s = s.strip()
    if not s or s.startswith("."):
        return False
    return "/" not in s and "\\" not in s and ".." not in s


def lookup(be_cfg: dict, family: str, ctx: str) -> str | None:
    """The stem assigned to ``family`` in the ``ctx`` slice of ``profile_map``, or ``None``."""
    pm = be_cfg.get("profile_map") if isinstance(be_cfg, dict) else None
    if not isinstance(pm, dict):
        return None
    slice_ = pm.get(ctx)
    if not isinstance(slice_, dict):
        return None
    name = slice_.get(family)
    if not isinstance(name, str):
        return None
    return name.strip() or None


def resolve(be_cfg: dict, family: str | None, context: str) -> str | None:
    """The stem for ``family`` in ``context`` ("docked"|"handheld"), or ``None`` when unset.

    HANDHELD MIRROR: when ``[backends.<be>].handheld_mirrors_docked`` is set AND a handheld family
    has no handheld entry, fall back to that family's DOCKED entry (opt-in "same as docked"). The
    fallback is ONE-WAY -- docked never inherits handheld -- and per FAMILY, not per map: a family
    explicitly set for handheld always wins. Default absent = off = handheld never inherits docked,
    which is the codebase-wide context convention (see lib/handheld_input)."""
    if not family:
        return None
    ctx = handheld_input.normalize(context)
    name = lookup(be_cfg, family, ctx)
    if (name is None and ctx == "handheld"
            and isinstance(be_cfg, dict) and be_cfg.get(MIRROR_KEY)):
        name = lookup(be_cfg, family, "docked")
    return name


def nth(base: str | None, ordinal: int, profile_dir, suffix: str) -> str | None:
    """``base`` bumped for the ``ordinal``-th connected pad of that family (0-based), so two
    same-family pads use DISTINCT device-bound profiles instead of both reusing the first.

    The map holds ONE stem per family = the FIRST pad's profile (e.g. "DualSense 1"); for the
    ordinal-th pad derive "<base><n+ordinal>" by bumping the trailing number ("DS 1" -> "DS 2").
    Falls back to ``base`` for ordinal 0, when ``base`` has no trailing number, or when the derived
    file does not exist -- so one profile per family, or non-numbered names, keep working.
    ``profile_dir`` is passed IN by the caller to keep this module a leaf."""
    if not base or ordinal <= 0:
        return base
    m = _TRAILING_NUM_RE.match(base)
    if not m:
        return base
    candidate = f"{m.group(1)}{int(m.group(2)) + ordinal}"
    try:
        if profile_dir is not None and (Path(profile_dir) / f"{candidate}{suffix}").is_file():
            return candidate
    except OSError:
        pass
    return base


def list_stems(profile_dir, suffix: str) -> list[str]:
    """Sorted valid stems of ``<profile_dir>/*<suffix>``; ``[]`` when the dir is absent/unreadable.

    NOTE pathlib's glob DOES match dotfiles (the stdlib ``glob`` module does not), so the
    ``valid_stem`` filter is load-bearing here, not decoration."""
    try:
        return sorted(p.stem for p in Path(profile_dir).expanduser().glob(f"*{suffix}")
                      if valid_stem(p.stem))
    except OSError:
        return []
