"""dolphin.* methods — Dolphin (GameCube/Wii) settings editor for the MAD
Standalones "GameCube / Wii → Settings" page.

Edits the Flatpak Dolphin config across TWO files — Dolphin.ini ([Core], [DSP],
[Display]) and GFX.ini ([Settings], [Hardware]). We do BYTE-PRESERVING in-place
edits of a single key's value (regex scoped to its [section]); everything else —
spacing, other keys/sections, LF endings — is left intact. Atomic write + a
one-time .bak per file. Refused while Dolphin is running (it rewrites its config
on exit). Stateless: each get/set re-reads disk.

SAFETY: we only ever EDIT keys that ALREADY EXIST in the file (in their expected
section). Dolphin writes these keys itself, so editing-in-place means we always
match THIS build's section + name + format and never create a mis-placed key —
important because MaxAnisotropy ([Hardware] vs newer [Enhancements]) and
AudioStretch (vs newer AudioPreservePitch) move/rename across Dolphin versions.

Value encodings verified against Dolphin source (see deck-docs/dolphin-ini-encodings.md):
  bool -> capitalized True/False (NOT 1/0)        [C++ chip sends "1"/"0"]
  enum write="index" -> the stored value IS the integer == the option index
                        (InternalResolution 0=Auto,1=Native,2=2x,... ;
                         AspectRatio 0=Auto,1=16:9,2=4:3,3=Stretch ;
                         MaxAnisotropy 0=1x,1=2x,2=4x,3=8x,4=16x)   write str(idx)
  enum write="option" -> the stored value IS the option STRING (GFXBackend "OGL"/
                        "Software Renderer"; DSP Backend "Pulse"/"No Audio Output") write options[idx]
MSAA is intentionally NOT exposed (u32 hex sample-count entangled with the SSAA
bool — needs a dedicated composite control, deferred).
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from .. import proc_guard
from .rpc import RpcError, method

_DIR = Path.home() / ".var/app/org.DolphinEmu.dolphin-emu/config/dolphin-emu"
_FILES = {"Dolphin.ini": _DIR / "Dolphin.ini", "GFX.ini": _DIR / "GFX.ini"}

_TRUE = {"1", "true", "yes", "on"}

# Curated, grouped, user-facing settings. Each item: key, label, file, section,
# type (C++ widget: bool/enum), and for enums `write` ("index" -> str(idx);
# "option" -> options[idx]) + `options` (display labels / stored tokens).
GROUPS = [
    {"title": "Graphics", "note": "", "items": [
        {"key": "InternalResolution", "label": "Internal resolution",
         "file": "GFX.ini", "section": "Settings", "type": "enum", "write": "index",
         "options": ["Auto", "Native (1x)", "2x", "3x", "4x", "5x", "6x", "7x", "8x"]},
        {"key": "AspectRatio", "label": "Aspect ratio",
         "file": "GFX.ini", "section": "Settings", "type": "enum", "write": "index",
         "options": ["Auto", "Force 16:9", "Force 4:3", "Stretch"]},
        {"key": "MaxAnisotropy", "label": "Anisotropic filtering",
         "file": "GFX.ini", "section": "Hardware", "type": "enum", "write": "index",
         "options": ["1x", "2x", "4x", "8x", "16x"]},
        {"key": "VSync", "label": "V-Sync",
         "file": "GFX.ini", "section": "Hardware", "type": "bool"},
        {"key": "GFXBackend", "label": "Graphics backend",
         "file": "Dolphin.ini", "section": "Core", "type": "enum", "write": "option",
         "options": ["Vulkan", "OGL", "Software Renderer"]},
    ]},
    {"title": "Display", "note": "", "items": [
        {"key": "Fullscreen", "label": "Fullscreen",
         "file": "Dolphin.ini", "section": "Display", "type": "bool"},
    ]},
    {"title": "Core", "note": "Dual core is faster but a few games need it off.", "items": [
        {"key": "CPUThread", "label": "Dual core (speed-up)",
         "file": "Dolphin.ini", "section": "Core", "type": "bool"},
    ]},
    {"title": "Audio", "note": "", "items": [
        {"key": "Backend", "label": "Audio backend",
         "file": "Dolphin.ini", "section": "DSP", "type": "enum", "write": "option",
         "options": ["Cubeb", "ALSA", "Pulse", "OpenAL", "No Audio Output"]},
        {"key": "AudioStretch", "label": "Audio stretching",
         "file": "Dolphin.ini", "section": "Core", "type": "bool"},
    ]},
]


def _item_by_key(key: str) -> dict | None:
    for g in GROUPS:
        for it in g["items"]:
            if it["key"] == key:
                return it
    return None


def _read(fname: str) -> str | None:
    p = _FILES[fname]
    if not p.is_file():
        return None
    with p.open("r", encoding="utf-8", errors="replace", newline="") as fh:
        return fh.read()


def _section_span(text: str, section: str) -> tuple[int, int] | None:
    sm = re.search(rf'(?ms)^\[{re.escape(section)}\][^\n]*\n', text)
    if not sm:
        return None
    start = sm.end()
    nm = re.search(r'(?m)^\[', text[start:])
    return start, (start + nm.start() if nm else len(text))


def _read_key(text: str | None, section: str, key: str) -> str | None:
    if text is None:                         # post-write re-read can race a vanished file
        return None
    span = _section_span(text, section)
    if not span:
        return None
    body = text[span[0]:span[1]]
    m = re.search(rf'(?m)^[ \t]*{re.escape(key)}[ \t]*=[ \t]*([^\n]*?)[ \t]*$', body)
    return m.group(1) if m else None


def _replace_key(text: str, section: str, key: str, value: str) -> str | None:
    """Byte-preserving: rewrite ONLY this key's value token within [section].
    Returns new text, or None if the section or key isn't present."""
    span = _section_span(text, section)
    if not span:
        return None
    body = text[span[0]:span[1]]
    kpat = re.compile(rf'(?m)^([ \t]*{re.escape(key)}[ \t]*=[ \t]*)[^\n]*$')
    if not kpat.search(body):
        return None
    new_body = kpat.sub(lambda m: m.group(1) + value, body, count=1)
    return text[:span[0]] + new_body + text[span[1]:]


def _ensure_bak(fname: str) -> None:
    p = _FILES[fname]
    bak = p.with_suffix(p.suffix + ".bak")
    if not bak.exists():
        shutil.copy2(p, bak)


def _atomic_write(fname: str, text: str) -> None:
    p = _FILES[fname]
    tmp = p.with_suffix(p.suffix + ".mad-tmp")
    with tmp.open("w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    tmp.replace(p)


def _enum_get(item: dict, raw: str) -> tuple[list[str], int]:
    """Return (options, value-index) for an enum, honestly representing a current
    value that falls outside the curated list (never clamp/lose it)."""
    if item["write"] == "option":
        options = list(item["options"])
        if raw not in options:
            options.insert(0, raw)          # preserve an unknown current token
        return options, options.index(raw)
    # write == "index": stored value is the integer (== option index)
    try:
        val = int(float(raw))
    except (TypeError, ValueError):
        return list(item["options"]), 0
    if item["key"] == "InternalResolution":
        hi = max(8, val if val > 0 else 0)
        options = ["Auto"] + [("Native (1x)" if n == 1 else f"{n}x")
                              for n in range(1, hi + 1)]
    else:
        options = list(item["options"])
        if val >= len(options):             # represent an out-of-range on-disk code
            options += [str(i) for i in range(len(options), val + 1)]
    return options, max(0, val)


@method("dolphin.get", slow=True)
def _dolphin_get(params):
    """Curated Dolphin settings, grouped, with current values. Only keys that
    actually exist in this build's config are offered (safe across versions)."""
    texts = {f: _read(f) for f in _FILES}
    exists = any(t is not None for t in texts.values())
    out_groups = []
    for g in GROUPS:
        settings = []
        for it in g["items"]:
            text = texts.get(it["file"])
            if text is None:
                continue
            raw = _read_key(text, it["section"], it["key"])
            if raw is None:
                continue                     # not in THIS build — don't offer it
            if it["type"] == "bool":
                settings.append({"key": it["key"], "label": it["label"], "type": "bool",
                                 "value": raw.strip().lower() in _TRUE})
            elif it["type"] == "enum":
                options, value = _enum_get(it, raw)
                settings.append({"key": it["key"], "label": it["label"], "type": "enum",
                                 "options": options, "value": value})
        if settings:
            out_groups.append({"title": g["title"], "note": g["note"], "settings": settings})
    return {"exists": exists, "path": str(_DIR),
            "running": proc_guard.emulator_running("dolphin"),
            "note": "Dolphin (GameCube/Wii). Changes save instantly; a one-time backup "
                    "is made before the first change.",
            "groups": out_groups}


@method("dolphin.set", slow=True)
def _dolphin_set(params):
    """Write one curated Dolphin setting (byte-preserving, atomic, .bak) and return
    the re-read value. Refused while Dolphin is running."""
    if proc_guard.emulator_running("dolphin"):
        raise RpcError("EBUSY", "Dolphin is running — close it first "
                                "(it rewrites its config on exit).")
    key = params["key"]
    raw_value = params["value"]
    item = _item_by_key(key)
    if item is None:
        raise RpcError("EINVAL", f"{key!r} is not an editable Dolphin setting")
    text = _read(item["file"])
    if text is None:
        raise RpcError("ENOENT", f"{item['file']} not found — launch a game once to create it.")
    # Confirm the key exists in this build (we never create keys — version safety).
    cur = _read_key(text, item["section"], item["key"])
    if cur is None:
        raise RpcError("ENOKEY", f"{key!r} not present in {item['file']} [{item['section']}]")

    if item["type"] == "bool":
        write = "True" if str(raw_value).strip().lower() in _TRUE else "False"
    else:  # enum
        try:
            idx = int(float(raw_value))
        except (TypeError, ValueError):
            raise RpcError("EINVAL", f"bad enum index {raw_value!r} for {key}")
        if item["write"] == "option":
            options, _ = _enum_get(item, cur)   # mirror get() so idx maps correctly
            if not (0 <= idx < len(options)):
                raise RpcError("EINVAL", f"enum index {idx} out of range for {key}")
            write = options[idx]
        else:  # index: stored integer == the option index
            if idx < 0:
                raise RpcError("EINVAL", f"negative index for {key}")
            write = str(idx)

    new_text = _replace_key(text, item["section"], item["key"], write)
    if new_text is None:
        raise RpcError("ENOKEY", f"{key!r} not present in {item['file']} [{item['section']}]")
    if new_text != text:
        _ensure_bak(item["file"])
        _atomic_write(item["file"], new_text)

    back = _read_key(_read(item["file"]), item["section"], item["key"])
    if item["type"] == "bool":
        return {"key": key, "value": (back or "").strip().lower() in _TRUE}
    options, value = _enum_get(item, back)
    return {"key": key, "value": value}
