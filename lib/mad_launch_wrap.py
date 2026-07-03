"""lib/mad_launch_wrap.py — wrap the Switch / PS2 / PS3 / Xbox <command>s in
custom_systems/es_systems.xml with MAD's launch binders, so controllers are bound at
launch and restored on exit:
  * Switch (Ryujinx / Eden)  -> mad-switch-launch.py
  * PS2 / PS3 / Xbox         -> mad-standalone-launch.py   (+ inject an Xbox <system> if absent)
Idempotent (a command already wrapped is left alone).

Extracted VERBATIM from the identical inline python block that was copy-pasted into
install.sh and deck-post-update.sh, so there is ONE copy. transform() is pure (input text
-> output text) for golden tests; wrap_console_launchers() reads the file, applies
transform(), and writes only if it changed. The binder paths are derived from __file__
(this lib lives in the launchers repo), replacing the old hardcoded /home/deck/... strings.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

_LAUNCHERS = Path(__file__).resolve().parent.parent
W = str(_LAUNCHERS / "mad-switch-launch.py")       # Switch launch binder
S = str(_LAUNCHERS / "mad-standalone-launch.py")   # PS2/PS3/Xbox launch binder


def _wrap(text, label, emu):
    pat = re.compile(r'(<command label="%s \(Standalone\)">)(?!%s)(.*?)(</command>)'
                     % (re.escape(label), re.escape(W)))
    return pat.sub(lambda m: f'{m.group(1)}{W} {emu} %ROM% -- {m.group(2)}{m.group(3)}', text)


def _rewrap(text, label, emu):
    # Migrated standalone: replace the command (possibly controller-router-wrap.sh-wrapped)
    # with the mad-standalone-launch.py binder. Idempotent.
    pat = re.compile(r'(<command label="%s \(Standalone\)">)(?!\s*%s)(.*?)(</command>)'
                     % (re.escape(label), re.escape(S)), re.S)

    def sub(m):
        inner = m.group(2).strip()
        mm = re.match(r'\S*controller-router-wrap\.sh\s+\S+\s+%ROM%\s+"[^"]*"\s+"[^"]*"\s+--\s+(.*)',
                      inner, re.S)
        real = (mm.group(1) if mm else inner).strip()
        return f'{m.group(1)} {S} {emu} %ROM% -- {real} {m.group(3)}'

    return pat.sub(sub, text)


# Convert a hardcoded ~/Applications AppImage path for a CUSTOM Switch/Namco emulator to
# %EMULATOR_<NAME>%, so es_systems.xml resolves it via the custom es_find_rules.xml (see
# lib/es_find_rules.py) and an emulator update (new AppImage filename) needs no es_systems edit.
# Idempotent (once replaced, the %EMULATOR_% token no longer matches) and SPECIFIC — it only
# touches ~/Applications/<...>.AppImage tokens for these emulators, leaving data paths (e.g. the
# pcsx2x6 retail -datapath .../pcsx2x6-retail dir) and every other emulator's command untouched.
# Ryujinx/xemu are already dynamic via the BUNDLED find rules, so they are not listed here.
_APP = r'[^\s<>"]*/Applications/'
_DYN = [
    (re.compile(_APP + r'pcsx2x6/[^\s<>"]*\.AppImage'), "%EMULATOR_PCSX2X6%"),
    (re.compile(_APP + r'[^\s<>"]*[Cc]itron[^\s<>"]*\.AppImage'), "%EMULATOR_CITRON%"),
    (re.compile(_APP + r'[Ee]den[^\s<>"]*\.AppImage'), "%EMULATOR_EDEN%"),
]


def _dynamize(text):
    for pat, rep in _DYN:
        text = pat.sub(rep, text)
    return text


def _inject_xbox(text):
    # xbox is bundled-only by default — add a wrapped <system> if entirely absent.
    if "<name>xbox</name>" in text:
        return text
    block = (
        '    <system>\n        <name>xbox</name>\n'
        '        <fullname>Microsoft Xbox</fullname>\n'
        '        <path>%ROMPATH%/xbox</path>\n'
        '        <extension>.iso .ISO .xiso .XISO</extension>\n'
        f'        <command label="xemu (Standalone)">{S} xemu %ROM% -- '
        '%INJECT%=%BASENAME%.esprefix %EMULATOR_XEMU% -dvd_path %ROM%</command>\n'
        '        <platform>xbox</platform>\n        <theme>xbox</theme>\n    </system>\n')
    return text.replace("</systemList>", block + "</systemList>", 1)


def transform(text: str) -> str:
    """Pure: apply all wraps/injects to es_systems.xml text. Idempotent."""
    # Switch: Ryujinx/Eden/Citron all route through the Switch launch binder.
    t = _wrap(_wrap(_wrap(text, "Ryujinx", "ryujinx"), "Eden", "eden"), "Citron", "citron")
    t = _rewrap(t, "PCSX2", "pcsx2")   # ps2 -> Standalones launch binder
    t = _inject_xbox(t)                # xbox: add if absent (bundled-only by default)
    t = _rewrap(t, "xemu", "xemu")     # then ensure its xemu command is wrapped
    t = _rewrap(t, "RPCS3", "rpcs3")   # ps3 -> Standalones launch binder
    t = _dynamize(t)                   # hardcoded custom-emulator AppImage paths -> %EMULATOR_X%
    return t


def wrap_console_launchers(path: Path | None = None) -> bool:
    """Read custom_systems/es_systems.xml, apply transform(), write only if changed.
    Returns True iff it wrote. Honors $ESDE_APPDATA_DIR (defaults to ~/ES-DE — identical
    to the old inline blocks when unset)."""
    if path is None:
        base = os.environ.get("ESDE_APPDATA_DIR") or str(Path.home() / "ES-DE")
        path = Path(base) / "custom_systems" / "es_systems.xml"
    path = Path(path)
    if not path.is_file():
        return False
    t = path.read_text(encoding="utf-8")
    t2 = transform(t)
    if t2 != t:
        path.write_text(t2, encoding="utf-8")
        return True
    return False
