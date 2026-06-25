"""cfgutil — shared, byte-preserving config-edit helpers for the MAD per-emulator
Settings backends (eden/cemu/rpcs3/pcsx2/model3; dolphin predates this and keeps
its own copies).

Every writer here changes ONLY the one value token of an existing key/element,
scoped to its section/parent, leaving all other bytes — spacing, comments, other
keys, line endings — intact. We never CREATE keys (so version drift in section/
key names can't make us write to the wrong place — a missing key is simply not
offered). One-time .bak + atomic temp+replace. The generic get/set engine maps
the C++ contract (bool -> "1"/"0"; enum -> option INDEX; int -> integer string)
onto each emulator's exact stored format via per-item metadata.

Item metadata (in a module's GROUPS):
  key, label, file, section            location (section = [INI section] / XML
                                       parent tag / YAML top-level key)
  type        "bool" | "enum" | "int"  (the C++ control type)
  bool_true, bool_false                bool literals for THIS emulator
                                       (default "true"/"false"; e.g. "1"/"0")
  write_mode  "index" | "option"       enum: write str(idx) vs options_stored[idx]
  options_display, options_stored      enum labels / exact stored tokens (same order)
  min, max, step                       int stepper bounds
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from .rpc import RpcError

_TRUE = {"1", "true", "yes", "on"}


# ── file ops ──────────────────────────────────────────────────────────────────
def read_text(p: Path) -> str | None:
    if not p.is_file():
        return None
    with p.open("r", encoding="utf-8", errors="replace", newline="") as fh:
        return fh.read()  # newline="" -> preserve LF/CRLF byte-for-byte


def ensure_bak(p: Path) -> None:
    bak = p.with_suffix(p.suffix + ".bak")
    # Defer if the launch/device-assign side already took a pristine .router-backup
    # — one pristine snapshot only, under either name (rule #5 recover-to-original;
    # mad_backup.restore_router_backups restores from whichever exists).
    if p.exists() and not bak.exists() and not p.with_name(p.name + ".router-backup").exists():
        shutil.copy2(p, bak)


def atomic_write(p: Path, text: str) -> None:
    tmp = p.with_suffix(p.suffix + ".mad-tmp")
    with tmp.open("w", encoding="utf-8", newline="") as fh:
        fh.write(text)  # verbatim — never add/strip a trailing newline
    tmp.replace(p)


# ── INI: [section] key = value  (tolerant of spaces in the header + around '=';
#    reads/writes the LAST occurrence within the section, so a duplicated key like
#    Supermodel's [Global] FullScreen resolves to the effective last-wins value) ──
def _ini_span(text: str, section: str) -> tuple[int, int] | None:
    sm = re.search(rf'(?m)^\[[ \t]*{re.escape(section)}[ \t]*\][^\n]*\n', text)
    if not sm:
        return None
    start = sm.end()
    nm = re.search(r'(?m)^\[', text[start:])
    return start, (start + nm.start() if nm else len(text))


def ini_read(text: str, section: str, key: str) -> str | None:
    span = _ini_span(text, section)
    if not span:
        return None
    body = text[span[0]:span[1]]
    ms = list(re.finditer(rf'(?m)^[ \t]*{re.escape(key)}[ \t]*=[ \t]*([^\n]*?)[ \t]*$', body))
    return ms[-1].group(1) if ms else None


def ini_replace(text: str, section: str, key: str, value: str) -> str | None:
    span = _ini_span(text, section)
    if not span:
        return None
    body = text[span[0]:span[1]]
    ms = list(re.finditer(rf'(?m)^([ \t]*{re.escape(key)}[ \t]*=[ \t]*)([^\n]*)$', body))
    if not ms:
        return None
    m = ms[-1]
    new_body = body[:m.start()] + m.group(1) + value + body[m.end():]
    return text[:span[0]] + new_body + text[span[1]:]


def ini_insert_after(text: str, section: str, anchor_key: str, new_line: str) -> str | None:
    """Insert ``new_line`` immediately after the section's ``anchor_key = …`` line.
    For configs (e.g. Eden per-game) where a key needs a sibling line CREATED next
    to an existing one. Returns None if the section or the anchor line is absent."""
    span = _ini_span(text, section)
    if not span:
        return None
    body = text[span[0]:span[1]]
    m = re.search(rf'(?m)^[ \t]*{re.escape(anchor_key)}[ \t]*=[^\n]*$', body)
    if not m:
        return None
    at = span[0] + m.end()
    return text[:at] + "\n" + new_line + text[at:]


def ini_set_or_insert(text: str, section: str, key: str, value: str) -> str | None:
    """Replace ``key``'s value in ``section`` if present, else INSERT ``key = value``
    at the end of the section body (creating the key). Returns None only if the
    section header itself is absent. For configs whose section may not yet hold the
    key — e.g. an empty ``[USB1]`` before its device Type / bindings are written."""
    cur = ini_replace(text, section, key, value)
    if cur is not None:
        return cur
    span = _ini_span(text, section)
    if not span:
        return None
    at = span[1]                                  # just before the next [section] / EOF
    line = f"{key} = {value}\n"
    head = text[:at]
    if head and not head.endswith("\n"):
        line = "\n" + line
    return head + line + text[at:]


# ── XML: <parent> … <tag>value</tag> … </parent>  (parent isolates non-unique
#    tags like Cemu's <api> which appears in both <Graphic> and <Audio>) ─────────
def _xml_block(text: str, parent: str) -> tuple[int, int] | None:
    m = re.search(rf'(?s)<{re.escape(parent)}>.*?</{re.escape(parent)}>', text)
    return (m.start(), m.end()) if m else None


def xml_read(text: str, parent: str, tag: str) -> str | None:
    span = _xml_block(text, parent)
    if not span:
        return None
    block = text[span[0]:span[1]]
    m = re.search(rf'(?s)<{re.escape(tag)}>(.*?)</{re.escape(tag)}>', block)
    return m.group(1) if m else None


def xml_replace(text: str, parent: str, tag: str, value: str) -> str | None:
    span = _xml_block(text, parent)
    if not span:
        return None
    block = text[span[0]:span[1]]
    pat = re.compile(rf'(?s)(<{re.escape(tag)}>)(.*?)(</{re.escape(tag)}>)')
    if not pat.search(block):
        return None
    new_block = pat.sub(lambda m: m.group(1) + value + m.group(3), block, count=1)
    return text[:span[0]] + new_block + text[span[1]:]


# ── YAML: top-level `Section:` block of 2-space-indented `Key: value` lines.
#    Scoped to the section so e.g. RPCS3's Video:/Renderer ≠ Audio:/Renderer.
#    Never adds a trailing newline (RPCS3 config.yml ends without one). ──────────
def _yaml_block(text: str, section: str) -> tuple[int, int] | None:
    sm = re.search(rf'(?m)^{re.escape(section)}:[ \t]*\n', text)
    if not sm:
        return None
    start = sm.end()
    nm = re.search(r'(?m)^[^\s#]', text[start:])  # next column-0 key ends the block
    return start, (start + nm.start() if nm else len(text))


def yaml_read(text: str, section: str, key: str) -> str | None:
    span = _yaml_block(text, section)
    if not span:
        return None
    block = text[span[0]:span[1]]
    m = re.search(rf'(?m)^[ \t]+{re.escape(key)}:[ \t]*(.*?)[ \t]*$', block)
    return m.group(1) if m else None


def yaml_replace(text: str, section: str, key: str, value: str) -> str | None:
    span = _yaml_block(text, section)
    if not span:
        return None
    block = text[span[0]:span[1]]
    pat = re.compile(rf'(?m)^([ \t]+{re.escape(key)}:[ \t]*)(.*)$')
    if not pat.search(block):
        return None
    new_block = pat.sub(lambda m: m.group(1) + value, block, count=1)
    return text[:span[0]] + new_block + text[span[1]:]


# ── generic enum/bool value engine (shared by every module) ───────────────────
def _enum_get(item: dict, raw: str) -> tuple[list[str], int]:
    disp = list(item.get("options_display") or item.get("options_stored") or [])
    if item.get("write_mode") == "option":
        stored = list(item.get("options_stored") or item.get("options_display") or [])
        if raw in stored:
            return disp, stored.index(raw)
        # current value isn't in the curated list — show it so nothing is lost
        return [raw] + disp, 0
    # write_mode "index": stored integer == option index
    try:
        idx = int(float(raw))
    except (TypeError, ValueError):
        return disp, 0
    if idx >= len(disp):                       # represent an out-of-range on-disk code
        disp = disp + [str(i) for i in range(len(disp), idx + 1)]
    return disp, max(0, idx)


def _enum_write(item: dict, idx: int, raw_cur: str) -> str | None:
    if item.get("write_mode") == "option":
        stored = list(item.get("options_stored") or item.get("options_display") or [])
        if raw_cur not in stored:              # mirror _enum_get's prepend so idx maps
            stored = [raw_cur] + stored
        return stored[idx] if 0 <= idx < len(stored) else None
    if idx < 0:
        return None
    return str(idx)


def bool_get(item: dict, raw: str) -> bool:
    return raw.strip().lower() in _TRUE


def bool_write(item: dict, value: str) -> str:
    on = str(value).strip().lower() in _TRUE
    return item.get("bool_true", "true") if on else item.get("bool_false", "false")


def get_groups(groups: list, file_texts: dict, read_fn, *, running: bool, note: str) -> dict:
    """Build the GROUPS payload. Offers a setting ONLY if its key exists in the
    file right now (version-safe), reading the current value via read_fn."""
    out = []
    for g in groups:
        settings = []
        for it in g["items"]:
            text = file_texts.get(it["file"])
            if text is None:
                continue
            raw = read_fn(text, it["section"], it.get("name", it["key"]))
            if raw is None:
                continue
            if it["type"] == "bool":
                settings.append({"key": it["key"], "label": it["label"],
                                 "type": "bool", "value": bool_get(it, raw)})
            elif it["type"] == "enum":
                disp, val = _enum_get(it, raw)
                settings.append({"key": it["key"], "label": it["label"],
                                 "type": "enum", "options": disp, "value": val})
            elif it["type"] == "int":
                try:
                    v = int(float(raw))
                except (TypeError, ValueError):
                    v = it.get("min", 0)
                row = {"key": it["key"], "label": it["label"], "type": "int", "value": v}
                for k in ("min", "max", "step"):
                    if k in it:
                        row[k] = it[k]
                settings.append(row)
        if settings:
            out.append({"title": g["title"], "note": g["note"], "settings": settings})
    return {"exists": any(t is not None for t in file_texts.values()),
            "running": running, "note": note, "groups": out}


def item_by_key(groups: list, key: str) -> dict | None:
    for g in groups:
        for it in g["items"]:
            if it["key"] == key:
                return it
    return None


def do_get(groups: list, path: Path, read_fn, *, proc: str, label: str) -> dict:
    """Whole get(): read the single config file + build the GROUPS payload."""
    from .. import proc_guard
    note = (f"{label}. Changes save instantly; a one-time backup is made before the "
            "first change.")
    return get_groups(groups, {path.name: read_text(path)}, read_fn,
                      running=proc_guard.emulator_running(proc), note=note)


def do_set(groups: list, params: dict, path: Path, read_fn, replace_fn, *,
           proc: str, label: str) -> dict:
    """Whole set(): running guard + key lookup + byte-preserving write."""
    from .. import proc_guard
    if proc_guard.emulator_running(proc):
        raise RpcError("EBUSY", f"{label} is running — close it first "
                                "(it rewrites its config on exit).")
    key = params["key"]
    item = item_by_key(groups, key)
    if item is None:
        raise RpcError("EINVAL", f"{key!r} is not an editable setting")
    return {"key": key, "value": apply_set(item, params["value"], path, read_fn, replace_fn)}


def apply_set(item: dict, value, path: Path, read_fn, replace_fn):
    """Single-file set: read, confirm the key exists (never create), byte-preserving
    replace, one-time .bak + atomic write, and return the re-read C++-shaped value."""
    name = item.get("name", item["key"])
    text = read_text(path)
    if text is None:
        raise RpcError("ENOENT", f"{path.name} not found — launch a game once to create it.")
    cur = read_fn(text, item["section"], name)
    if cur is None:
        raise RpcError("ENOKEY", f"{item['key']!r} not present in {path.name} [{item['section']}]")
    write = compute_write(item, value, cur)
    new_text = replace_fn(text, item["section"], name, write)
    if new_text is None:
        raise RpcError("ENOKEY", f"{item['key']!r} not present in {path.name} [{item['section']}]")
    if new_text != text:
        ensure_bak(path)
        atomic_write(path, new_text)
    back = read_fn(read_text(path), item["section"], name)
    if item["type"] == "bool":
        return bool_get(item, back or "")
    if item["type"] == "enum":
        _, v = _enum_get(item, back if back is not None else "")
        return v
    try:
        return int(float(back))
    except (TypeError, ValueError):
        return back


def compute_write(item: dict, value, raw_cur: str) -> str:
    """The exact string to store for one setting, given the C++-sent value and the
    current on-disk raw value (needed to mirror enum option lists)."""
    if item["type"] == "bool":
        return bool_write(item, value)
    if item["type"] == "enum":
        try:
            idx = int(float(value))
        except (TypeError, ValueError):
            raise RpcError("EINVAL", f"bad enum index {value!r} for {item['key']}")
        w = _enum_write(item, idx, raw_cur)
        if w is None:
            raise RpcError("EINVAL", f"enum index {idx} out of range for {item['key']}")
        return w
    if item["type"] == "int":
        try:
            n = int(float(value))
        except (TypeError, ValueError):
            raise RpcError("EINVAL", f"bad integer {value!r} for {item['key']}")
        if "min" in item and "max" in item:
            n = max(item["min"], min(item["max"], n))
        return str(n)
    raise RpcError("EINVAL", f"unsupported type {item['type']!r} for {item['key']}")
