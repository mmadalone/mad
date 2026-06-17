"""
Tiny section-targeted INI/TOML editor shared by the standalone backends
(pcsx2_cfg, xemu_cfg, eden_cfg). It replaces ONE `[section]` block in place and
leaves the rest of the file byte-for-byte intact — important because PCSX2 /
xemu / Eden each rewrite their config on exit and we only own a few sections.
This is deliberately not a full parser: section names match literally (so
`[input.bindings]` and `[EmuCore/GS]` work), bodies are raw text.
"""
from __future__ import annotations

import re


def section_body(text: str, name: str) -> str | None:
    """Body (lines after the `[name]` header, trailing blanks stripped) or None."""
    m = re.search(rf"(?ms)^\[{re.escape(name)}\]\n(.*?)(?=^\[|\Z)", text)
    return m.group(1).rstrip("\n") if m else None


def set_section(text: str, name: str, body: str) -> str:
    """Replace (or append) the `[name]` section with `body` (no header line),
    preserving the rest of the file. One trailing blank line separates sections."""
    block = f"[{name}]\n{body}\n\n" if body else f"[{name}]\n\n"
    pat = re.compile(rf"(?ms)^\[{re.escape(name)}\]\n.*?(?=^\[|\Z)")
    if pat.search(text):
        return pat.sub(lambda _m: block, text, count=1)
    if text and not text.endswith("\n"):
        text += "\n"
    return text + block


def remove_section(text: str, name: str) -> str:
    """Delete the `[name]` section (header + body, through the blank line before the
    next `[section]` or EOF), preserving the rest. No-op if the section is absent.
    Used to undo a section a transient writer ADDED (e.g. PCSX2 multitap [PadN]/[Pad])
    so an on-exit restore returns the file to its pre-bind shape."""
    pat = re.compile(rf"(?ms)^\[{re.escape(name)}\]\n.*?(?=^\[|\Z)")
    return pat.sub("", text, count=1)
