"""Resolve an Eden / Citron (Yuzu-fork, Switch) family x context input profile stem from policy.

WHY FAMILY-KEYED AND NOT PER-PLAYER. A Yuzu-fork profile (``<input dir>/<stem>.ini``, authored in
the emulator's own Controls dialog) is DEVICE-BOUND twice over: every binding value embeds the pad's
SDL guid (``engine:sdl,port:0,guid:030000004c05...,button:1``) and the raw button/axis indices differ
per pad model (d-pad up is ``button:13`` on a Wii U Pro but ``button:11`` on a DualSense; ZL is a
digital button on one and ``axis:4`` on the other). There is no symbolic GameController layer to
retarget through. On top of that ``eden_cfg.assign_devices`` re-resolves every slot from the pad that
actually landed in it, so a per-PLAYER pick would simply be overwritten before the game starts.

Keyed by controller FAMILY it works, and is the same model Cemu uses for the same reason:

    [backends.<eden|citron>.profile_map.<docked|handheld>]
    DualSense = "DS 1"                # value = input/<stem>.ini, no extension, no path

``eden_cfg._retarget`` rewrites the ``guid:`` and ``port:`` of every value it writes, so a profile
authored for the FIRST pad of a family is automatically correct for the second one -- which is what
makes "DS 1" -> "DS 2" (family_profiles.nth) meaningful rather than cosmetic.

The map lookup, the opt-in handheld mirror and the nth derivation live in lib/family_profiles,
shared with lib/cemu_profiles. This module adds the Yuzu flavour: the ``.ini`` suffix, the input dir,
and ``profile_keys`` -- the bridge from "which family" to the ``(vidpid, port ordinal)`` key that
``eden_cfg.assign_devices`` can consume.

Leaf module: imports only lib.handheld_input + lib.family_profiles. It is deliberately
family-IGNORANT -- ``routing.family_of`` is INJECTED by the caller -- so the launch hot path does not
drag lib.routing (and through it lib.devices) into a resolver.
"""
from __future__ import annotations

from pathlib import Path

from . import family_profiles, handheld_input

SUFFIX = ".ini"

_DEFAULT_INPUT_DIRS = {"eden": "~/.config/eden/input", "citron": "~/.config/citron/input"}


def valid_stem(s) -> bool:
    """A safe profile stem (no path separators, no ``..``, no leading dot)."""
    return family_profiles.valid_stem(s)


def input_dir(backend: str, be_cfg: dict | None = None) -> Path:
    """The emulator's named-profile dir. Derived from the backend's ``template_profile`` (its
    parent), so a Flatpak or portable tree resolves too; falls back to the XDG default."""
    if isinstance(be_cfg, dict):
        v = be_cfg.get("template_profile")
        if isinstance(v, str) and v.strip():
            return Path(v.strip()).expanduser().parent
    return Path(_DEFAULT_INPUT_DIRS.get(backend, f"~/.config/{backend}/input")).expanduser()


def list_stems(idir) -> list[str]:
    """Sorted valid stems of ``<idir>/*.ini``; ``[]`` when absent/unreadable."""
    return family_profiles.list_stems(idir, SUFFIX)


def profile_for(be_cfg: dict, family: str | None, context: str) -> str | None:
    """The profile stem assigned to ``family`` in ``context``, or ``None`` when unset."""
    return family_profiles.resolve(be_cfg, family, context)


def profile_for_nth(be_cfg: dict, family: str | None, context: str,
                    ordinal: int, idir) -> str | None:
    """``profile_for`` bumped for the ``ordinal``-th connected pad of that family (0-based)."""
    return family_profiles.nth(profile_for(be_cfg, family, context), ordinal, idir, SUFFIX)


def profile_keys(pads, be_cfg: dict, context: str, idir, family_of) -> dict:
    """``{(vidpid, port_ordinal): stem}`` for ``eden_cfg.assign_devices(profiles=...)``.

    ``pads`` MUST be the same ordered list ``assign_devices`` will enumerate, because the key's
    second element is the per-``vid:pid`` ordinal that its own ``seen`` loop computes. Two counters
    run here and they are NOT the same:

      * ``seen``    -- per vid:pid, and the key ``assign_devices`` matches on.
      * ``fam_ord`` -- per FAMILY, and what selects "DS 1" vs "DS 2". A family can span several pids
        (routing._DS4_PIDS maps three to "DualShock 4"), so a DS4 v1 plus a DS4 v2 are ordinals 0
        and 1 of ONE family while being ordinal 0 of two different vid:pids.

    The ``seen`` counter is bumped for EVERY pad, including pads with no family and pads with no
    pick -- skipping one would silently desync every later key from assign_devices' own numbering
    and hand a profile to the wrong slot.

    Existence of the resolved stem is NOT checked here: ``eden_cfg._profile_block`` is the single
    gate for that and returns ``{}`` (falsy) for a missing file, which falls through to the resting
    ladder. Keeping one owner avoids two places disagreeing about what "unset" means.

    ``family_of`` is injected (a callable taking a pad, returning a family name or ``None``)."""
    out: dict = {}
    seen: dict = {}
    fam_ord: dict = {}
    for d in pads or ():
        vp = getattr(d, "vidpid", "") or ""
        port = seen.get(vp, 0)
        seen[vp] = port + 1                      # bump FIRST, unconditionally: see the docstring
        try:
            fam = family_of(d)
        except Exception:
            fam = None
        if not fam:
            continue
        k = fam_ord.get(fam, 0)
        fam_ord[fam] = k + 1
        stem = profile_for_nth(be_cfg, fam, context, k, idir)
        if stem and valid_stem(stem):
            out[(vp, port)] = stem
    return out
