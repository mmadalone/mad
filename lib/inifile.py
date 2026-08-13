"""
Tiny section-targeted INI/TOML editor shared by the standalone backends
(pcsx2_cfg, xemu_cfg, eden_cfg). It replaces ONE `[section]` block in place and
leaves the rest of the file byte-for-byte intact — important because PCSX2 /
xemu / Eden each rewrite their config on exit and we only own a few sections.
This is deliberately not a full parser: section names match literally (so
`[input.bindings]` and `[EmuCore/GS]` work), bodies are raw text.

CRLF: some of these config files are written with Windows line endings. The
header match tolerates an optional \\r before the \\n so a CRLF `[Controls]`
header is not invisible (an invisible header used to make set_section APPEND
a second copy of the section instead of replacing the first one — see audit
phase 4a). set_section detects the file's existing line-ending style (CRLF if
`\\r\\n` appears anywhere in the text, else LF) and writes the replacement
block in that same style, so a CRLF file stays entirely CRLF.

Every existing caller (lib/pcsx2_cfg.py, lib/xemu_cfg.py, lib/eden_cfg.py and
the madsrv writers) passes plain LF text today, and on an LF file this module
must keep behaving byte-identically to before the CRLF fix — that is the most
important property here, since those callers are not being touched.
"""
from __future__ import annotations

import re


def section_body(text: str, name: str) -> str | None:
    """Body (lines after the `[name]` header, trailing blanks stripped) or None."""
    m = re.search(rf"(?ms)^\[{re.escape(name)}\]\r?\n(.*?)(?=^\[|\Z)", text)
    return m.group(1).rstrip("\r\n") if m else None


def set_section(text: str, name: str, body: str) -> str:
    """Replace (or append) the `[name]` section with `body` (no header line),
    preserving the rest of the file. One trailing blank line separates sections.

    Preserves the file's existing line-ending style (CRLF in, CRLF out; LF in,
    LF out; empty/absent text defaults to LF, same as before this function
    learned about CRLF). `body` itself may use either style internally - it is
    normalised to \\n and then re-rendered in whatever style the target file
    uses, so callers never need to care.
    """
    eol = "\r\n" if "\r\n" in text else "\n"
    norm_body = body.replace("\r\n", "\n").replace("\r", "\n") if body else ""
    if eol != "\n":
        norm_body = norm_body.replace("\n", eol)
    block = f"[{name}]{eol}{norm_body}{eol}{eol}" if norm_body else f"[{name}]{eol}{eol}"
    pat = re.compile(rf"(?ms)^\[{re.escape(name)}\]\r?\n.*?(?=^\[|\Z)")
    if pat.search(text):
        return pat.sub(lambda _m: block, text, count=1)
    if text and not text.endswith("\n"):
        text += eol
    return text + block


def remove_section(text: str, name: str) -> str:
    """Delete the `[name]` section (header + body, through the blank line before the
    next `[section]` or EOF), preserving the rest. No-op if the section is absent.
    Used to undo a section a transient writer ADDED (e.g. PCSX2 multitap [PadN]/[Pad])
    so an on-exit restore returns the file to its pre-bind shape."""
    pat = re.compile(rf"(?ms)^\[{re.escape(name)}\]\r?\n.*?(?=^\[|\Z)")
    return pat.sub("", text, count=1)
