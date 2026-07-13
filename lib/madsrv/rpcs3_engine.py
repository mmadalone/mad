"""rpcs3_engine — YAML item codec + buffered engine for RPCS3 (config.yml) settings.

Reuses ``pcsx2_engine.BufferedEngine`` (buffered X=Save / Y=Cancel, staterev bump on
save, replay-of-staged-edits onto a FRESH read so a foreign write to other keys is never
clobbered) but swaps the INI item codec for a YAML one: RPCS3 stores values as a
top-level ``Section:`` block of 2-space-indented ``Key: value`` lines, read/written
byte-preserving via ``cfgutil.yaml_read`` / ``cfgutil.yaml_replace``.

``yaml_replace`` is REPLACE-ONLY (never creates a key), which is exactly cfgutil's
"never create keys" discipline: a key absent from the live config.yml is simply not
offered, so version drift in a key name can't make us write to the wrong place. RPCS3
bools are lowercase ``true``/``false`` (cfgutil's defaults, so items need no
bool_true/false). The item schema is cfgutil's standard (key, label, section, type
bool/enum/int/float, write_mode, options_display/_stored, min/max/step); the PCSX2-only
composites (clamp, float_scaled) are not used here.
"""
from __future__ import annotations

from . import cfgutil, pcsx2_engine
from .rpc import RpcError


def read_item(text: str, it: dict):
    """Read one setting's current value from YAML text; None if the key is absent
    (so the engine drops it — version-safe). Shape mirrors pcsx2_engine.read_item."""
    raw = cfgutil.yaml_read(text, it["section"], it.get("name", it["key"]))
    if raw is None:
        return None
    t = it["type"]
    if t == "bool":
        return {"key": it["key"], "label": it["label"], "type": "bool",
                "value": cfgutil.bool_get(it, raw)}
    if t == "enum":
        disp, val = cfgutil._enum_get(it, raw)
        return {"key": it["key"], "label": it["label"], "type": "enum",
                "options": disp, "value": val}
    if t in ("int", "float"):
        try:
            v = float(raw) if t == "float" else int(float(raw))
        except (TypeError, ValueError):
            v = float(it.get("min", 0)) if t == "float" else int(it.get("min", 0))
        row = {"key": it["key"], "label": it["label"], "type": t, "value": v}
        for k in ("min", "max", "step"):
            if k in it:
                row[k] = it[k]
        return row
    return None


def write_item(text: str, it: dict, value):
    """Stage one edit into YAML ``text``; return (new_text, cpp_shaped_value).
    Refuses (ENOKEY) if the key is absent — never creates a key (cfgutil discipline)."""
    name = it.get("name", it["key"])
    cur = cfgutil.yaml_read(text, it["section"], name)
    if cur is None:
        raise RpcError("ENOKEY", f"{it['key']!r} not present in [{it['section']}]")
    write = cfgutil.compute_write(it, value, cur)
    nt = cfgutil.yaml_replace(text, it["section"], name, write)
    if nt is None:
        raise RpcError("ENOKEY", f"{it['key']!r} not present in [{it['section']}]")
    if it["type"] == "enum":
        _, shaped = cfgutil._enum_get(it, write)
    elif it["type"] == "bool":
        shaped = cfgutil.bool_get(it, write)
    elif it["type"] == "float":
        try:
            shaped = float(write)
        except (TypeError, ValueError):
            shaped = write
    else:
        try:
            shaped = int(float(write))
        except (TypeError, ValueError):
            shaped = write
    return nt, shaped


def engine(file, running, categories: dict, buf: dict) -> pcsx2_engine.BufferedEngine:
    """A BufferedEngine bound to a YAML file with the RPCS3 codec."""
    return pcsx2_engine.BufferedEngine(file, running, categories, buf,
                                       note_label="RPCS3",
                                       read_item_fn=read_item, write_item_fn=write_item)


new_buf = pcsx2_engine.new_buf
