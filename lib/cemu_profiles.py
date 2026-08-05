"""Resolve a Cemu (Wii U) family x context input profile name from policy.

The family x context map lives in ``[backends.cemu.profile_map.<context>]`` as
``{family: "<profile stem>"}`` (see controller-policy.toml). This leaf answers the one
question the launch binder (lib/cemu_seat) and the MAD editor ask: "which native
``controllerProfiles/<stem>.xml`` is assigned to this controller FAMILY in this launch
context?" An unset / blank / absent entry returns ``None`` = leave that slot's resting
file untouched (never cleared).

PER-GAME (2026-08-04): a title may override any family in either context via
``[backends.cemu.pergame.<titleid>.<context>]``, the same ``{family: stem}`` shape. The launch
binder resolves the title id from the rom (cemu_games.titleid_for_rom) and hands the slice in;
an unknown title, or no per-game table, degrades to the global map. This deliberately does NOT
write Cemu's native ``gameProfiles/<tid>.ini [Controller]`` pin, which BYPASSES seating entirely
(see lib/madsrv/cemu_pg_input_cmds) -- the per-game pick feeds cemu_seat instead.

Family keys are the canonical ``routing.family_of`` names (DualSense, DualShock 4,
Wii Remote Pro, Steam Deck, 8BitDo, 8BitDo Pro, Xbox). Context is "docked" | "handheld".

The map lookup, the opt-in handheld mirror and the nth-same-family-pad derivation live in
lib/family_profiles (shared with the Yuzu-fork Switch emulators, which key their profiles the
same way and for the same reason). This module adds only the Cemu flavour: the ``.xml`` suffix
and the per-game tier.

Leaf module: imports only lib.handheld_input + lib.family_profiles, so the launch
hot path and hook-side CLIs stay cheap.
"""
from __future__ import annotations

from . import family_profiles, handheld_input

SUFFIX = ".xml"                       # Cemu's native controllerProfiles/<stem>.xml


def _lookup(cemu_cfg: dict, family: str, ctx: str) -> str | None:
    """The stem assigned to ``family`` in the ``ctx`` slice of profile_map, or ``None``. Husk-tolerant
    (a non-dict profile_map / slice, or a non-string value, degrades to ``None`` on the launch path).
    Kept as a name because callers and tests reference it."""
    return family_profiles.lookup(cemu_cfg, family, ctx)


def _pergame_lookup(pergame, family: str, ctx: str) -> str | None:
    """``pergame[ctx][family]``, husk-tolerant in exactly the same way as the global lookup."""
    if not isinstance(pergame, dict):
        return None
    slice_ = pergame.get(ctx)
    if not isinstance(slice_, dict):
        return None
    name = slice_.get(family)
    if not isinstance(name, str):
        return None
    return name.strip() or None


def pergame_slice(cemu_cfg: dict, titleid: str | None) -> dict:
    """``[backends.cemu.pergame.<titleid>]`` as a ``{context: {family: stem}}`` dict, or ``{}`` when
    the title id is absent / unknown / husked. Title ids are stored lowercase 16-hex, matching
    cemu_games.pergame_path and the res_presets store."""
    if not titleid or not isinstance(cemu_cfg, dict):
        return {}
    pg = cemu_cfg.get("pergame")
    if not isinstance(pg, dict):
        return {}
    entry = pg.get(str(titleid).strip().lower())
    return entry if isinstance(entry, dict) else {}


def resolve(pergame, cemu_cfg: dict, family: str | None, context: str) -> str | None:
    """The stem to apply for this launch: per-game[ctx] -> global[ctx] -> None.

    The opt-in ``handheld_mirrors_docked`` tier runs only for a HANDHELD launch whose handheld chain
    missed entirely, and it consults per-game DOCKED before global DOCKED -- otherwise a less
    specific global pick would shadow a more specific per-game one. Without the flag, handheld never
    inherits docked at all."""
    if not family:
        return None
    ctx = handheld_input.normalize(context)
    name = _pergame_lookup(pergame, family, ctx) or family_profiles.lookup(cemu_cfg, family, ctx)
    if (name is None and ctx == "handheld"
            and isinstance(cemu_cfg, dict) and cemu_cfg.get(family_profiles.MIRROR_KEY)):
        name = (_pergame_lookup(pergame, family, "docked")
                or family_profiles.lookup(cemu_cfg, family, "docked"))
    return name


def resolve_nth(pergame, cemu_cfg: dict, family: str | None, context: str,
                ordinal: int, cfg_dir) -> str | None:
    """``resolve`` for the ``ordinal``-th connected pad of ``family`` (0-based). The bump reads
    whatever ``resolve`` returned, so a per-game base derives its own "<base> 2" twin."""
    return family_profiles.nth(resolve(pergame, cemu_cfg, family, context),
                               ordinal, cfg_dir, SUFFIX)


def profile_for(cemu_cfg: dict, family: str | None, context: str) -> str | None:
    """The native profile stem assigned to ``family`` in ``context``
    ("docked"|"handheld"), or ``None`` when unset / blank / absent.

    ``cemu_cfg`` is the merged ``[backends.cemu]`` table. Tolerates a hand-edited
    husk (a non-dict profile_map / context slice, or a non-string value) by
    degrading to ``None`` rather than raising on the launch path.

    HANDHELD MIRROR: when ``[backends.cemu].handheld_mirrors_docked`` is set AND a
    HANDHELD family has no handheld entry, fall back to that family's DOCKED entry
    (opt-in "same as docked"). Default off = today's stock fallback, so with the
    flag absent this is byte-identical and the seating path is unchanged.

    The GLOBAL-only entry point (no per-game tier) -- callers that have a title in scope use
    ``resolve``. Kept so the MAD pages and the Preview, which are system-level, stay unchanged.
    """
    return resolve(None, cemu_cfg, family, context)


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
    leaf module). GLOBAL-only; the per-game twin is ``resolve_nth``."""
    return resolve_nth(None, cemu_cfg, family, context, ordinal, cfg_dir)
