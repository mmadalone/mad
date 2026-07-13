"""pcsx2_engine — the reusable buffered PCSX2 settings engine.

Extracted verbatim from pcsx2_settings so the three PCSX2-family editors (standard
PCSX2, the Namco 246/256 arcade fork, the retail GunCon2 fork) share ONE engine
instead of triplicating ~250 lines. The engine is pure with respect to WHICH ini it
edits: a BufferedEngine instance is parameterised by (ini path, running-check,
category map, shared buffer dict), so the same code drives every member.

Buffered model (mirrors lindbergh_cmds._buf): `<ns>.get` returns {buffered:true} and
the C++ shows SAVE/CANCEL; `<ns>.set` STAGES the edit into an in-memory copy of the
ini; `<ns>.save` writes it (one-time .bak + atomic) and bumps staterev; `<ns>.cancel`
reloads. All categories of one engine edit ONE file; pages are modal so a single
shared buffer is safe. Switching category (or a clean re-fetch) reloads fresh; a dirty
same-category re-fetch preserves staged edits. `edits` = the ordered (key, value)
pairs staged since the last reload; save REPLAYS them onto a FRESH read of the file
(not the possibly-stale whole-text buffer), so an external write to other keys between
load and save is never clobbered.

Item dict = cfgutil's schema (key, label, section, type bool/enum/int/float,
write_mode, options_display/_stored, bool_true/false, min/max/step) PLUS two composites:
  type "clamp"         clamp_keys=[k0,k1,k2] options_display=[...] — a 4-way enum stored
                       as a triple of bools (idx>=1, idx>=2, idx>=3), mirroring PCSX2's
                       setClampingMode.
  type "float_scaled"  scale=N — a float stored as value/scale, presented as an INT
                       stepper in the scaled units (PCSX2's own x10/x100 sliders).
"""
from __future__ import annotations

from pathlib import Path

from . import cfgutil
from .rpc import method, RpcError


def clamp_index(bits: list[bool]) -> int:
    """Number of leading True bits (F,F,F)->0 .. (T,T,T)->3; stops at the first
    False so an inconsistent on-disk triple degrades gracefully."""
    idx = 0
    for b in bits:
        if not b:
            break
        idx += 1
    return idx


def read_item(text: str, it: dict):
    if it["type"] == "clamp":
        sec = it["section"]
        raws = [cfgutil.ini_read(text, sec, k) for k in it["clamp_keys"]]
        if raws[0] is None:
            return None
        bits = [cfgutil.bool_get(it, r or "") for r in raws]
        return {"key": it["key"], "label": it["label"], "type": "enum",
                "options": list(it["options_display"]), "value": clamp_index(bits)}
    raw = cfgutil.ini_read(text, it["section"], it.get("name", it["key"]))
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
    if t == "float_scaled":
        try:
            v = float(raw)
        except (TypeError, ValueError):
            v = 0.0
        row = {"key": it["key"], "label": it["label"], "type": "int",
               "value": int(round(v * it["scale"]))}
        for k in ("min", "max", "step"):
            if k in it:
                row[k] = it[k]
        return row
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


def _shape(it: dict, token: str):
    t = it["type"]
    if t == "bool":
        return cfgutil.bool_get(it, token)
    if t in ("enum", "clamp"):
        return None  # handled by callers directly
    if t == "float":
        try:
            return float(token)
        except (TypeError, ValueError):
            return token
    try:
        return int(float(token))
    except (TypeError, ValueError):
        return token


def write_item(text: str, it: dict, value):
    """Stage one edit into `text`; return (new_text, cpp_shaped_value)."""
    if it["type"] == "clamp":
        try:
            idx = int(float(value))
        except (TypeError, ValueError):
            raise RpcError("EINVAL", f"bad clamp index {value!r} for {it['key']}")
        idx = max(0, min(len(it["options_display"]) - 1, idx))
        sec = it["section"]
        for i, k in enumerate(it["clamp_keys"]):
            tok = "true" if idx >= (i + 1) else "false"
            nt = cfgutil.ini_set_or_insert(text, sec, k, tok)
            if nt is None:
                raise RpcError("ENOKEY", f"{k!r} not present in [{sec}]")
            text = nt
        return text, idx
    if it["type"] == "float_scaled":
        try:
            n = int(round(float(value)))
        except (TypeError, ValueError):
            raise RpcError("EINVAL", f"bad scaled value {value!r} for {it['key']}")
        if "min" in it and "max" in it:
            n = max(it["min"], min(it["max"], n))
        tok = cfgutil.fmt_float(n / it["scale"])
        nt = cfgutil.ini_set_or_insert(text, it["section"], it["key"], tok)
        if nt is None:
            raise RpcError("ENOKEY", f"{it['key']!r} not present in [{it['section']}]")
        return nt, n
    name = it.get("name", it["key"])
    cur = cfgutil.ini_read(text, it["section"], name)
    if cur is None:
        raise RpcError("ENOKEY", f"{it['key']!r} not present in [{it['section']}]")
    write = cfgutil.compute_write(it, value, cur)
    nt = cfgutil.ini_set_or_insert(text, it["section"], name, write)
    if nt is None:
        raise RpcError("ENOKEY", f"{it['key']!r} not present in [{it['section']}]")
    if it["type"] == "enum":
        _, shaped = cfgutil._enum_get(it, write)
    else:
        shaped = _shape(it, write)
    return nt, shaped


class BufferedEngine:
    """A buffered single-ini settings editor over a category map.

    file       : Path to the ini this engine edits.
    running    : a 0-arg callable -> True when the emulator is live (writes refused).
    categories : {ns: (title, groups)} — the pages this engine serves.
    buf        : the shared buffer dict (module-level, so tests can monkeypatch it);
                 keys ns/text/disk/dirty/edits, exactly like the original _buf.
    note_label : the emulator name used in the staged-changes note (default "PCSX2").
    """

    def __init__(self, file: Path, running, categories: dict, buf: dict,
                 note_label: str = "PCSX2", read_item_fn=None, write_item_fn=None):
        self.file = file
        self.running = running
        self.categories = categories
        self.buf = buf
        self.note_label = note_label
        # Item codec: default = this module's INI read/write. A YAML flavor
        # (rpcs3_engine) passes its own so RPCS3's config.yml reuses this engine
        # unchanged (same buffered save/cancel, replay-on-fresh-read, staterev bump).
        self.read_item_fn = read_item_fn or read_item
        self.write_item_fn = write_item_fn or write_item

    # ── buffer ────────────────────────────────────────────────────────────────
    def reload(self) -> None:
        text = cfgutil.read_text(self.file)
        self.buf["text"] = text
        self.buf["disk"] = text
        self.buf["dirty"] = False
        self.buf["edits"] = []

    def item_by_key(self, ns: str, key: str):
        for g in self.categories[ns][1]:
            for it in g["items"]:
                if it["key"] == key:
                    return it
        return None

    # ── rpc verbs ─────────────────────────────────────────────────────────────
    def get(self, ns: str) -> dict:
        title, groups = self.categories[ns]
        # Reload fresh unless re-fetching the SAME category with staged (dirty) edits.
        if not (self.buf["ns"] == ns and self.buf["dirty"]):
            self.reload()
        self.buf["ns"] = ns
        text = self.buf["text"] or ""
        out = []
        for g in groups:
            settings = []
            for it in g["items"]:
                row = self.read_item_fn(text, it)
                if row is not None:
                    settings.append(row)
            if settings:
                out.append({"title": g["title"], "note": g.get("note", ""), "settings": settings})
        note = f"{self.note_label} {title} settings. Staged; press Save."
        return {"exists": self.buf["text"] is not None, "running": self.running(),
                "buffered": True, "dirty": self.buf["dirty"], "note": note, "groups": out}

    def set(self, ns: str, params: dict) -> dict:
        if self.running():
            raise RpcError("EBUSY", f"{self.note_label} is running — close it first "
                                    "(it rewrites its config on exit).")
        if self.buf["ns"] != ns or self.buf["text"] is None:
            self.reload()
            self.buf["ns"] = ns
        key = params["key"]
        it = self.item_by_key(ns, key)
        if it is None:
            raise RpcError("EINVAL", f"{key!r} is not an editable setting")
        new_text, shaped = self.write_item_fn(self.buf["text"], it, params["value"])
        self.buf["text"] = new_text
        self.buf["edits"].append((key, params["value"]))
        self.buf["dirty"] = (new_text != self.buf["disk"])
        return {"key": key, "value": shaped, "dirty": self.buf["dirty"]}

    def save(self, ns: str) -> dict:
        if self.running():
            raise RpcError("EBUSY", f"{self.note_label} is running — close it first "
                                    "(it rewrites its config on exit).")
        from .. import staterev
        if not self.buf["edits"]:
            self.buf["dirty"] = False
            return {"saved": False}
        # Re-read the file FRESH and replay only the staged edits onto it, so an
        # external write to OTHER keys since the buffer loaded is preserved.
        fresh = cfgutil.read_text(self.file)
        if fresh is None:
            raise RpcError("ENOENT", f"{self.file.name} not found — launch a game once to create it.")
        text = fresh
        for key, value in self.buf["edits"]:
            it = self.item_by_key(self.buf["ns"], key)
            if it is not None:
                text, _ = self.write_item_fn(text, it, value)
        saved = text != fresh
        if saved:
            cfgutil.ensure_bak(self.file)
            cfgutil.atomic_write(self.file, text)
            staterev.bump("config")
        self.buf["text"] = text
        self.buf["disk"] = text
        self.buf["edits"] = []
        self.buf["dirty"] = False
        return {"saved": saved}

    def cancel(self, ns: str) -> dict:
        self.reload()
        self.buf["ns"] = ns
        return {"cancelled": True}

    # ── rpc registration: <ns>.get/.set/.save/.cancel for each category ──────
    def register(self) -> None:
        for ns in self.categories:
            self._register_ns(ns)

    def _register_ns(self, ns: str) -> None:
        @method(f"{ns}.get", slow=True)
        def _g(params, ns=ns):
            return self.get(ns)

        @method(f"{ns}.set", slow=True)
        def _s(params, ns=ns):
            return self.set(ns, params)

        @method(f"{ns}.save", slow=True)
        def _sv(params, ns=ns):
            return self.save(ns)

        @method(f"{ns}.cancel", slow=True)
        def _c(params, ns=ns):
            return self.cancel(ns)


def new_buf() -> dict:
    """A fresh engine buffer dict."""
    return {"ns": None, "text": None, "disk": None, "dirty": False, "edits": []}
