"""
Idempotently wrap a system's ES-DE launch command(s) in controller-router-wrap.sh,
inside ~/ES-DE/custom_systems/es_systems.xml, so the controller-router runs for
that system at launch (and its `[systems.<name>].ports` priority rule takes
effect — see controller-router._resolve_ports).

Reads the system's FULL definition from the bundled es_systems.xml via xml.etree,
re-emits it with every <command> wrapped (labels preserved so the gamelist's
<alternativeEmulator> choice still resolves), and TEXT-SPLICES it into the custom
file — never ET.write, which would reflow the file and drop its comments + the
user's other overrides (mugen, openbor, sinden, the console/CD entries). A `.bak`
is written before any change.

ES-DE must be restarted to pick up a newly-wrapped system (it caches es_systems
at startup); the policy rule itself is read fresh at each launch.

Stdlib only (no pip): xml.etree + re + shutil + pathlib.
"""
from __future__ import annotations

import os
import re
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

from . import esde_settings
from .esde_paths import bundled_es_systems
WRAP = str(Path(__file__).resolve().parent.parent / "controller-router-wrap.sh")
BUNDLED = bundled_es_systems()   # running AppImage mount → AppDir → /usr/share
CUSTOM = esde_settings.APPDATA / "custom_systems" / "es_systems.xml"   # honors $ESDE_APPDATA_DIR


def _xesc(s: str) -> str:
    """Escape XML element-text special chars (& and < are mandatory)."""
    return s.replace("&", "&amp;").replace("<", "&lt;")


def _xesc_attr(s: str) -> str:
    return _xesc(s).replace('"', "&quot;")


def _find_system(path: Path, system: str):
    """Return the <system> ET element named `system` in `path`, or None."""
    if not path.is_file():
        return None
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return None
    for el in root.findall("system"):
        if (el.findtext("name") or "").strip() == system:
            return el
    return None


def is_wrapped(system: str) -> bool:
    """True if the system's CUSTOM override already routes through the wrap.
    (A system not present in custom uses the bundled command = unwrapped.)"""
    el = _find_system(CUSTOM, system)
    if el is None:
        return False
    return any(WRAP in (c.text or "") for c in el.findall("command"))


def _render_block(el) -> str:
    """Build a wrapped <system> block (text) from a bundled <system> element."""
    name = (el.findtext("name") or "").strip()
    fullname = (el.findtext("fullname") or name).strip()
    path = (el.findtext("path") or f"%ROMPATH%/{name}").strip()
    ext = (el.findtext("extension") or "").strip()
    platform = (el.findtext("platform") or name).strip()
    theme = (el.findtext("theme") or name).strip()

    lines = ["    <system>",
             f"        <name>{name}</name>",
             f"        <fullname>{_xesc(fullname)}</fullname>",
             f"        <path>{path}</path>",
             f"        <extension>{_xesc(ext)}</extension>"]
    for c in el.findall("command"):
        text = " ".join((c.text or "").split())
        if not text:
            continue
        if WRAP not in text:  # don't double-wrap
            text = f'{WRAP} {name} %ROM% "%BASENAME%" "{fullname}" -- {text}'
        # ALWAYS emit a label. A label-less <command> makes ES-DE's per-game
        # "Alternative emulator" picker show the full (very long, wrapped) command
        # string — a wide, unreadable dialog. Fall back to "Default" when the
        # source command has none.
        label = (c.get("label") or "").strip() or "Default"
        lines.append(f'        <command label="{_xesc_attr(label)}">{_xesc(text)}</command>')
    lines.append(f"        <platform>{platform}</platform>")
    lines.append(f"        <theme>{theme}</theme>")
    lines.append("    </system>")
    return "\n".join(lines)


def _atomic_write(text: str) -> bool:
    """Validate `text` parses as XML, then atomically replace CUSTOM (tmp +
    os.replace) so a kill / disk-full mid-write can't truncate-corrupt the user's
    es_systems.xml (which ES-DE would then silently drop). Returns False without
    writing if the new text wouldn't parse."""
    try:
        ET.fromstring(text)
    except ET.ParseError:
        return False
    tmp = CUSTOM.with_suffix(CUSTOM.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, CUSTOM)
    return True


def wrap_system(system: str) -> bool:
    """Write a wrapped <system> block for `system` into the custom file.

    Idempotent: if a <system> with this name already exists in custom, its block
    is replaced; otherwise the new block is inserted before </systemList>.
    Returns False if the system can't be found to wrap (not in bundled/custom).
    """
    # Prefer the CUSTOM definition if one exists, so we wrap the user's actual
    # launch command (e.g. a Proton/openbor.sh override) rather than pulling the
    # stock bundled one — only fall back to bundled for systems with no override.
    src = _find_system(CUSTOM, system) or _find_system(BUNDLED, system)
    if src is None:
        return False
    block = _render_block(src)

    CUSTOM.parent.mkdir(parents=True, exist_ok=True)
    if not CUSTOM.is_file():
        return _atomic_write('<?xml version="1.0"?>\n<systemList>\n' + block
                             + "\n</systemList>\n")

    text = CUSTOM.read_text(encoding="utf-8")
    shutil.copy2(CUSTOM, str(CUSTOM) + ".bak")

    # Match the existing <system> block for this name (no </system> may appear
    # before the matching <name>, so we hit the right block). Splice via slicing
    # — NOT re.sub, whose replacement string would mangle backslashes in commands.
    pat = re.compile(
        r"[ \t]*<system>(?:(?!</system>).)*?<name>\s*"
        + re.escape(system) + r"\s*</name>.*?</system>[ \t]*\n?",
        re.DOTALL,
    )
    m = pat.search(text)
    if m:
        new_text = text[:m.start()] + block + "\n" + text[m.end():]
    else:
        idx = text.rfind("</systemList>")
        if idx == -1:
            return False
        new_text = text[:idx] + block + "\n" + text[idx:]
    return _atomic_write(new_text)
