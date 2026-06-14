"""Locate the RUNNING ES-DE's bundled resources (es_systems.xml, find rules, …)
robustly and portably, instead of hardcoding a manually-extracted AppDir path.

Resolution order (first that exists wins):
  1. $ESDE_RESOURCES                     — explicit override (an es-de resources dir)
  2. /tmp/.mount_ES-DE*/usr/share/es-de/resources  — the live AppImage mount
  3. ~/Applications/ES-DE{-MAD,}.AppDir/usr/share/es-de/resources — permanently-extracted AppDir (the wrapper's runtime build)
  4. ~/AppDir/usr/share/es-de/resources  — legacy manual extraction (this Deck)
  5. /usr/share/es-de/resources          — distro/system install

Falls back to the legacy path even if nothing exists, so callers that guard with
`.is_file()` behave exactly as before (empty result) rather than crashing.
Stdlib only.
"""
from __future__ import annotations

import glob
import os
from pathlib import Path

_LEGACY = Path.home() / "AppDir" / "usr" / "share" / "es-de" / "resources"


def esde_resources() -> Path:
    env = os.environ.get("ESDE_RESOURCES")
    if env and (Path(env) / "systems").is_dir():
        return Path(env)
    # The running AppImage mounts its read-only resources here (random suffix);
    # prefer the newest mount so we read the ACTUAL running ES-DE's system set.
    for m in sorted(glob.glob("/tmp/.mount_ES-DE*/usr/share/es-de/resources"),
                    reverse=True):
        if (Path(m) / "systems").is_dir():
            return Path(m)
    for cand in (Path.home() / "Applications" / "ES-DE-MAD.AppDir" / "usr" / "share" / "es-de" / "resources",
                 Path.home() / "Applications" / "ES-DE.AppDir" / "usr" / "share" / "es-de" / "resources",
                 _LEGACY, Path("/usr/share/es-de/resources")):
        if (cand / "systems").is_dir():
            return cand
    return _LEGACY


def bundled_es_systems(os_dir: str = "linux") -> Path:
    """Path to the running ES-DE's stock es_systems.xml for the given OS dir."""
    return esde_resources() / "systems" / os_dir / "es_systems.xml"
