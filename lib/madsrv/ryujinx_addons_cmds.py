"""ryujinx_addons.* — Ryujinx (Switch) per-game Add-Ons: Mods, Update, DLC.

Ryujinx keeps each of these in its OWN per-title JSON under games/<titleid-lower>/ (NOT in
Config.json; see deck-docs/ryubing-config.md), so we edit those files directly:

  mods.json    {"mods":[{"name","path","enabled":bool}]}                 -> multi-toggle (enabled)
  updates.json {"selected":"<nsp path or empty>","paths":[...]}          -> single-SELECT applied update
  dlc.json     [{"path":<container>,"dlc_nca_list":[{"path","title_id","is_enabled":bool}]}]  -> multi-toggle

DLC is exposed ONLY when a dlc.json already exists (authoring one needs NCA parsing MAD lacks -- we
never create it). Rendered per game by GuiMadPageEmuSettings; writes refuse while Ryujinx runs (it
rewrites these on its own edits/exit). Row keys: mod:<idx>, update (an enum), dlc:<ci>:<ni>.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .. import fsutil, proc_guard
from . import cfgutil, ryujinx_json
from .rpc import RpcError, method

_PROC = "ryujinx"
_TID_RE = re.compile(r"^[0-9A-Fa-f]{16}$")


def _running() -> bool:
    return proc_guard.emulator_running(_PROC)


def _tid(params) -> str:
    t = params.get("titleid") or ""
    if not _TID_RE.match(t):
        raise RpcError("EINVAL", f"bad game id {t!r}")
    return t


def _game_dir(tid: str) -> Path:
    # Computed from the live config path (test-friendly: stubbing ryujinx_json.CONFIG redirects it).
    return Path(ryujinx_json.CONFIG).parent / "games" / tid.lower()


def _load(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write(path: Path, data) -> None:
    cfgutil.ensure_bak(path)                              # one-time .bak before first edit
    fsutil.atomic_write_text(path, json.dumps(data, indent=2) + "\n")   # bumps staterev("config")


# ── get ──────────────────────────────────────────────────────────────────────
def has_content(tid: str) -> bool:
    """True if this game has any Ryujinx add-on -- a mod, an update path, or a DLC entry (mirrors the
    has_any check in _get). Used to hide the empty per-game Add-Ons tile."""
    gdir = _game_dir(tid)
    mods = _load(gdir / "mods.json")
    if isinstance(mods, dict) and mods.get("mods"):
        return True
    upd = _load(gdir / "updates.json")
    if isinstance(upd, dict) and upd.get("paths"):
        return True
    dlc = _load(gdir / "dlc.json")
    if isinstance(dlc, list):
        for cont in dlc:
            if isinstance(cont, dict) and (cont.get("dlc_nca_list") or []):
                return True
    return False


@method("ryujinx_addons.get", slow=True)
def _get(params):
    tid = _tid(params)
    gdir = _game_dir(tid)
    groups = []

    mods = _load(gdir / "mods.json")
    mod_rows = []
    if isinstance(mods, dict) and isinstance(mods.get("mods"), list):
        for i, m in enumerate(mods["mods"]):
            if isinstance(m, dict):
                mod_rows.append({"key": f"mod:{i}", "label": m.get("name") or f"Mod {i + 1}",
                                 "type": "bool", "value": bool(m.get("enabled", True))})
    groups.append({"title": "Mods",
                   "note": "" if mod_rows else "No mods installed for this game.",
                   "settings": mod_rows})

    upd = _load(gdir / "updates.json")
    if isinstance(upd, dict) and isinstance(upd.get("paths"), list) and upd["paths"]:
        paths = upd["paths"]
        sel = upd.get("selected") or ""
        opts = ["None (base game)"] + [Path(p).name for p in paths]
        val = paths.index(sel) + 1 if sel in paths else 0
        groups.append({"title": "Update", "note": "", "settings": [
            {"key": "update", "label": "Applied update", "type": "enum",
             "options": opts, "value": val}]})

    dlc = _load(gdir / "dlc.json")
    if isinstance(dlc, list):
        dlc_rows = []
        for ci, cont in enumerate(dlc):
            if not isinstance(cont, dict):
                continue
            cname = Path(cont.get("path", "")).name
            for ni, nca in enumerate(cont.get("dlc_nca_list") or []):
                if isinstance(nca, dict):
                    label = (cname + " / " + Path(nca.get("path", "")).name).strip(" /")
                    dlc_rows.append({"key": f"dlc:{ci}:{ni}", "label": label or f"DLC {ci}.{ni}",
                                     "type": "bool", "value": bool(nca.get("is_enabled", True))})
        if dlc_rows:
            groups.append({"title": "DLC", "note": "", "settings": dlc_rows})

    has_any = any(g["settings"] for g in groups)
    note = ("Enable/disable mods, choose which update is applied, and toggle DLC for this game."
            if has_any else
            "No add-ons found for this game. Add mods/updates/DLC in Ryujinx to manage them here.")
    return {"exists": True, "running": _running(), "note": note, "groups": groups}


# ── set ──────────────────────────────────────────────────────────────────────
@method("ryujinx_addons.set", slow=True)
def _set(params):
    if _running():
        raise RpcError("EBUSY", "close Ryujinx first — it rewrites its add-on files on exit.")
    tid = _tid(params)
    gdir = _game_dir(tid)
    key = params.get("key", "")

    if key.startswith("mod:"):
        on = str(params.get("value", "")).strip().lower() in cfgutil._TRUE
        i = int(key[len("mod:"):])
        path = gdir / "mods.json"
        data = _load(path)
        if isinstance(data, dict) and isinstance(data.get("mods"), list) and 0 <= i < len(data["mods"]):
            data["mods"][i]["enabled"] = on
            _write(path, data)
        return {"key": key, "value": on}

    if key == "update":
        idx = int(params.get("value"))
        path = gdir / "updates.json"
        data = _load(path)
        if isinstance(data, dict):
            paths = data.get("paths") or []
            data["selected"] = "" if idx <= 0 else (paths[idx - 1] if idx - 1 < len(paths) else "")
            _write(path, data)
        return {"key": key, "value": idx}

    if key.startswith("dlc:"):
        on = str(params.get("value", "")).strip().lower() in cfgutil._TRUE
        try:
            _, ci_s, ni_s = key.split(":", 2)
            ci, ni = int(ci_s), int(ni_s)
        except (ValueError, IndexError):
            raise RpcError("EINVAL", f"bad DLC key {key!r}")
        path = gdir / "dlc.json"
        data = _load(path)
        if isinstance(data, list) and 0 <= ci < len(data):
            lst = data[ci].get("dlc_nca_list") or []
            if 0 <= ni < len(lst):
                lst[ni]["is_enabled"] = on
                _write(path, data)
        return {"key": key, "value": on}

    raise RpcError("EINVAL", f"{key!r} is not an add-on toggle")
