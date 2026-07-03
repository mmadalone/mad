"""lib/es_find_rules.py — ensure custom_systems/es_find_rules.xml carries MAD's DYNAMIC find
rules for the custom Switch/Namco emulators (Citron/Eden/pcsx2x6), so es_systems.xml
can use %EMULATOR_<NAME>% instead of a hardcoded, versioned AppImage filename and an emulator
update needs no es_systems edit. Pairs with lib/mad_launch_wrap._dynamize (which rewrites the
es_systems commands to %EMULATOR_X%).

A custom es_find_rules.xml COMPLEMENTS the bundled one (ES-DE adds/overrides emulators by name),
so this file only needs OUR emulator blocks. transform() is PURE (text -> text) for golden tests;
it is ADDITIVE + IDEMPOTENT — it never removes the user's own <emulator> rules, only inserts one
of ours when that name is absent. ensure_find_rules() reads/writes the file (honors
$ESDE_APPDATA_DIR, default ~/ES-DE, identical to the wrap module). Shared by install.sh and
deck-post-update.sh so a fresh install or an EmuDeck/ES-DE re-setup re-establishes the rules.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

# name -> ordered staticpath globs (first existing match wins, ES-DE semantics). A clean stable
# name first, then a token glob that also matches a versioned build, then the other install dirs.
_RULES = [
    ("CITRON", ["~/Applications/Citron.AppImage", "~/Applications/*itron*.AppImage",
                "~/.local/share/applications/*itron*.AppImage",
                "~/.local/bin/*itron*.AppImage", "~/bin/*itron*.AppImage"]),
    ("EDEN", ["~/Applications/Eden.AppImage", "~/Applications/Eden-*.AppImage",
              "~/Applications/*eden*.AppImage", "~/.local/share/applications/Eden*.AppImage"]),
    ("PCSX2X6", ["~/Applications/pcsx2x6/pcsx2x6.AppImage",
                 "~/Applications/pcsx2x6/*.AppImage", "~/Applications/pcsx2x6*.AppImage"]),
]

_COMMENT = {
    "CITRON": "Nintendo Switch emulator Citron (Citron.AppImage, or any citron_* build)",
    "EDEN": "Nintendo Switch emulator Eden",
    "PCSX2X6": "Namco System 246/256 PCSX2 fork (pcsx2x6.AppImage, in its own subdir)",
}

_HEADER = (
    '<?xml version="1.0"?>\n'
    "<!--\n"
    "  Custom ES-DE find rules (managed by MAD lib/es_find_rules.py) — COMPLEMENTS the bundled\n"
    "  es_find_rules.xml (adds emulators by name; does not replace the bundled rules). Gives\n"
    "  DYNAMIC resolution for the custom Switch/Namco emulators so es_systems.xml can use\n"
    "  %EMULATOR_<NAME>% instead of a hardcoded, versioned AppImage filename. Re-established by\n"
    "  install.sh / deck-post-update.sh. Never edit the bundled file; keep customizations here.\n"
    "-->\n"
    "<ruleList>\n"
)
_FOOTER = "</ruleList>\n"


def _block(name: str) -> str:
    entries = dict(_RULES)[name]
    rows = "".join(f"            <entry>{e}</entry>\n" for e in entries)
    return (f'    <emulator name="{name}">\n'
            f"        <!-- {_COMMENT[name]} -->\n"
            f'        <rule type="staticpath">\n{rows}'
            f"        </rule>\n"
            f"    </emulator>\n")


def _canonical() -> str:
    return _HEADER + "".join(_block(n) for n, _ in _RULES) + _FOOTER


def transform(text: str) -> str:
    """Pure + additive + idempotent. Empty/blank/no-ruleList input -> the canonical file. Otherwise
    insert each of OUR emulator blocks that is not already present (matched by name=), before the
    final </ruleList>, leaving the user's own rules untouched."""
    if not text or not text.strip() or "</ruleList>" not in text:
        return _canonical()
    out = text
    for name, _ in _RULES:
        if re.search(r'<emulator\s+name="%s"' % re.escape(name), out):
            continue
        out = out.replace("</ruleList>", _block(name) + "</ruleList>", 1)
    return out


def ensure_find_rules(path: Path | None = None) -> bool:
    """Read custom_systems/es_find_rules.xml, apply transform(), write only if changed (creating
    the file + parent dir if absent). Returns True iff it wrote."""
    if path is None:
        base = os.environ.get("ESDE_APPDATA_DIR") or str(Path.home() / "ES-DE")
        path = Path(base) / "custom_systems" / "es_find_rules.xml"
    path = Path(path)
    old = path.read_text(encoding="utf-8") if path.is_file() else ""
    new = transform(old)
    if new != old:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new, encoding="utf-8")
        return True
    return False
