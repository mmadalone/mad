"""Resolve a Cemu (Wii U) family x context input profile name from policy.

The family x context map lives in ``[backends.cemu.profile_map.<context>]`` as
``{family: "<profile stem>"}`` (see controller-policy.toml). This leaf answers the one
question the launch binder (lib/cemu_seat) and the MAD editor ask: "which native
``controllerProfiles/<stem>.xml`` is assigned to this controller FAMILY in this launch
context?" An unset / blank / absent entry returns ``None`` = leave that slot's resting
file untouched (never cleared).

Family keys are the canonical ``routing.family_of`` names (DualSense, DualShock 4,
Wii Remote Pro, Steam Deck, 8BitDo, 8BitDo Pro, Xbox). Context is "docked" | "handheld".

Leaf module: imports only lib.handheld_input (for context normalisation), so the launch
hot path and hook-side CLIs stay cheap.
"""
from __future__ import annotations

import re
from pathlib import Path

from . import handheld_input

_TRAILING_NUM_RE = re.compile(r"^(.*?)(\d+)\s*$")


def profile_for(cemu_cfg: dict, family: str | None, context: str) -> str | None:
    """The native profile stem assigned to ``family`` in ``context``
    ("docked"|"handheld"), or ``None`` when unset / blank / absent.

    ``cemu_cfg`` is the merged ``[backends.cemu]`` table. Tolerates a hand-edited
    husk (a non-dict profile_map / context slice, or a non-string value) by
    degrading to ``None`` rather than raising on the launch path.
    """
    if not family:
        return None
    ctx = handheld_input.normalize(context)
    pm = cemu_cfg.get("profile_map") if isinstance(cemu_cfg, dict) else None
    if not isinstance(pm, dict):
        return None
    slice_ = pm.get(ctx)
    if not isinstance(slice_, dict):
        return None
    name = slice_.get(family)
    if not isinstance(name, str):
        return None
    return name.strip() or None


def profile_for_nth(cemu_cfg: dict, family: str | None, context: str,
                    ordinal: int, cfg_dir) -> str | None:
    """The profile for the ``ordinal``-th connected pad of ``family`` (0-based), so two same-family
    pads use DISTINCT device-bound profiles instead of both reusing the first.

    The map holds ONE stem per family = the FIRST pad's profile (e.g. "DualSense 1"). For the
    ordinal-th pad we auto-derive "<base> <n+ordinal>" by bumping the trailing number
    ("DualSense 1" -> "DualSense 2" for the 2nd DualSense, "WiiU Pro 1" -> "WiiU Pro 2", ...). Falls
    back to the base profile when ordinal 0, when the base has no trailing number, or when the derived
    file does not exist -- so a user who only has one profile per family, or non-numbered names, keeps
    today's behaviour. ``cfg_dir`` is the controllerProfiles dir (the caller passes it to keep this a
    leaf module)."""
    base = profile_for(cemu_cfg, family, context)
    if not base or ordinal <= 0:
        return base
    m = _TRAILING_NUM_RE.match(base)
    if not m:
        return base
    candidate = f"{m.group(1)}{int(m.group(2)) + ordinal}"
    try:
        if cfg_dir is not None and (Path(cfg_dir) / f"{candidate}.xml").is_file():
            return candidate
    except OSError:
        pass
    return base
