"""Docked vs handheld launch context for standalone input overrides.

MAD's input override stores are keyed by context — `{ "docked": {...}, "handheld":
{...} }` — so a game can carry different button maps on the arcade cab and on the
couch. This module answers the one question the launch path asks: "which context am
I launching in?" The store is read for that context and, when a handheld map is
unset, the empty slice falls back to the emulator's STOCK default (the Steam Deck
pad's own layout) — never to the docked map. Handheld is its own independent axis.

Resolution mirrors lib/handheld_res (the canonical on-the-go launch consumer): the
on-the-go feature must be ENABLED, then deck_state decides docked/handheld (physical
DRM state, a policy force, or the MAD_FORCE_CONTEXT test hook). With the feature off —
or nothing configured — the context is "docked", i.e. exactly today's behaviour, so
shipping this changes nothing until a handheld map is deliberately set.
"""
from __future__ import annotations

import os

from . import deck_state

DOCKED = "docked"
HANDHELD = "handheld"
CONTEXTS = (DOCKED, HANDHELD)


def normalize(context) -> str:
    """Canonicalise any context-ish value to "docked" or "handheld" (default docked)."""
    return HANDHELD if str(context).strip().lower() == HANDHELD else DOCKED


def _policy_handheld() -> dict | None:
    """The merged policy's [handheld] table, or None if unavailable. Lazy import so this
    module stays importable in a bare unit test (no policy files present)."""
    try:
        from . import policy
        hh = policy.load_merged().get("handheld")
        return hh if isinstance(hh, dict) else None
    except Exception:
        return None


_UNSET = object()


def context(handheld_cfg=_UNSET) -> str:
    """The current launch context, "docked" or "handheld".

    `handheld_cfg` = the merged policy's [handheld] table; omit it to have this module
    load the policy itself (the usual launch call). The MAD_FORCE_CONTEXT env hook
    overrides everything (tests / scripts); otherwise the on-the-go feature must be
    enabled for a handheld context to ever be returned, matching lib/handheld_res.
    """
    hh = _policy_handheld() if handheld_cfg is _UNSET else handheld_cfg
    force = deck_state.resolve_force(hh if isinstance(hh, dict) else None)
    env = os.environ.get("MAD_FORCE_CONTEXT", "").strip().lower()
    if env not in CONTEXTS and not (isinstance(hh, dict) and hh.get("enabled", False)):
        return DOCKED          # feature off (or unconfigured) and not forced -> today's behaviour
    return HANDHELD if deck_state.is_handheld(force) else DOCKED
