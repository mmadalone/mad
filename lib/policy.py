"""Merged controller-policy view: controller-policy.toml deep-merged with the
machine-owned local overrides (controller-policy.local.toml).

Extracted verbatim from router-config-gui.py (MAD task #13 modularization) so
lib/ page mixins can call load_merged() without importing the main script.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

from . import localpolicy
from .routing import deep_merge

_LAUNCHERS = Path(__file__).resolve().parent.parent       # lib/.. = the launchers dir
POLICY = _LAUNCHERS / "controller-policy.toml"
LOCAL = _LAUNCHERS / "controller-policy.local.toml"


def load_merged() -> dict:
    base = {"systems": {}, "backends": {}}
    if POLICY.is_file():
        try:
            with POLICY.open("rb") as f:                  # context manager: no leaked handle (1.3)
                base = tomllib.load(f)
        except (tomllib.TOMLDecodeError, OSError):
            pass  # keep the safe default; a corrupt base must not brick the panel (C1.2)
    over = localpolicy.load(LOCAL)
    # Reuse routing.deep_merge — the SAME recursive merge the router itself uses —
    # so the MAD panel's view can never diverge from the launch-time resolution
    # (the old bespoke 2-level merge dropped nested per-system overrides). (1.0/N4.1)
    return deep_merge(base, over)
