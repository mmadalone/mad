"""sidebar.* — which MAD control-panel sidebar sections should be VISIBLE, and how to
override that. The C++ panel calls `sidebar.sections` on backend-ready and filters its
sidebar; the "Sidebar" page calls `sidebar.set` to force a row on/off.

Visibility = capability auto-hide + install.conf FORCE_SHOW_*/FORCE_HIDE_* overrides:
    CORE row            -> always visible (FORCE_* ignored; you can't lose Preview)
    capability-gated row -> (capability_met OR FORCE_SHOW) AND NOT FORCE_HIDE
The capability-gated rows hide the things a user can't use yet:
    lightgun     <- Sinden driver installed (~/Lightgun/*)
    x-arcade     <- an X-Arcade cabinet has been identified ([hardware].xarcade_port set)
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
    ("retroarch", "RetroArch", True, None),
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


def _togglable() -> set:
    return {k for k, _, core, _ in _SECTIONS if not core}


def _sections(conf: dict | None = None) -> list:
    conf = install_conf.load() if conf is None else conf
    out = []
    for key, label, core, probe in _SECTIONS:
        tok = _tok(key)
        force_show = conf.get(f"FORCE_SHOW_{tok}", "").strip().lower() in _TRUE
        force_hide = conf.get(f"FORCE_HIDE_{tok}", "").strip().lower() in _TRUE
        cap = True if probe is None else _PROBES[probe]()
        visible = True if core else ((cap or force_show) and not force_hide)
        out.append({"key": key, "label": label, "core": core, "capability_met": cap,
                    "force_show": force_show, "force_hide": force_hide, "visible": visible})
    return out


@method("sidebar.sections")  # not cached: the FS capability probes (Sinden/RA) don't bump staterev, so a cached row set could go stale mid-session
def _sidebar_sections(params):
    """Per-section {key,label,core,capability_met,force_show,force_hide,visible}."""
    return {"sections": _sections()}


@method("sidebar.set")
def _sidebar_set(params):
    """Override a togglable section. {key, mode: auto|show|hide}. Writes FORCE_SHOW/HIDE in
    install.conf (which bumps staterev "config", refreshing sidebar.sections)."""
    key = params.get("key", "")
    mode = params.get("mode", "auto")
    if key not in _togglable():
        raise RpcError("EINVAL", f"section {key!r} is core / not togglable")
    if mode not in ("auto", "show", "hide"):
        raise RpcError("EINVAL", f"bad mode {mode!r} (auto|show|hide)")
    tok = _tok(key)
    install_conf.set_value(f"FORCE_SHOW_{tok}", "1" if mode == "show" else "0")
    install_conf.set_value(f"FORCE_HIDE_{tok}", "1" if mode == "hide" else "0")
    return {"ok": True, "key": key, "mode": mode}
