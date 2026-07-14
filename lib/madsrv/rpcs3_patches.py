"""rpcs3_patches — parse RPCS3 ``patches/patch.yml`` into a serial->patches index and
read/write ``patch_config.yml`` (the enabled-state file RPCS3 applies at game boot -- it lives
at the config ROOT ~/.config/rpcs3/, NOT under patches/).
Pure helpers (no RPC); every failure degrades to empty rather than raising.

patch.yml quirks handled (verified against the live 880 KB file + RPCS3 master
``Utilities/bin_patch.cpp`` on 2026-07-14):
  * DUPLICATE top-level ``PPU-<hash>`` keys and multiple ``Anchors:`` blocks -- yaml-cpp
    iterates raw nodes, but PyYAML ``safe_load`` collapses duplicate mapping keys (last
    wins) and would silently DROP patches. -> a node-level walk that preserves duplicates.
  * DUPLICATE anchors (e.g. ``&32_9_value`` defined twice) -- yaml-cpp lets a later anchor
    override for subsequent aliases; PyYAML's Composer raises. -> a Composer subclass that
    tolerates redefinition (most-recent def wins, matching yaml-cpp).
  * version scalars like ``01.00`` MUST stay the raw STRING: RPCS3 reads app_version via
    ``.Scalar()`` and the patch_config version key must match it byte-for-byte. PyYAML's
    resolver would turn ``01.00`` into the float ``1.0`` and the patch would never apply.
    -> we read ``node.value`` (raw text), never construct these scalars.
Parsing the big file is ~3.5 s, so the index is cached to disk keyed by patch.yml mtime+size
(mirrors ``pcsx2_games.ws_index``), rebuilt only when patch.yml changes.

patch_config.yml format (RPCS3 ``save_config``/``load_config``, master):
  ``<FULL PPU-hash>: {<desc>: {<title>: {<serial>: {<app_version>:
      {Enabled: true, "Configurable Values": {<param>: <bare-number>}}}}}}``
  * ``Enabled`` written only when true; ``Configurable Values`` only when a value differs from
    the patch's default ``Value``; values are bare numbers (f64), not labels. A legacy
    ``<version>: true`` scalar form is tolerated on read.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

try:
    import yaml
    from yaml.composer import ComposerError
    from yaml.events import (AliasEvent, MappingStartEvent, ScalarEvent,
                             SequenceStartEvent)
    from yaml.nodes import MappingNode, ScalarNode, SequenceNode
except ImportError:                    # PyYAML missing -> no patches feature
    yaml = None

from .. import mad_paths
from . import cfgutil

_DIR = Path.home() / ".config/rpcs3/patches"
_PATCH_YML = _DIR / "patch.yml"
# patch_config.yml lives at the CONFIG ROOT (~/.config/rpcs3/), NOT the patches/ subdir --
# that is where RPCS3 actually reads & writes it (verified on-device). Only patch.yml is under
# patches/. Pointing at patches/patch_config.yml makes the whole feature a no-op.
_PATCH_CONFIG = Path.home() / ".config/rpcs3/patch_config.yml"
_SERIAL_RE = re.compile(r"^[A-Z]{4}[0-9]{5}\Z")
_SKIP_KEYS = {"Anchors", "Version"}


def _index_file() -> Path:
    return mad_paths.storage("rpcs3", "patch-index.json")


# ── dup-key + dup-anchor tolerant composition ─────────────────────────────────
if yaml is not None:
    class _Composer(yaml.SafeLoader):
        def compose_node(self, parent, index):
            # Like yaml.Composer.compose_node but WITHOUT the duplicate-anchor guard, so a
            # redefined anchor just overwrites (yaml-cpp semantics: later def wins).
            if self.check_event(AliasEvent):
                event = self.get_event()
                if event.anchor not in self.anchors:
                    raise ComposerError(None, None, "found undefined alias %r" % event.anchor,
                                        event.start_mark)
                return self.anchors[event.anchor]
            event = self.peek_event()
            anchor = event.anchor
            self.descend_resolver(parent, index)
            if self.check_event(ScalarEvent):
                node = self.compose_scalar_node(anchor)
            elif self.check_event(SequenceStartEvent):
                node = self.compose_sequence_node(anchor)
            elif self.check_event(MappingStartEvent):
                node = self.compose_mapping_node(anchor)
            else:                                      # defensive: never expected at top level
                node = None
            self.ascend_resolver()
            return node


def _root_node(path: Path):
    """The composed root MappingNode (aliases resolved, duplicates preserved), or None."""
    if yaml is None:
        return None
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            loader = _Composer(f)
            try:
                node = loader.get_single_node()
            finally:
                loader.dispose()
    except (OSError, yaml.YAMLError, ComposerError):
        return None
    return node if isinstance(node, MappingNode) else None


def _scalar(node):
    return node.value if isinstance(node, ScalarNode) else None


def _map_items(node):
    return node.value if isinstance(node, MappingNode) else []


def _seq_items(node):
    return node.value if isinstance(node, SequenceNode) else []


# ── configurable-values parse (kept plain-data so the index is JSON-cacheable) ──
def _parse_cfg(cfg_node) -> dict:
    """A ``Configurable Values`` node -> {param: {type, default, allowed:[[label,raw]],
    min, max}} with every number kept as its RAW string (byte-exact write-back)."""
    out: dict = {}
    for pname_node, spec_node in _map_items(cfg_node):
        pname = _scalar(pname_node)
        if pname is None:
            continue
        typ = dflt = mn = mx = None
        allowed: list = []
        for sk, sv in _map_items(spec_node):
            key = _scalar(sk)
            if key == "Type":
                typ = _scalar(sv)
            elif key == "Value":
                dflt = _scalar(sv)
            elif key == "Min":
                mn = _scalar(sv)
            elif key == "Max":
                mx = _scalar(sv)
            elif key == "Allowed Values":
                allowed = [[_scalar(lk), _scalar(lv)] for lk, lv in _map_items(sv)
                           if _scalar(lk) is not None]
        out[pname] = {"type": typ, "default": dflt, "allowed": allowed, "min": mn, "max": mx}
    return out


def _build_index(path: Path):
    """serial -> [ {hash, desc, title, versions:[raw str], group, cfg} ] (plain data).
    Returns None (NOT {}) when the file could not be parsed, so a TRANSIENT read failure is
    not mistaken for a genuinely-empty DB and cached forever (mirrors pcsx2_games ws_index)."""
    root = _root_node(path)
    if root is None:
        return None
    idx: dict = {}
    for k_node, v_node in root.value:
        hash_key = _scalar(k_node)
        if hash_key in _SKIP_KEYS or not isinstance(v_node, MappingNode):
            continue
        for desc_node, patch_node in v_node.value:
            desc = _scalar(desc_node)
            if desc is None or not isinstance(patch_node, MappingNode):
                continue
            games = group = cfg_node = None
            for pk, pv in patch_node.value:
                key = _scalar(pk)
                if key == "Games":
                    games = pv
                elif key == "Group":
                    group = _scalar(pv)
                elif key == "Configurable Values":
                    cfg_node = pv
                # Patch / Author / Notes / Patch Version deliberately not constructed
            if games is None:
                continue
            cfg = _parse_cfg(cfg_node) if cfg_node is not None else None
            for title_node, serials_node in _map_items(games):
                title = _scalar(title_node)
                for serial_node, versions_node in _map_items(serials_node):
                    serial = _scalar(serial_node)
                    if serial is None:
                        continue
                    versions = [v for vn in _seq_items(versions_node)
                                if (v := _scalar(vn)) is not None]
                    idx.setdefault(serial, []).append(
                        {"hash": hash_key, "desc": desc, "title": title,
                         "versions": versions, "group": group, "cfg": cfg})
    return idx


# ── disk + memory cached index ────────────────────────────────────────────────
_MEM: dict = {"key": None, "idx": None}


def _cache_key(path: Path):
    try:
        st = path.stat()
    except OSError:
        return None
    return [str(path), st.st_mtime, st.st_size]


def load_index() -> dict:
    """serial -> patch-entry list. Memory-cached this process; disk-cached across restarts
    (keyed by patch.yml mtime+size). Empty dict if patch.yml is missing / PyYAML absent."""
    if yaml is None:
        return {}
    key = _cache_key(_PATCH_YML)
    if key is None:
        return {}
    if _MEM["key"] == key and _MEM["idx"] is not None:
        return _MEM["idx"]
    idxf = _index_file()
    if idxf.is_file():
        try:
            data = json.loads(idxf.read_text(encoding="utf-8"))
            if data.get("key") == key and isinstance(data.get("idx"), dict):
                _MEM.update(key=key, idx=data["idx"])
                return data["idx"]
        except Exception:
            pass
    idx = _build_index(_PATCH_YML)
    if idx is None:                       # transient parse/read failure -> serve empty, DON'T cache
        return {}
    try:
        idxf.parent.mkdir(parents=True, exist_ok=True)
        cfgutil.atomic_write(idxf, json.dumps({"key": key, "idx": idx}))
    except OSError:
        pass
    _MEM.update(key=key, idx=idx)
    return idx


# ── numeric helpers ───────────────────────────────────────────────────────────
def num(raw):
    """Raw config scalar -> int (if an integer literal) else float, else None."""
    if raw is None:
        return None
    s = str(raw).strip()
    try:
        if re.fullmatch(r"[+-]?\d+", s):
            return int(s)
        return float(s)
    except ValueError:
        return None


def _close(a, b) -> bool:
    """Numeric equality within a RELATIVE epsilon (no absolute floor). A max(1.0, ...) floor
    would make every pair of values below 1 compare equal within 1e-9 and merge a patch's tiny
    sentinel enum values (e.g. Uncharted 3 split-screen 2e-37 vs 4e-37)."""
    if a is None or b is None:
        return False
    a, b = float(a), float(b)
    if a == b:
        return True
    m = max(abs(a), abs(b))
    return m > 0 and abs(a - b) <= 1e-9 * m


def _fmt(x, is_long: bool) -> str:
    xf = float(x)
    if is_long or xf.is_integer():
        return str(int(round(xf)))
    return f"{xf:.6g}"


def _range_stops(mn, mx, default, is_long: bool, target: int = 12):
    """A controller-friendly list of preset (label, num) stops spanning [mn, mx], always
    including mn, mx and the default. Nice 1/2/2.5/5 step sizing (axis-tick style)."""
    lo, hi = float(mn), float(mx)
    dv = float(default) if default is not None else lo
    vals = {lo, hi, dv}
    if hi > lo:
        raw = (hi - lo) / max(1, target)
        if raw > 0:
            mag = 10 ** math.floor(math.log10(raw))
            step = next((m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw), 10 * mag)
            x = math.ceil(lo / step) * step
            while x <= hi + step * 1e-6:
                vals.add(round(x, 10))
                x += step
    if is_long:
        vals = {round(v) for v in vals}
    ordered = sorted(vals)
    return [(_fmt(v, is_long), (int(round(v)) if is_long else v)) for v in ordered]


# ── per-serial patch view (deduped by desc, targets flattened) ────────────────
def patches_for(serial: str) -> list[dict]:
    """The distinct patches applicable to `serial`, deduped by description and preserving
    first-seen order. Each: {desc, group, cfg, targets:[{hash,title,serial,version}]}.
    `targets` is every (hash,title,version) the enabled state must be written under (RPCS3
    applies only the one whose executable hash matches the actual disc; extra targets are
    harmless)."""
    entries = load_index().get(serial) or []
    by_desc: dict = {}
    order: list = []
    for e in entries:
        desc = e["desc"]
        p = by_desc.get(desc)
        if p is None:
            p = by_desc[desc] = {"desc": desc, "group": e.get("group"),
                                 "cfg": e.get("cfg"), "targets": []}
            order.append(desc)
        if p["cfg"] is None and e.get("cfg") is not None:
            p["cfg"] = e["cfg"]
        if p["group"] is None and e.get("group") is not None:
            p["group"] = e["group"]
        for v in (e.get("versions") or []):
            t = {"hash": e["hash"], "title": e["title"], "serial": serial, "version": v}
            if t not in p["targets"]:
                p["targets"].append(t)
    return [by_desc[d] for d in order]


def value_options(spec: dict):
    """For one configurable param spec -> (options:list[(label,num)], default_num, is_long).
    enum -> the Allowed Values; range -> generated preset stops. [] if unusable."""
    typ = (spec.get("type") or "").lower()
    is_long = typ.startswith("long")
    default_num = num(spec.get("default"))
    if typ.endswith("_enum"):
        opts = [(lbl, num(raw)) for lbl, raw in (spec.get("allowed") or [])
                if num(raw) is not None]
        return opts, default_num, is_long
    if typ.endswith("_range"):
        mn, mx = num(spec.get("min")), num(spec.get("max"))
        if mn is None or mx is None:
            return [], default_num, is_long
        return _range_stops(mn, mx, spec.get("default"), is_long), default_num, is_long
    return [], default_num, is_long


# ── patch_config.yml read / write ─────────────────────────────────────────────
def _node_to_data(node, ctor):
    """Convert a composed node to plain data KEEPING every mapping key as its RAW string.
    RPCS3 writes patch_config.yml version keys UNQUOTED (e.g. `01.00:`); yaml.safe_load would
    resolve them to floats (1.0) so string lookups miss AND every NN.NN key across the whole
    file is lossily re-emitted as `1.0:` on the next save, silently breaking OTHER games'
    patches. Scalar VALUES are still typed by their tag (Enabled -> bool, config -> number)."""
    if isinstance(node, MappingNode):
        out = {}
        for k, v in node.value:
            key = k.value if isinstance(k, ScalarNode) else None
            if key is not None:
                out[key] = _node_to_data(v, ctor)
        return out
    if isinstance(node, SequenceNode):
        return [_node_to_data(v, ctor) for v in node.value]
    if isinstance(node, ScalarNode):
        try:
            return ctor.construct_object(node, deep=True)
        except Exception:
            return node.value
    return None


def read_config():
    """The whole patch_config.yml as a nested dict, with version keys preserved as STRINGS.
    Returns {} when the file is absent or genuinely empty; returns None when the file is
    present and NON-empty but could not be parsed (so a caller can refuse to overwrite it and
    destroy other games' entries), distinct from a real empty {}."""
    if yaml is None or not _PATCH_CONFIG.is_file():
        return {}
    node = _root_node(_PATCH_CONFIG)
    if node is None:
        try:                                            # empty/whitespace file is a VALID empty {}
            if not _PATCH_CONFIG.read_text(encoding="utf-8", errors="replace").strip():
                return {}
        except OSError:
            return None
        return None                                     # non-empty but unparseable
    return _node_to_data(node, yaml.constructor.SafeConstructor())


def write_config(data: dict) -> None:
    """Atomically write patch_config.yml (one-time .bak), preserving key order."""
    if yaml is None:
        return
    _PATCH_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    cfgutil.ensure_bak(_PATCH_CONFIG)
    text = yaml.safe_dump(data or {}, sort_keys=False, default_flow_style=False,
                          allow_unicode=True)
    cfgutil.atomic_write(_PATCH_CONFIG, text)


def _leaf_enabled(node) -> bool:
    """Enabled state of a patch_config <version> leaf (map with Enabled, or legacy scalar)."""
    if isinstance(node, dict):
        return bool(node.get("Enabled"))
    return bool(node)                              # legacy `<version>: true`


def _leaf_values(node) -> dict:
    return node.get("Configurable Values") or {} if isinstance(node, dict) else {}


def state_from_config(serial: str, patches: list[dict], cfg: dict) -> dict:
    """Current on-disk state for the serial's patches from a loaded patch_config dict.
    -> {desc: {"enabled": bool, "vals": {param: num}}}. vals default to each param's default,
    overridden by any stored Configurable Value. A None cfg (unparseable file) reads as empty
    for display; saving over such a file is refused separately in the cmds layer."""
    cfg = cfg or {}
    state: dict = {}
    for p in patches:
        desc = p["desc"]
        enabled = False
        stored_vals: dict = {}
        for t in p["targets"]:
            leaf = (((cfg.get(t["hash"]) or {}).get(desc) or {}).get(t["title"]) or {})
            leaf = (leaf.get(t["serial"]) or {}).get(t["version"])
            if leaf is None:
                continue
            if _leaf_enabled(leaf):
                enabled = True
            for k, v in _leaf_values(leaf).items():
                stored_vals.setdefault(k, num(v))
        vals: dict = {}
        for param, spec in (p.get("cfg") or {}).items():
            vals[param] = stored_vals.get(param, num(spec.get("default")))
        state[desc] = {"enabled": enabled, "vals": vals}
    return state


def _prune(d):
    """Recursively drop empty-dict children; return d (or {} if it collapses)."""
    for k in list(d.keys()):
        v = d[k]
        if isinstance(v, dict):
            _prune(v)
            if not v:
                del d[k]
    return d


def apply_state(cfg: dict, patches: list[dict], state: dict) -> dict:
    """Merge the serial's desired `state` into a loaded patch_config dict `cfg` (mutates &
    returns it). Other games' entries are untouched. Enabled patches are written; disabled
    patches removed; empty parents pruned. Per RPCS3 apply semantics (each configurable param
    is looked up independently, defaulting when absent): a MANAGED param is written only when
    it differs from the patch default (omitted = default), so a patch at all-defaults writes
    just `Enabled: true`.

    A param whose default does NOT parse as a plain decimal number (RPCS3's rare hex-bit-pattern
    config values) is NOT managed here: no picker renders (value_options drops it) and we never
    author a value for it -- but any value ALREADY on disk for it (e.g. set via RPCS3's own Patch
    Manager) is PRESERVED verbatim, so a MAD save of an unrelated toggle can't revert it."""
    for p in patches:
        desc = p["desc"]
        st = state.get(desc) or {}
        enabled = bool(st.get("enabled"))
        vals = st.get("vals") or {}
        managed = {k: v for k, v in (p.get("cfg") or {}).items() if num(v.get("default")) is not None}
        for t in p["targets"]:
            h, title, ser, ver = t["hash"], t["title"], t["serial"], t["version"]
            if enabled:
                prev = cfg.get(h, {}).get(desc, {}).get(title, {}).get(ser, {}).get(ver)
                prev_cv = prev.get("Configurable Values") if isinstance(prev, dict) else None
                cv = dict(prev_cv) if isinstance(prev_cv, dict) else {}   # keep unmanaged params
                for param, spec in managed.items():
                    dflt = num(spec.get("default"))
                    chosen = vals.get(param, dflt)
                    if chosen is None or _close(chosen, dflt):
                        cv.pop(param, None)                     # missing/garbage or default -> omit (never null)
                    else:
                        cv[param] = chosen
                leaf = {"Enabled": True}
                if cv:
                    leaf["Configurable Values"] = cv
                cfg.setdefault(h, {}).setdefault(desc, {}).setdefault(title, {}) \
                   .setdefault(ser, {})[ver] = leaf
            else:
                node = cfg.get(h, {}).get(desc, {}).get(title, {}).get(ser, {})
                if ver in node:
                    del node[ver]
    return _prune(cfg)


def is_serial(s: str) -> bool:
    return bool(_SERIAL_RE.match(s or ""))
