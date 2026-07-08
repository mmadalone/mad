r"""cemu_packs_* - Cemu (Wii U) PER-GAME graphic packs, mirroring Cemu's own Graphic Packs window:
category sub-pages, and within each, one GROUP per pack = an Enabled toggle + a dropdown for each of
the pack's option groups (its rules.txt [Preset] categories). Six category namespaces cemu_packs_<cat>
(Enhancements/Graphics/Mods/Workarounds/Cheats/Other), each a BUFFERED GuiMadPageEmuSettings page.

Contract (buffered; X=Save / Y=Cancel + the panel unsaved-changes guard):
  <ns>.get     -> {exists:true, running, buffered:true, dirty, note,
                   groups:[{title:<pack name>, settings:[{Enabled bool}, {<option> enum}...]}]}
  <ns>.set     -> STAGE one enable toggle OR one option pick
  <ns>.save    -> apply the staged model to settings.xml <GraphicPack> (byte-preserving that span,
                  one-time .bak + atomic, bump config)
  <ns>.cancel  -> discard

Option dropdowns show the pack's OWN preset names (Cemu parity), with the pack's real default
pre-selected (the rules.txt [Preset] `default`, else the first) -- NO synthetic "Pack default" entry.
Picking the default clears that option's override (Cemu writes nothing for a default). Toggling a pack
off keeps its stored option choices (disabled="true"), exactly like Cemu. Universal (titleIds=*) packs
are a GLOBAL choice, not per-game, so they are excluded here.

Buffer: a module-level WORKING copy of the <GraphicPack> entries model (the whole block) + the pristine
disk parse; ctx=(titleid, path-category) drives reloads. Pages are modal and the panel forces
Save/Discard before leaving a dirty page, so one shared buffer is safe; save re-reads fresh disk and
writes the working model onto it (all other games' entries are in the model, so nothing is lost).

rules.txt / <GraphicPack> formats source-verified: GraphicPack2.cpp + CemuConfig.cpp (cemu-project/
Cemu, 2026-07-08); real on-device settings.xml.
"""
from __future__ import annotations

import copy
import html
import re
from pathlib import Path

from .. import proc_guard, staterev
from . import cemu_games, cfgutil
from .rpc import RpcError, method

_SETTINGS = Path.home() / ".config/Cemu/settings.xml"   # module global: tests redirect it
_PROC = "cemu"
_SEP = "\x1f"                                            # option-row key delimiter: <filename>\x1f<optgroup>
_TID_RE = re.compile(r"^[0-9A-Fa-f]{16}$")

# Fixed category sub-pages (Cemu's own pack tree). "Other" catches any non-standard 2nd path segment so
# a pack is never invisible; it is hidden per-game when empty (via the browser hide list).
CATEGORIES = ["Enhancements", "Graphics", "Mods", "Workarounds", "Cheats", "Other"]
_CANON = {c.lower(): c for c in CATEGORIES}


def catkey(category: str) -> str:
    return category.lower()


# ── rules.txt parsing ──────────────────────────────────────────────────────────
def _parse_rules_text(text: str) -> tuple[dict, list]:
    definition: dict = {}
    presets: list = []
    cur = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line[:2] in ("//",) or line[:1] in ("#", ";"):
            continue
        if line.startswith("[") and line.endswith("]"):
            hdr = line[1:-1].strip().lower()
            if hdr == "definition":
                cur = definition
            elif hdr == "preset":
                cur = {}
                presets.append(cur)
            else:
                cur = None
            continue
        if cur is None or "=" not in line:
            continue
        k, _, v = line.partition("=")
        cur[k.strip().lower()] = v.strip()
    return definition, presets


def _parse_rules(path: Path) -> dict | None:
    try:
        definition, presets = _parse_rules_text(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return None
    name = definition.get("name", "").strip()
    tids_raw = definition.get("titleids", "").strip()
    if not name or not tids_raw:
        return None
    universal = tids_raw == "*"
    titleids: set[str] = set()
    if not universal:
        for tok in tids_raw.split(","):
            tok = tok.strip().lower()
            if re.fullmatch(r"[0-9a-f]{16}", tok):
                titleids.add(tok)
        if not titleids:
            return None
    path_raw = definition.get("path", "").strip().strip('"').strip("'")
    segs = [s for s in path_raw.split("/") if s.strip()]
    category = segs[1] if len(segs) >= 2 else (segs[0] if segs else "")
    # option groups: preset name lists per [Preset] `category`, plus the default preset per group
    opts: dict[str, list] = {}
    defaults: dict[str, str] = {}
    for p in presets:
        pname = p.get("name", "").strip()
        if not pname:
            continue
        pcat = p.get("category", "")
        opts.setdefault(pcat, [])
        if pname not in opts[pcat]:
            opts[pcat].append(pname)
        dflag = p.get("default")
        if dflag is not None and dflag.strip().lower() not in ("0", "false", ""):
            defaults.setdefault(pcat, pname)
    for pcat, names in opts.items():
        defaults.setdefault(pcat, names[0] if names else "")   # unmarked group -> first preset
    return {"name": name, "universal": universal, "titleids": titleids, "category": category,
            "options": opts, "defaults": defaults}


def _cat_of(pk: dict) -> str:
    return _CANON.get((pk.get("category") or "").strip().lower(), "Other")


def _rel_filename(rules: Path) -> str:
    return rules.relative_to(cemu_games.graphicpacks_dir().parent).as_posix()


def _scan_packs() -> list:
    root = cemu_games.graphicpacks_dir()
    out = []
    if not root.is_dir():
        return out
    for rules in root.rglob("rules.txt"):
        pk = _parse_rules(rules)
        if pk is None:
            continue
        pk["filename"] = _rel_filename(rules)
        out.append(pk)
    return out


def _pack_label(pk: dict) -> str:
    return pk["name"]


def enabled_titleids() -> set:
    """Title ids (lowercase 16-hex) with at least one ENABLED, GAME-SPECIFIC graphic pack - the
    picker's 'custom' badge. Universal excluded."""
    try:
        text = cfgutil.read_text(_SETTINGS) or ""
    except OSError:
        return set()
    data_parent = cemu_games.graphicpacks_dir().parent
    out: set = set()
    for e in _parse_graphicpack(text):
        if e["disabled"]:
            continue
        pk = _parse_rules(data_parent / _norm(e["filename"]))
        if pk and not pk["universal"]:
            out |= pk["titleids"]
    return out


def applicable_categories() -> dict:
    """{titleid(lower): {path-category, ...}} for game-specific packs (universal excluded), for the
    per-game browser hide list."""
    out: dict = {}
    for pk in _scan_packs():
        if pk["universal"]:
            continue
        cat = _cat_of(pk)
        for tid in pk["titleids"]:
            out.setdefault(tid, set()).add(cat)
    return out


# ── <GraphicPack> block in settings.xml: parse / mutate / serialise ────────────
_BLOCK_RE = re.compile(r'(?s)([ \t]*)<GraphicPack>(.*?)</GraphicPack>')
_ENTRY_RE = re.compile(r'(?s)<Entry\b([^>]*?)(?:/>|>(.*?)</Entry>)')
_PRESET_RE = re.compile(r'(?s)<Preset>(.*?)</Preset>')


def _attr(attrs: str, name: str) -> str | None:
    m = re.search(rf'{name}\s*=\s*"([^"]*)"', attrs)
    return html.unescape(m.group(1)) if m else None


def _tag_text(body: str, tag: str) -> str | None:
    m = re.search(rf'(?s)<{tag}>(.*?)</{tag}>', body)
    return html.unescape(m.group(1)) if m else None


def _norm(filename: str) -> str:
    fn = filename.replace("\\", "/")
    i = fn.find("graphicPacks/")
    return fn[i:] if i != -1 else fn


def _parse_graphicpack(text: str) -> list:
    """[{filename, disabled, presets:[(optgroup, preset), ...]}] in file order, or [] if no block."""
    m = _BLOCK_RE.search(text)
    if not m:
        return []
    entries = []
    for em in _ENTRY_RE.finditer(m.group(2)):
        fn = _attr(em.group(1), "filename")
        if fn is None:
            continue
        disabled = (_attr(em.group(1), "disabled") or "").lower() in ("true", "1")
        presets = []
        for pm in _PRESET_RE.finditer(em.group(2) or ""):
            pbody = pm.group(1)
            pre = _tag_text(pbody, "preset")
            if pre is not None:
                presets.append((_tag_text(pbody, "category") or "", pre))
        entries.append({"filename": fn, "disabled": disabled, "presets": presets})
    return entries


def _esc_text(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _esc_attr(s: str) -> str:
    return _esc_text(s).replace('"', "&quot;")


def _serialize(entries: list, base: str, nl: str) -> str:
    i2, i3, i4 = base + "    ", base + "        ", base + "            "
    out = [f"{base}<GraphicPack>"]
    for e in entries:
        dis = ' disabled="true"' if e["disabled"] else ""
        head = f'{i2}<Entry filename="{_esc_attr(e["filename"])}"{dis}'
        if not e["presets"]:
            out.append(head + "/>")
            continue
        out.append(head + ">")
        for cat, pre in e["presets"]:
            out.append(f"{i3}<Preset>")
            if cat:
                out.append(f"{i4}<category>{_esc_text(cat)}</category>")
            out.append(f"{i4}<preset>{_esc_text(pre)}</preset>")
            out.append(f"{i3}</Preset>")
        out.append(f"{i2}</Entry>")
    out.append(f"{base}</GraphicPack>")
    return nl.join(out)


def _write_block(text: str, entries: list) -> str:
    nl = "\r\n" if "\r\n" in text else "\n"
    m = _BLOCK_RE.search(text)
    if m:
        base = m.group(1)
        return text[:m.start()] + _serialize(entries, base, nl) + text[m.end():]
    block = _serialize(entries, "    ", nl)
    cm = re.search(r'(?s)([ \t]*)</content>', text)
    if cm:
        return text[:cm.start()] + block + nl + cm.group(1) + "</content>" + text[cm.end():]
    return text + (nl if text and not text.endswith("\n") else "") + block + nl


def _find(entries: list, filename: str) -> dict | None:
    tail = _norm(filename)
    for e in entries:
        if _norm(e["filename"]) == tail:
            return e
    return None


def _entry_preset(entry: dict, optgroup: str) -> str | None:
    for cat, pre in entry["presets"]:
        if cat == optgroup:
            return pre
    return None


# ── model mutation (the WORKING entries model) ─────────────────────────────────
def _set_enabled(entries: list, filename: str, on: bool) -> None:
    e = _find(entries, filename)
    if on:
        if e is None:
            entries.append({"filename": filename, "disabled": False, "presets": []})
        else:
            e["disabled"] = False
    elif e is not None:
        if e["presets"]:
            e["disabled"] = True                     # keep the stored option choices (Cemu parity)
        else:
            entries.remove(e)                        # nothing to remember -> drop the entry


def _set_option(entries: list, filename: str, optgroup: str, value: str, default: str) -> None:
    e = _find(entries, filename)
    is_default = (value == default)
    if e is None:
        if is_default:
            return                                   # default + no entry = nothing to store
        e = {"filename": filename, "disabled": True, "presets": []}   # remember the pick; pack stays off
        entries.append(e)
    e["presets"] = [(c, p) for (c, p) in e["presets"] if c != optgroup]
    if not is_default:
        e["presets"].append((optgroup, value))
    if e["disabled"] and not e["presets"]:
        entries.remove(e)                            # a disabled entry with no overrides is meaningless


# ── buffered category pages ────────────────────────────────────────────────────
_BUF: dict = {"ctx": None, "disk": None, "entries": None}
_NOTE = ("Enable graphic packs for this game and pick their options - the same choices as Cemu's own "
         "Graphic Packs window. Changes are staged: press X to Save, Y to Cancel.")


def _running() -> bool:
    return proc_guard.emulator_running(_PROC)


def _tid(params) -> str:
    t = (params.get("titleid") or "").strip()
    if not _TID_RE.match(t):
        raise RpcError("EINVAL", f"bad game id {t!r}")
    return t


def _reload() -> None:
    _BUF["disk"] = _parse_graphicpack(cfgutil.read_text(_SETTINGS) or "")
    _BUF["entries"] = copy.deepcopy(_BUF["disk"])


def _state(entries: list) -> dict:
    return {_norm(e["filename"]): (e["disabled"], frozenset(e["presets"])) for e in entries}


def _dirty() -> bool:
    if _BUF["entries"] is None:
        return False
    return _state(_BUF["entries"]) != _state(_BUF["disk"])


def _packs_for(titleid: str, category: str) -> list:
    tid = titleid.lower()
    out = [pk for pk in _scan_packs()
           if (not pk["universal"]) and tid in pk["titleids"] and _cat_of(pk) == category]
    out.sort(key=lambda pk: _pack_label(pk).lower())
    return out


def _optgroups(pk: dict) -> list:
    # option-group order: named groups sorted, the unnamed ("") group last.
    named = sorted((c for c in pk["options"] if c), key=str.lower)
    return named + ([""] if "" in pk["options"] else [])


_DEFAULT_TAIL = re.compile(r"\s*\(\s*default\s*\)\s*$", re.I)   # "Disabled (Default)" -> "Disabled"
_DEFAULT_IN = re.compile(r",\s*default\s*\)\s*$", re.I)         # "Golden (production, Default)" -> "...)"


def _strip_default_tag(name: str) -> str:
    """Drop the redundant '(Default)' annotation pack authors bake into preset names - the picker
    already pre-selects the default, so it is just clutter. A standalone 'Default' option name (no
    parentheses) is left untouched. Display-only: the stored/matched value stays the real name."""
    s = _DEFAULT_TAIL.sub("", name)
    if s == name:
        s = _DEFAULT_IN.sub(")", name)
    return s.strip() or name


def _do_get(category: str, params: dict) -> dict:
    tid = _tid(params)
    ctx = (tid, category)
    if not (_BUF["ctx"] == ctx and _dirty()):
        _reload()
    _BUF["ctx"] = ctx
    entries = _BUF["entries"]
    groups = []
    for pk in _packs_for(tid, category):
        e = _find(entries, pk["filename"])
        rows = [{"key": pk["filename"], "label": "Enabled", "type": "bool",
                 "value": bool(e) and not e["disabled"]}]
        for og in _optgroups(pk):
            names = pk["options"][og]
            default = pk["defaults"].get(og) or (names[0] if names else "")
            stored = _entry_preset(e, og) if e else None
            sel = stored if stored in names else default
            rows.append({"key": pk["filename"] + _SEP + og, "label": og or "Preset",
                         "type": "enum", "options": [_strip_default_tag(n) for n in names],
                         "value": names.index(sel) if sel in names else 0})
        groups.append({"title": _pack_label(pk), "note": "", "settings": rows})
    note = _NOTE if groups else "This game has no graphic packs in this category."
    return {"exists": True, "running": _running(), "buffered": True, "dirty": _dirty(),
            "note": note, "groups": groups}


def _do_set(category: str, params: dict) -> dict:
    if _running():
        raise RpcError("EBUSY", "close Cemu first - it rewrites settings.xml on exit.")
    tid = _tid(params)
    ctx = (tid, category)
    if _BUF["ctx"] != ctx or _BUF["entries"] is None:
        _reload()
        _BUF["ctx"] = ctx
    key = params.get("key", "")
    filename, sep, optgroup = key.partition(_SEP)
    if not filename:
        raise RpcError("EINVAL", "empty pack key")
    entries = _BUF["entries"]
    if sep:                                           # an option dropdown
        pk = next((p for p in _scan_packs() if p["filename"] == filename), None)
        if pk is None:
            raise RpcError("EINVAL", "graphic pack no longer exists")
        names = pk["options"].get(optgroup, [])
        try:
            idx = int(float(params.get("value")))
        except (TypeError, ValueError):
            raise RpcError("EINVAL", "bad option index")
        if not (0 <= idx < len(names)):
            raise RpcError("EINVAL", "option index out of range")
        default = pk["defaults"].get(optgroup) or (names[0] if names else "")
        _set_option(entries, filename, optgroup, names[idx], default)
        ret = idx
    else:                                             # the Enabled toggle
        on = str(params.get("value")).strip().lower() in ("1", "true", "yes", "on")
        _set_enabled(entries, filename, on)
        ret = on
    return {"key": key, "value": ret, "dirty": _dirty()}


def _do_save(category: str, params: dict) -> dict:
    if _running():
        raise RpcError("EBUSY", "close Cemu first - it rewrites settings.xml on exit.")
    if not _dirty():
        return {"saved": False}
    disk = cfgutil.read_text(_SETTINGS)
    if disk is None:
        raise RpcError("ENOENT", "settings.xml not found - launch Cemu once to create it.")
    new = _write_block(disk, _BUF["entries"])         # the working model IS the whole block
    saved = False
    if new != disk:
        cfgutil.ensure_bak(_SETTINGS)
        cfgutil.atomic_write(_SETTINGS, new)
        staterev.bump("config")
        saved = True
    _reload()
    return {"saved": saved}


def _do_cancel(category: str, params: dict) -> dict:
    _reload()
    return {"cancelled": True}


def _register(category: str) -> None:
    ns = f"cemu_packs_{catkey(category)}"

    @method(f"{ns}.get", slow=True)                  # buffered: NO cache - the buffer is truth
    def _g(params, category=category):
        return _do_get(category, params)

    @method(f"{ns}.set", slow=True)
    def _s(params, category=category):
        return _do_set(category, params)

    @method(f"{ns}.save", slow=True)
    def _sv(params, category=category):
        return _do_save(category, params)

    @method(f"{ns}.cancel", slow=True)
    def _c(params, category=category):
        return _do_cancel(category, params)


for _cat in CATEGORIES:
    _register(_cat)
