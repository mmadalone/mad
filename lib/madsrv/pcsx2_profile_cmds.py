"""pcsx2prof.* / pcsx2profhh.* / pcsx2profpg.* / pcsx2profpghh.* — the PS2 docked/handheld
input-profile pickers (global + per-game), replacing the per-button Mappings editors.

Profiles are AUTHORED in PCSX2's own desktop UI (``~/.config/PCSX2/inputprofiles/<stem>.ini``);
MAD only PICKS one per context and the launch binder copies its ``[PadN]`` bodies into the
global ini transiently (lib/switch_bind.py -> pcsx2_cfg.apply_profile_bodies), so the pick
composes with the per-launch pad calibration. CRITICAL: native ``[Pad] InputProfileName`` is
NEVER written — PCSX2 would load the profile INSTEAD of ``[PadN]`` and silently bypass that
calibration.

Context placement follows the panel convention (the DOOR bakes the context): the PS2 tile
opens the DOCKED namespaces (``pcsx2prof`` global, ``pcsx2profpg`` per-game — the per-game
page also carries the relocated USB-port / Player-2 selectors); the On-the-go tile opens the
HANDHELD twins (``pcsx2profhh``, ``pcsx2profpghh``). Handheld never inherits docked.

Stores (no EBUSY anywhere — these pages never touch the live ini):
  global    [backends.pcsx2].profile_docked / .profile_handheld   (controller-policy.local.toml)
  per-game  entry["profiles"] = {"docked": <stem>, "handheld": <stem>}  (sparse) in
            ~/Emulation/storage/pcsx2/pergame-input.json, beside usb1/usb2/pad2/pads
            (shared with lib/madsrv/pcsx2_pergame_input_cmds, which owns the store file).
"""
from __future__ import annotations

from pathlib import Path

from .. import localpolicy, pcsx2_profiles, staterev
from ..policy import LOCAL, load_merged
from . import pcsx2_games
from . import pcsx2_pergame_input_cmds as pgin
from .rpc import RpcError, method

_NONE = "(none — current layout)"
_AUTHOR_NOTE = ("Profiles are created in PCSX2 itself (desktop mode: Settings → Controllers → "
                "Profile). The pick is applied at launch and reverted on exit; your controller "
                "assignment (Pads to players) still decides who is Player 1.")


def _cfg() -> dict:
    be = (load_merged().get("backends") or {}).get("pcsx2")
    return be if isinstance(be, dict) else {}


def _stems() -> list[str]:
    return pcsx2_profiles.list_stems(pcsx2_profiles.profiles_dir(_cfg()))


def _options(zero_label: str, current: str | None) -> tuple[list[str], int]:
    """``[zero_label] + stems`` (a stored-but-deleted stem stays VISIBLE, appended, so it can
    be seen and cleared instead of silently reading as unset) + the current row index."""
    opts = [zero_label] + _stems()
    if current and current not in opts:
        opts.append(current)
    return opts, (opts.index(current) if current and current in opts else 0)


def _ctx_key(context: str) -> str:
    return "profile_handheld" if context == "handheld" else "profile_docked"


# ── global pickers (pcsx2prof = docked door, pcsx2profhh = handheld door) ─────
def _register_global(ns: str, context: str, row_label: str) -> None:
    key = _ctx_key(context)

    @method(f"{ns}.get", slow=True)
    def _get(params):
        opts, val = _options(_NONE, pcsx2_profiles.global_profile(_cfg(), context))
        note = (f"The {context} input profile for every PS2 game (a game can override it under "
                f"Per-game). {_AUTHOR_NOTE}")
        return {"exists": True, "running": False, "note": note,
                "groups": [{"title": "Input profiles", "note": "", "settings": [
                    {"key": key, "label": row_label, "type": "enum",
                     "options": opts, "value": val, "picker": True}]}]}

    @method(f"{ns}.set", slow=True)
    def _set(params):
        if params.get("key") != key:
            raise RpcError("EINVAL", f"{params.get('key')!r} is not a profile setting")
        opts, _cur = _options(_NONE, pcsx2_profiles.global_profile(_cfg(), context))
        try:
            idx = int(float(params.get("value")))
        except (TypeError, ValueError):
            raise RpcError("EINVAL", "bad profile index")
        if not (0 <= idx < len(opts)):
            raise RpcError("EINVAL", "profile index out of range")
        # The index maps against a FRESH options list; the panel's page may be older
        # (profiles are authored in PCSX2 while MAD is open, and a new .ini bumps no
        # staterev). Requiring the resolved stem to exist on disk kills the worst
        # variant (storing a deleted stem); an insertion between get and set can still
        # remap an index onto a neighboring REAL stem — a panel-wide enum limitation,
        # bounded by this page note telling the user to reopen after authoring.
        if idx != 0 and pcsx2_profiles.profile_path(
                pcsx2_profiles.profiles_dir(_cfg()), opts[idx]) is None:
            raise RpcError("EINVAL",
                           "profile list changed on disk — reopen this page and pick again")
        data = localpolicy.load(LOCAL)
        be = data.setdefault("backends", {}).setdefault("pcsx2", {})
        if idx == 0:
            be.pop(key, None)
            if not be:
                data.get("backends", {}).pop("pcsx2", None)
        else:
            be[key] = opts[idx]
        localpolicy.dump(LOCAL, data)          # atomic + staterev.bump("config")
        return {"key": key, "value": idx}


_register_global("pcsx2prof", "docked", "Docked profile")
_register_global("pcsx2profhh", "handheld", "Handheld profile")


# ── per-game pickers (pcsx2profpg = docked + ports, pcsx2profpghh = handheld) ─
_INHERIT = "(inherit global)"
_USB_LABELS = ["Inherit global", "None (port off)"]
_PAD2_LABELS = ["Inherit global", "On", "Off"]


def _games_payload():
    store = pgin._load()

    def _ovr(key):
        e = store.get(key)
        return pgin._has_input_override(e) if isinstance(e, dict) else False

    out = []
    for g in pcsx2_games.games():
        override = _ovr(g["key"])
        stem = Path(g["path"]).stem if g.get("path") else ""
        out.append({"titleid": g["key"], "name": g["name"], "stem": stem,
                    "override": override, "summary": "Custom input" if override else ""})
    return {"games": out, "system": "ps2"}


def _inherit_label(context: str) -> str:
    g = pcsx2_profiles.global_profile(_cfg(), context)
    return f"(inherit global: {g})" if g else _INHERIT


def _entry(tid: str) -> dict:
    e = pgin._load().get(tid)
    return pgin._normalize_entry(dict(e)) if isinstance(e, dict) else {}


def _store_pergame(tid: str, mutate) -> None:
    """Load a FRESH store under the shared lock, apply ``mutate(entry)``, prune, save."""
    with pgin._LOCK:
        data = pgin._load()
        entry = data.get(tid)
        entry = pgin._normalize_entry(entry) if isinstance(entry, dict) else {}
        mutate(entry)
        if pgin._is_empty(entry):
            data.pop(tid, None)
        else:
            data[tid] = entry
        pgin._save(data)
    staterev.bump("config")


def _set_profile(entry: dict, context: str, stem: str | None) -> None:
    profiles = entry.get("profiles")
    if not isinstance(profiles, dict):
        profiles = {}
        entry.pop("profiles", None)
    if stem:
        profiles[context] = stem
    else:
        profiles.pop(context, None)
    if profiles:
        entry["profiles"] = profiles
    else:
        entry.pop("profiles", None)


def _register_pergame(ns: str, context: str, with_ports: bool) -> None:
    row_label = "Docked profile" if context == "docked" else "Handheld profile"

    @method(f"{ns}.games", slow=True)
    def _games(params):
        return _games_payload()

    @method(f"{ns}.get", slow=True)
    def _get(params):
        tid = pgin._titleid(params)
        e = _entry(tid)
        opts, val = _options(_inherit_label(context),
                             pcsx2_profiles.pergame_profile(e, context))
        groups = [{"title": "Input profile", "note": "", "settings": [
            {"key": "profile", "label": row_label, "type": "enum",
             "options": opts, "value": val, "picker": True}]}]
        if with_ports:
            usb1, usb2, pad2 = e.get("usb1"), e.get("usb2"), e.get("pad2")
            groups.append({"title": "Ports & players", "note": "", "settings": [
                {"key": "usb1", "label": "USB Port 1", "type": "enum",
                 "options": _USB_LABELS, "value": 1 if usb1 == "None" else 0},
                {"key": "usb2", "label": "USB Port 2", "type": "enum",
                 "options": _USB_LABELS, "value": 1 if usb2 == "None" else 0},
                {"key": "pad2", "label": "Player 2 pad", "type": "enum",
                 "options": _PAD2_LABELS,
                 "value": 0 if pad2 is None else (1 if pad2 else 2)}]})
        note = (f"The {context} input profile for THIS game only — '(inherit global)' uses the "
                f"tile-level pick. Applied at launch, reverted on exit. {_AUTHOR_NOTE}")
        return {"exists": True, "running": False, "note": note, "groups": groups}

    @method(f"{ns}.set", slow=True)
    def _set(params):
        tid = pgin._titleid(params)
        key = params.get("key")
        try:
            idx = int(float(params.get("value")))
        except (TypeError, ValueError):
            raise RpcError("EINVAL", "bad option index")
        if key == "profile":
            e = _entry(tid)
            opts, _cur = _options(_inherit_label(context),
                                  pcsx2_profiles.pergame_profile(e, context))
            if not (0 <= idx < len(opts)):
                raise RpcError("EINVAL", "profile index out of range")
            stem = None if idx == 0 else opts[idx]
            if stem is not None and pcsx2_profiles.profile_path(
                    pcsx2_profiles.profiles_dir(_cfg()), stem) is None:
                raise RpcError("EINVAL",
                               "profile list changed on disk — reopen this page and pick again")
            _store_pergame(tid, lambda entry: _set_profile(entry, context, stem))
        elif with_ports and key in ("usb1", "usb2"):
            if not (0 <= idx < len(_USB_LABELS)):
                raise RpcError("EINVAL", "option index out of range")

            def _mut(entry, key=key, idx=idx):
                if idx == 0:
                    entry.pop(key, None)
                else:
                    entry[key] = "None"
            _store_pergame(tid, _mut)
        elif with_ports and key == "pad2":
            if not (0 <= idx < len(_PAD2_LABELS)):
                raise RpcError("EINVAL", "option index out of range")

            def _mut(entry, idx=idx):
                if idx == 0:
                    entry.pop("pad2", None)
                else:
                    entry["pad2"] = (idx == 1)
            _store_pergame(tid, _mut)
        else:
            raise RpcError("EINVAL", f"unknown key {key!r}")
        return {"key": key, "value": idx}


_register_pergame("pcsx2profpg", "docked", with_ports=True)
_register_pergame("pcsx2profpghh", "handheld", with_ports=False)
