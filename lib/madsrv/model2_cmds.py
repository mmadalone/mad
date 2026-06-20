"""model2.* methods — ElSemi's Sega Model 2 Emulator (m2emu) EMULATOR.INI editor.

The MAD "Model 2" page reads/writes the single shared INI at
~/Emulation/roms/model2/EMULATOR.INI (a Windows-style, CRLF, inline-";"-commented
file with a Wine Z:\\ backslash path). We edit it the safe way:

  * NEVER configparser — it would mangle the inline ; comments, escape the
    backslashes in the RomDirs path, and re-quote values.
  * Targeted regex substitution on the whole file text: a set rewrites ONLY the
    one key's value token; the separator, the inline comment, trailing spaces,
    section headers, ordering and the line endings are all preserved byte-for-byte.
  * Atomic write (temp + os.replace) + a one-time .bak (rule: never clobber user
    data). Stateless — each set re-reads disk first, so it never fights the
    launcher's per-game `DrawCross` sed (model-2-emulator.sh).

Only the curated user-facing keys in GROUPS are exposed/writable; debug,
menu-managed and launcher-managed keys (Wireframe, FullMode, Filter, DrawCross,
RomDirs, …) are never read or written here.

Source of truth (docs-first): ElSemi's README.TXT in the model2 dir + the INI's
own inline comments. See deck-docs/model2-emulator-ini.md.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from .. import mad_paths, proc_guard
from .rpc import RpcError, method

MODEL2_INI = mad_paths.roms_root() / "model2" / "EMULATOR.INI"

# Preset fullscreen resolutions offered by the "Resolution" control (1280x800 =
# Steam Deck native). The current value is prepended if it isn't one of these.
RESOLUTION_PRESETS = ["640x480", "800x600", "1024x768",
                      "1280x720", "1280x800", "1920x1080"]

# Curated, grouped, user-facing settings. type drives the C++ widget:
#   bool  -> chip toggle      enum  -> stepper over `options` (value = index)
#   int   -> stepper [min,max,step]   float -> stepper [min,max,step] (1 decimal)
#   resolution -> stepper over WxH presets (synthetic; writes FullScreenWidth/Height)
GROUPS = [
    {"title": "Display", "note": "", "items": [
        {"key": "Resolution", "label": "Fullscreen resolution", "type": "resolution"},
        {"key": "WideScreenWindow", "label": "Windowed aspect ratio", "type": "enum",
         "options": ["4:3", "16:9", "16:10"]},
        {"key": "FSAA", "label": "Anti-aliasing (FSAA)", "type": "bool"},
    ]},
    {"title": "Graphics quality",
     "note": "Trilinear can break Dead or Alive; Mesh transparency needs a Pixel Shader 3.0 GPU.",
     "items": [
        {"key": "Bilinear", "label": "Bilinear filtering", "type": "bool"},
        {"key": "Trilinear", "label": "Trilinear filtering", "type": "bool"},
        {"key": "FilterTilemaps", "label": "Filter 2D tilemaps", "type": "bool"},
        {"key": "MeshTransparency", "label": "Mesh transparency", "type": "bool"},
        {"key": "AutoMip", "label": "Auto-mipmaps", "type": "bool"},
        {"key": "FakeGouraud", "label": "Fake Gouraud shading", "type": "bool"},
    ]},
    {"title": "Color (gamma)", "note": "1.0 = no correction.", "items": [
        {"key": "GammaR", "label": "Red gamma", "type": "float", "min": 0.5, "max": 2.5, "step": 0.1},
        {"key": "GammaG", "label": "Green gamma", "type": "float", "min": 0.5, "max": 2.5, "step": 0.1},
        {"key": "GammaB", "label": "Blue gamma", "type": "float", "min": 0.5, "max": 2.5, "step": 0.1},
    ]},
    {"title": "Input",
     "note": "P1/P2 device numbers only matter with Raw mouse input on (2-gun games).",
     "items": [
        {"key": "XInput", "label": "XInput (Xbox-style pads)", "type": "bool"},
        {"key": "UseRawInput", "label": "Raw mouse input (2 mice / guns)", "type": "bool"},
        {"key": "HoldGears", "label": "Return to neutral when gear released", "type": "bool"},
        {"key": "RawDevP1", "label": "P1 mouse device #", "type": "int", "min": 0, "max": 9, "step": 1},
        {"key": "RawDevP2", "label": "P2 mouse device #", "type": "int", "min": 0, "max": 9, "step": 1},
    ]},
    {"title": "Troubleshooting",
     "note": "Enable only if Model 2 crashes or shows a blank screen.",
     "items": [
        {"key": "ForceManaged", "label": "Force managed textures", "type": "bool"},
    ]},
]

# Real INI keys this module is allowed to write (the synthetic "Resolution"
# expands to these two). Anything outside this set is refused.
EDITABLE_KEYS = {it["key"] for g in GROUPS for it in g["items"] if it["type"] != "resolution"}
EDITABLE_KEYS |= {"FullScreenWidth", "FullScreenHeight"}

_TRUE = {"1", "true", "yes", "on"}


def _key_re(key: str) -> re.Pattern:
    # ^[ws]Key[ws]=[ws] (value) (trailing spaces + optional ;comment + optional \r) $
    return re.compile(
        r"(?m)^([ \t]*" + re.escape(key) + r"[ \t]*=[ \t]*)"
        r"([^;\r\n]*?)"
        r"([ \t]*(?:;[^\r\n]*)?\r?)$")


def _read_key(text: str, key: str) -> str | None:
    m = _key_re(key).search(text)
    return m.group(2).strip() if m else None


def _set_key(text: str, key: str, value: str) -> tuple[str, bool]:
    """Rewrite key's value in-place. Returns (new_text, found). Only the value
    token changes; comment/spacing/line-ending preserved."""
    pat = _key_re(key)
    if pat.search(text) is None:
        return text, False
    new_text = pat.sub(lambda m: m.group(1) + value + m.group(3), text, count=1)
    return new_text, True


def _coerce_from(item_type: str, raw: str):
    if item_type == "bool":
        return raw.strip().lower() in _TRUE
    if item_type in ("int", "enum"):
        return int(float(raw))  # tolerate "1.0" written as an int field
    if item_type == "float":
        return float(raw)
    return raw


def _coerce_to(item_type: str, value) -> str:
    if item_type == "bool":
        truthy = value if isinstance(value, bool) else str(value).strip().lower() in _TRUE | {"1.0"}
        return "1" if truthy else "0"
    if item_type in ("int", "enum"):
        return str(int(float(value)))
    if item_type == "float":
        return f"{float(value):.1f}"
    return str(value)


def _item_by_key(key: str) -> dict | None:
    for g in GROUPS:
        for it in g["items"]:
            if it["key"] == key:
                return it
    return None


def _read_text() -> str:
    # newline="" disables universal-newline translation so the file's CRLF
    # endings survive a read-modify-write round-trip byte-for-byte.
    with MODEL2_INI.open("r", encoding="utf-8", errors="replace", newline="") as fh:
        return fh.read()


def _ensure_bak() -> None:
    """One-time recovery copy before the first edit (rule #5: never clobber)."""
    bak = MODEL2_INI.with_suffix(MODEL2_INI.suffix + ".bak")
    if not bak.exists():
        shutil.copy2(MODEL2_INI, bak)


def _atomic_write(text: str) -> None:
    tmp = MODEL2_INI.with_suffix(MODEL2_INI.suffix + ".model2-tmp")
    with tmp.open("w", encoding="utf-8", newline="") as fh:  # newline="" = write as-is
        fh.write(text)
    tmp.replace(MODEL2_INI)


@method("model2.get")
def _model2_get(params):
    """Load EMULATOR.INI and return the curated settings, grouped, with current
    values. exists:false (no error) if the INI hasn't been created yet."""
    if not MODEL2_INI.is_file():
        return {"exists": False, "path": str(MODEL2_INI), "groups": []}
    text = _read_text()

    out_groups = []
    for g in GROUPS:
        settings = []
        for it in g["items"]:
            if it["type"] == "resolution":
                w, h = _read_key(text, "FullScreenWidth"), _read_key(text, "FullScreenHeight")
                if not w or not h:
                    continue
                cur = f"{int(float(w))}x{int(float(h))}"
                options = list(RESOLUTION_PRESETS)
                if cur not in options:
                    options.insert(0, cur)
                settings.append({"key": "Resolution", "label": it["label"],
                                 "type": "resolution", "value": cur, "options": options})
                continue
            raw = _read_key(text, it["key"])
            if raw is None:
                continue  # key absent from this INI build — don't offer it
            row = {"key": it["key"], "label": it["label"], "type": it["type"],
                   "value": _coerce_from(it["type"], raw)}
            for extra in ("options", "min", "max", "step"):
                if extra in it:
                    row[extra] = it[extra]
            settings.append(row)
        if settings:
            out_groups.append({"title": g["title"], "note": g["note"], "settings": settings})
    return {"exists": True, "path": str(MODEL2_INI), "groups": out_groups}


@method("model2.set")
def _model2_set(params):
    """Write one curated setting to EMULATOR.INI (atomic, comment-preserving) and
    return the re-read effective value. The synthetic "Resolution" key expands to
    FullScreenWidth + FullScreenHeight in a single write."""
    if proc_guard.emulator_running("model2"):
        raise RpcError("EBUSY", "Model 2 Emulator is running — close it first "
                                "(it rewrites EMULATOR.INI on exit, which would revert this change).")
    key = params["key"]
    value = params["value"]
    if not MODEL2_INI.is_file():
        raise RpcError("ENOENT", f"{MODEL2_INI} not found — launch a Model 2 game once to create it.")
    text = _read_text()

    if key == "Resolution":
        m = re.fullmatch(r"\s*(\d{2,5})\s*[xX]\s*(\d{2,5})\s*", str(value))
        if not m:
            raise RpcError("EINVAL", f"bad resolution {value!r} (expected WxH)")
        w, h = m.group(1), m.group(2)
        text, okw = _set_key(text, "FullScreenWidth", w)
        text, okh = _set_key(text, "FullScreenHeight", h)
        if not (okw and okh):
            raise RpcError("ENOKEY", "FullScreenWidth/Height not present in EMULATOR.INI")
        _ensure_bak()
        _atomic_write(text)
        nw = _read_key(text, "FullScreenWidth")
        nh = _read_key(text, "FullScreenHeight")
        return {"key": "Resolution", "value": f"{int(float(nw))}x{int(float(nh))}"}

    if key not in EDITABLE_KEYS:
        raise RpcError("EINVAL", f"{key!r} is not an editable Model 2 setting")
    item = _item_by_key(key)
    str_value = _coerce_to(item["type"] if item else "string", value)
    text, ok = _set_key(text, key, str_value)
    if not ok:
        raise RpcError("ENOKEY", f"{key!r} not present in EMULATOR.INI")
    _ensure_bak()
    _atomic_write(text)
    raw = _read_key(text, key)
    return {"key": key, "value": _coerce_from(item["type"], raw) if item else raw}
