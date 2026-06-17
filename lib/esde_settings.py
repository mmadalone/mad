"""
Read a few values out of ES-DE's own settings + the active theme so the
router-config GUI can match ES-DE (theme palette/font, navigation sounds).

ES-DE stores `es_settings.xml` as a flat list of typed elements:
    <string name="Theme" value="pixel-es-de" />
    <bool   name="NavigationSounds" value="true" />
    <int    name="SoundVolumeNavigation" value="70" />

Stdlib-only (xml.etree); every getter is best-effort with a default, so a
missing/corrupt settings file never breaks the GUI — it just falls back.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from xml.etree import ElementTree as ET

# ES-DE's application-data dir (themes + settings). Honor the env override ES-DE
# itself uses, else the standard ~/ES-DE location on the Deck.
APPDATA = Path(os.environ.get("ESDE_APPDATA_DIR", str(Path.home() / "ES-DE")))
SETTINGS = APPDATA / "settings" / "es_settings.xml"


def _parse(path: Path) -> dict:
    """Flatten es_settings.xml into {name: value} with python-typed values.

    ES-DE's settings file is a FLAT list of elements with no single root (just
    an `<?xml?>` declaration then `<bool/>`/`<int/>`/`<string/>` siblings), which
    `ET.parse` rejects — so strip the declaration and wrap in a synthetic root."""
    out: dict = {}
    if not path.is_file():
        return out
    try:
        txt = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    # Drop the XML declaration (if any) and wrap the sibling elements.
    body = txt.split("?>", 1)[1] if "?>" in txt else txt
    try:
        root = ET.fromstring(f"<es>{body}</es>")
    except ET.ParseError:
        return out
    for el in root:
        name = el.get("name")
        if not name:
            continue
        raw = el.get("value", "")
        if el.tag == "bool":
            out[name] = (raw.strip().lower() == "true")
        elif el.tag == "int":
            try:
                out[name] = int(raw)
            except ValueError:
                out[name] = 0
        else:
            out[name] = raw
    return out


def read() -> dict:
    """Public snapshot of the settings the GUI cares about, with safe defaults."""
    s = _parse(SETTINGS)
    return {
        "theme": s.get("Theme", ""),
        "variant": s.get("ThemeVariant", ""),
        "color_scheme": s.get("ThemeColorScheme", "none"),
        "nav_sounds": bool(s.get("NavigationSounds", True)),
        "nav_volume": int(s.get("SoundVolumeNavigation", 80)),
        "user_theme_dir": s.get("UserThemeDirectory", ""),
    }


def themes_dirs() -> list[Path]:
    """Directories ES-DE searches for themes (user dir first if configured)."""
    dirs: list[Path] = []
    udir = read().get("user_theme_dir") or ""
    if udir:
        dirs.append(Path(udir).expanduser())
    dirs.append(APPDATA / "themes")
    return [d for d in dirs if d.is_dir()]


def active_theme_dir() -> Path | None:
    """Path to the currently-selected theme's folder, or None if not found."""
    name = read().get("theme") or ""
    if not name:
        return None
    for base in themes_dirs():
        cand = base / name
        if cand.is_dir():
            return cand
    return None


def set_value(name: str, value: str, *, settings: Path | None = None) -> bool:
    """Set a string setting in es_settings.xml (e.g. Theme) atomically.

    Replaces the ``value="…"`` of the existing ``<string name="NAME" …/>`` element,
    or appends a new ``<string name="NAME" value="VALUE" />`` if absent. Preserves
    the rest of the file byte-for-byte (regex value-swap, not an XML re-serialize,
    since ES-DE's file is a flat element list with no single root).

    Returns True if the file was changed, False if it was already set to ``value``
    (no write) or the settings file doesn't exist (best-effort).

    CALLER MUST ensure ES-DE is NOT running — it rewrites es_settings.xml on exit
    and would clobber this (CLAUDE rule #3). Writes via fsutil.atomic_write_text.
    """
    from . import fsutil
    target = Path(settings) if settings is not None else SETTINGS
    if not target.is_file():
        return False
    txt = target.read_text(encoding="utf-8", errors="replace")
    esc = (value.replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;"))
    # ES-DE serializes name then value with single spaces, e.g.
    #   <string name="Theme" value="pixel-es-de" />
    pat = re.compile(r'(<(?:string|bool|int)\s+name="%s"\s+value=")([^"]*)(")'
                     % re.escape(name))
    m = pat.search(txt)
    if m:
        if m.group(2) == esc:
            return False                       # already set — no write
        new = txt[:m.start(2)] + esc + txt[m.end(2):]
    else:                                      # absent → append as a <string>
        new = (txt if txt.endswith("\n") else txt + "\n") \
            + f'<string name="{name}" value="{esc}" />\n'
    fsutil.atomic_write_text(target, new)
    return True


if __name__ == "__main__":   # quick manual check
    import json
    info = read()
    info["active_theme_dir"] = str(active_theme_dir())
    print(json.dumps(info, indent=2))
