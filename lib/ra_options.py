"""Shared RetroArch per-system option definitions.

Single source of truth for the curated per-system RetroArch toggles. Rendered via
the RetroArch hub per-system editor's "RetroArch options" button, which opens the
per-system rasys_<system> settings namespace (``lib/madsrv/retroarch_settings.py``).

Each option:
  id      stable identifier (UI/RPC)
  label   shown text
  cfg_key the RetroArch config key written to ``config/<Core>/<system>.cfg``
  on      the value when enabled (off = the key is removed)
  systems ``"*"`` for any RetroArch system, or a set of system names
ON writes via ``retroarch_cfg.set_system_option`` (sentinel-managed, all cores).
"""
from __future__ import annotations

RA_SYSTEM_OPTIONS = [
    {"id": "n64_menu_text", "label": "Fix blank RetroArch menu text (force glcore)",
     "cfg_key": "video_driver", "on": "glcore", "systems": {"n64"}},
    {"id": "bilinear", "label": "Bilinear smoothing",
     "cfg_key": "video_smooth", "on": "true", "systems": "*"},
    {"id": "integer_scale", "label": "Integer scaling (sharp, pillarboxed)",
     "cfg_key": "video_scale_integer", "on": "true", "systems": "*"},
    {"id": "rewind", "label": "Rewind",
     "cfg_key": "rewind_enable", "on": "true", "systems": "*"},
]


def ra_options_for(sysname: str) -> list[dict]:
    """The RA option DEFS applicable to a system (n64 fix only on n64, etc.)."""
    return [o for o in RA_SYSTEM_OPTIONS
            if o["systems"] == "*" or sysname in o["systems"]]
