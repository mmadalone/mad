"""Seed a MINIMAL ~/ES-DE/custom_systems/es_systems.xml for a STANDALONE (no-EmuDeck)
MAD install.

ES-DE's bundled es_systems.xml already defines ~195 systems and es_find_rules.xml
resolves %EMULATOR_*% to whatever the user installed, so a standalone setup does NOT
need a full inventory. `es_systems.load_systems()` overlays the custom file on the
bundled one BY <name>, so any system we OMIT keeps its full bundled definition. We add
only two groups:

  * Category A — the standalones MAD binds controllers for at launch (switch, ps2,
    ps3, xbox). Synthesized FROM the running ES-DE's bundled definition: each
    "(Standalone)" command is wrapped with mad-switch-launch.py (switch) or
    mad-standalone-launch.py (ps2/ps3/xbox) so the launch-time binder runs. This does
    NOT depend on EmuDeck's command labels (bundled labels differ, e.g.
    "RPCS3 Directory (Standalone)") — it matches any "(Standalone)" command.

  * Category B — MAD's own special systems whose launch command stock ES-DE doesn't
    provide (model2, mugen, openbor, naomi, gameandwatch, daphne). Copied
    VERBATIM from the committed template data/standalone/es_systems.mad-special.xml,
    with the %MAD_LAUNCHERS% placeholder resolved to this install's launchers dir.

Idempotent + reversible: validates the result parses as XML, writes a .bak before any
change, only ADDS a <system> whose <name> is absent (never clobbers a curated block),
and never double-wraps. Mirrors lib/es_systems_wrap.py's atomic-write + slice-splice.

This is the STANDALONE path only. The EmuDeck install path keeps its own in-place
wrapping (install.sh) so existing installs are byte-for-byte unchanged.

Stdlib only.
"""
from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from .esde_paths import bundled_es_systems

# Category A: system -> launch binder. switch uses the switch binder; the disc-based
# standalones use the generic one. Per-command emulator token is derived from the label.
_CAT_A = ("switch", "ps2", "ps3", "xbox")


def _launchers_dir() -> Path:
    return Path(__file__).resolve().parent.parent


def _binder(system: str, launchers: Path) -> str:
    name = "mad-switch-launch.py" if system == "switch" else "mad-standalone-launch.py"
    return str(launchers / name)


def _emu_for(system: str, label: str) -> str | None:
    """The emulator token MAD binds for a given Cat-A (Standalone) command label."""
    if system == "switch":
        if "Ryujinx" in label:
            return "ryujinx"
        if "Eden" in label:
            return "eden"
        return None
    return {"ps2": "pcsx2", "ps3": "rpcs3", "xbox": "xemu"}.get(system)


def _xesc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;")


def _xesc_attr(s: str) -> str:
    return _xesc(s).replace('"', "&quot;")


def existing_names(text: str) -> set[str]:
    """The <name>s already defined in a custom es_systems.xml text."""
    return {m.strip() for m in re.findall(r"<name>\s*([^<]+?)\s*</name>", text)}


def _bundled_el(bundled_path: Path, system: str):
    try:
        root = ET.parse(bundled_path).getroot()
    except (ET.ParseError, OSError):
        return None
    for el in root.findall("system"):
        if (el.findtext("name") or "").strip() == system:
            return el
    return None


def render_cat_a(el, system: str, launchers: Path) -> str:
    """Render a wrapped <system> block from a bundled Cat-A <system> element: every
    "(Standalone)" command gets the launch binder prepended (idempotent)."""
    name = (el.findtext("name") or system).strip()
    fullname = (el.findtext("fullname") or name).strip()
    path = (el.findtext("path") or f"%ROMPATH%/{name}").strip()
    ext = (el.findtext("extension") or "").strip()
    platform = (el.findtext("platform") or name).strip()
    theme = (el.findtext("theme") or name).strip()
    binder = _binder(system, launchers)

    lines = ["    <system>",
             f"        <name>{name}</name>",
             f"        <fullname>{_xesc(fullname)}</fullname>",
             f"        <path>{path}</path>",
             f"        <extension>{_xesc(ext)}</extension>"]
    for c in el.findall("command"):
        text = " ".join((c.text or "").split())
        if not text:
            continue
        label = (c.get("label") or "").strip() or "Default"
        emu = _emu_for(system, label)
        if emu and "(Standalone)" in label and binder not in text:
            text = f"{binder} {emu} %ROM% -- {text}"
        lines.append(f'        <command label="{_xesc_attr(label)}">{_xesc(text)}</command>')
    lines.append(f"        <platform>{platform}</platform>")
    lines.append(f"        <theme>{theme}</theme>")
    lines.append("    </system>")
    return "\n".join(lines)


def template_blocks(template_path: Path, launchers: Path) -> list[tuple[str, str]]:
    """Parse the Cat-B template into [(name, block_text), ...], with %MAD_LAUNCHERS%
    resolved. block_text is the verbatim <system>...</system> slice from the file so
    comments/structure are preserved (we never re-serialize via ET)."""
    if not template_path.is_file():
        return []
    raw = template_path.read_text(encoding="utf-8").replace("%MAD_LAUNCHERS%", str(launchers))
    out = []
    for m in re.finditer(r"[ \t]*<system>.*?</system>", raw, re.DOTALL):
        block = m.group(0)
        nm = re.search(r"<name>\s*([^<]+?)\s*</name>", block)
        if nm:
            out.append((nm.group(1).strip(), block.rstrip("\n")))
    return out


def _splice_before_close(text: str, block: str) -> str:
    idx = text.rfind("</systemList>")
    if idx == -1:
        return text
    return text[:idx] + block + "\n" + text[idx:]


def _atomic_write(path: Path, text: str) -> bool:
    """Validate XML, then tmp+os.replace so a crash can't truncate the file."""
    try:
        ET.fromstring(text)
    except ET.ParseError:
        return False
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
    return True


def seed_standalone(custom_path: Path,
                    bundled_path: Path | None = None,
                    template_path: Path | None = None,
                    launchers: Path | None = None) -> dict:
    """Seed/refresh the minimal standalone custom_systems file. Returns a summary
    dict {created, added:[names], skipped:[names]}. Idempotent: re-running adds
    nothing already present. Returns {"error": ...} on a validation failure (no write).
    """
    launchers = launchers or _launchers_dir()
    bundled_path = bundled_path or bundled_es_systems()
    template_path = template_path or (launchers / "data" / "standalone" / "es_systems.mad-special.xml")

    custom_path.parent.mkdir(parents=True, exist_ok=True)
    created = not custom_path.is_file()
    text = ('<?xml version="1.0"?>\n<systemList>\n</systemList>\n'
            if created else custom_path.read_text(encoding="utf-8"))
    if "</systemList>" not in text:
        return {"error": "custom es_systems.xml has no </systemList>"}

    if not created:
        # one .bak before we touch a user's existing file
        bak = custom_path.with_suffix(custom_path.suffix + ".bak")
        bak.write_text(text, encoding="utf-8")

    have = existing_names(text)
    added, skipped, unavailable = [], [], []

    # Category B — verbatim template blocks (name-gated)
    for name, block in template_blocks(template_path, launchers):
        if name in have:
            skipped.append(name)
            continue
        text = _splice_before_close(text, block)
        have.add(name)
        added.append(name)

    # Category A — synthesized wrapped blocks from bundled (name-gated). If the bundled
    # es_systems.xml isn't resolvable (e.g. the AppDir hasn't been extracted yet), the
    # system can't be synthesized — record it as UNAVAILABLE rather than dropping it
    # silently, so the caller can warn instead of reporting a clean success.
    for system in _CAT_A:
        if system in have:
            skipped.append(system)
            continue
        el = _bundled_el(bundled_path, system)
        if el is None:
            unavailable.append(system)
            continue
        text = _splice_before_close(text, render_cat_a(el, system, launchers))
        have.add(system)
        added.append(system)

    if not _atomic_write(custom_path, text):
        return {"error": "generated es_systems.xml did not parse — not written"}
    return {"created": created, "added": added, "skipped": skipped,
            "unavailable": unavailable, "bundled": str(bundled_path)}


if __name__ == "__main__":   # manual: seed into a target custom file
    import json
    import sys
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else (
        Path.home() / "ES-DE" / "custom_systems" / "es_systems.xml")
    print(json.dumps(seed_standalone(target), indent=2))
