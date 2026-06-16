"""Tiny JSON read/write helper for Ryujinx's ~/.config/Ryujinx/Config.json.

cfgutil is INI-only; Ryujinx stores everything (settings + `input_config`) in
JSON. Used by ryujinx_cmds (settings) and ryujinx_input_cmds (input). A full
parse → modify → dump round-trip (Ryujinx is not byte-sensitive and rewrites the
file itself on exit); a one-time `.router-backup` is taken before MAD's first
write. fsutil.atomic_write_text bumps the staterev "config" revision, so the
panel's cached pages refresh after a change.
"""
from __future__ import annotations

import json
from pathlib import Path

from .. import fsutil

CONFIG = Path.home() / ".config/Ryujinx/Config.json"


def load(path: Path | None = None) -> dict:
    """Parse Config.json (raises on a missing/invalid file — callers guard).
    `path` defaults to CONFIG, resolved at CALL time (so it stays patchable)."""
    return json.loads((path or CONFIG).read_text(encoding="utf-8"))


def write(data: dict, path: Path | None = None) -> None:
    """One-time .router-backup, then atomic-write the re-serialized JSON.
    `path` defaults to CONFIG, resolved at CALL time."""
    path = path or CONFIG
    fsutil.ensure_pristine_backup(path)   # one pristine .router-backup (defers to a sibling .bak)
    fsutil.atomic_write_text(path, json.dumps(data, indent=2) + "\n")
