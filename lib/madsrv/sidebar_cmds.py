"""sidebar.* — which MAD control-panel sidebar sections should be VISIBLE, and how to
override that. The C++ panel calls `sidebar.sections` on backend-ready and filters its
sidebar; the "Sidebar" page calls `sidebar.set` to force a row on/off.

Visibility = capability auto-hide + install.conf FORCE_SHOW_*/FORCE_HIDE_* overrides:
    "sidebar" row -> ALWAYS visible (the toggle page is the escape hatch; can't be hidden)
    any other row -> (core OR capability_met OR FORCE_SHOW) AND NOT FORCE_HIDE
                     (so even core rows like Preview can be hidden now)
Order: a saved SIDEBAR_ORDER (comma-separated keys) reorders the rows; unlisted keys keep
catalog order. `sidebar.set_order` writes it; the C++ panel renders rows in that order.
The capability-gated rows hide the things a user can't use yet:
    lightgun     <- Sinden driver installed (~/Lightgun/*)
    x-arcade     <- an X-Arcade cabinet has been identified ([hardware].xarcade_port set)
    retroarch    <- RetroArch is installed (the hub is empty without it; --standalone rigs
                    without RetroArch would otherwise dead-end into an empty grid)
    bezelproject <- RetroArch is installed  (NOT "packs present" — the page is HOW you get
                    packs, so gating on packs would hide the very page you need)
"""
from __future__ import annotations

from pathlib import Path

from .. import install_conf, routing
from ..policy import load_merged
from ..retroarch_cfg import RA_CONFIG_BASE
from .rpc import RpcError, method

# (key, label, core?, capability-probe name | None). Keys match the C++ mSections artKeys.
_SECTIONS = [
    ("preview", "Preview", True, None),
    ("systems", "Systems", True, None),
    ("priority", "Priority", True, None),
    ("players", "Players", True, None),
    ("quit-combo", "Quit combo", True, None),
    ("lightgun", "Lightgun", False, "sinden"),
    ("standalones", "Standalones", True, None),
    ("retroarch", "RetroArch", False, "retroarch"),
    ("bezelproject", "Bezel Project", False, "retroarch"),
    ("x-arcade", "X-Arcade", False, "xarcade"),
    ("gamepads", "Gamepads", True, None),
    ("splash", "Splash", True, None),
    ("backup", "Backup", True, None),
    ("sidebar", "Sidebar", True, None),   # the toggle page itself — always shown
]
_TRUE = install_conf._TRUE   # share the truthy set with the shell + install_conf readers


def _sinden_installed() -> bool:
    lg = Path.home() / "Lightgun"
    return all((lg / f).is_file()
               for f in ("LightgunMono.exe", "libCameraInterface.so", "libSdlInterface.so"))


def _retroarch_installed() -> bool:
    return RA_CONFIG_BASE.exists() or (RA_CONFIG_BASE.parent / "retroarch.cfg").exists()


def _xarcade_present() -> bool:
    try:
        return bool(routing.xarcade_port(load_merged()))
    except Exception:
        return False


# Indirected through a dict so tests can monkeypatch a probe.
_PROBES = {"sinden": _sinden_installed, "retroarch": _retroarch_installed, "xarcade": _xarcade_present}


def _tok(key: str) -> str:
    return key.upper().replace("-", "_")


def _keys() -> set:
    return {k for k, _, _, _ in _SECTIONS}


def _sections(conf: dict | None = None) -> list:
    conf = install_conf.load() if conf is None else conf
    rows = {}
    catalog = []
    for key, label, core, probe in _SECTIONS:
        tok = _tok(key)
        force_show = conf.get(f"FORCE_SHOW_{tok}", "").strip().lower() in _TRUE
        force_hide = conf.get(f"FORCE_HIDE_{tok}", "").strip().lower() in _TRUE
        cap = True if probe is None else _PROBES[probe]()
        never_hide = (key == "sidebar")   # the toggle page is the escape hatch
        visible = True if never_hide else ((core or cap or force_show) and not force_hide)
        rows[key] = {"key": key, "label": label, "core": core, "capability_met": cap,
                     "force_show": force_show, "force_hide": force_hide,
                     "visible": visible, "can_hide": not never_hide}
        catalog.append(key)
    # Honor a saved order; drop unknown + duplicate keys, append any not listed in catalog order.
    saved = []
    seen = set()
    for s in (x.strip() for x in install_conf.get("SIDEBAR_ORDER", "", conf).split(",")):
        if s in rows and s not in seen:
            saved.append(s)
            seen.add(s)
    ordered = saved + [k for k in catalog if k not in seen]
    return [rows[k] for k in ordered]


@method("sidebar.sections")  # not cached: the FS capability probes (Sinden/RA) don't bump staterev, so a cached row set could go stale mid-session
def _sidebar_sections(params):
    """Per-section {key,label,core,capability_met,force_show,force_hide,visible}."""
    return {"sections": _sections()}


@method("sidebar.set")
def _sidebar_set(params):
    """Override any section's visibility. {key, mode: auto|show|hide}. Writes FORCE_SHOW/HIDE in
    install.conf (which bumps staterev "config", refreshing sidebar.sections). The "sidebar" page
    can't be hidden (escape hatch) — a hide on it is rejected."""
    key = params.get("key", "")
    mode = params.get("mode", "auto")
    if key not in _keys():
        raise RpcError("EINVAL", f"unknown section {key!r}")
    if mode not in ("auto", "show", "hide"):
        raise RpcError("EINVAL", f"bad mode {mode!r} (auto|show|hide)")
    if key == "sidebar" and mode == "hide":
        raise RpcError("EINVAL", "the Sidebar page can't be hidden")
    tok = _tok(key)
    install_conf.set_value(f"FORCE_SHOW_{tok}", "1" if mode == "show" else "0")
    install_conf.set_value(f"FORCE_HIDE_{tok}", "1" if mode == "hide" else "0")
    return {"ok": True, "key": key, "mode": mode}


@method("sidebar.set_order")
def _sidebar_set_order(params):
    """Persist the sidebar section order. {order: ["key", ...]}. Unknown keys are dropped;
    any omitted keys keep their catalog order (appended by sidebar.sections). Writes
    SIDEBAR_ORDER (bumps staterev "config")."""
    valid = _keys()
    order = []
    seen = set()
    for k in (params.get("order") or []):
        if k in valid and k not in seen:
            order.append(k)
            seen.add(k)
    install_conf.set_value("SIDEBAR_ORDER", ",".join(order))
    return {"ok": True, "order": order}
