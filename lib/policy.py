"""Merged controller-policy view: controller-policy.toml deep-merged with the
machine-owned local overrides (controller-policy.local.toml).

Extracted verbatim from router-config-gui.py (MAD task #13 modularization) so
lib/ page mixins can call load_merged() without importing the main script.
"""
import tomllib
from pathlib import Path

from . import localpolicy

_LAUNCHERS = Path(__file__).resolve().parent.parent       # lib/.. = the launchers dir
POLICY = _LAUNCHERS / "controller-policy.toml"
LOCAL = _LAUNCHERS / "controller-policy.local.toml"


def load_merged() -> dict:
    base = {"systems": {}, "backends": {}}
    if POLICY.is_file():
        base = tomllib.load(POLICY.open("rb"))
    over = localpolicy.load(LOCAL)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            for kk, vv in v.items():
                if isinstance(vv, dict) and isinstance(base[k].get(kk), dict):
                    base[k][kk].update(vv)
                else:
                    base[k][kk] = vv
        else:
            base[k] = v
    return base
