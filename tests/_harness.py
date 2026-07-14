"""
Run a backend ``assign()`` against a scenario in an isolated temp dir and return
the exact text it wrote to the config it owns. Same code drives golden CAPTURE
(current code) and golden COMPARE (after the pad_assign refactor) — that's the
no-behaviour-change proof.
"""
from __future__ import annotations

import importlib
import logging
import shutil
from pathlib import Path

from tests import scenarios
from tests._fakes import patch_sdl

FIX = Path(__file__).parent / "fixtures"
BACKENDS = ["pcsx2", "xemu", "eden", "rpcs3"]


def _logger():
    lg = logging.getLogger("padtest")
    if not lg.handlers:
        lg.addHandler(logging.NullHandler())
    lg.setLevel(logging.CRITICAL)
    return lg


def _setup_pcsx2(tmp: Path):
    cfgf = tmp / "PCSX2.ini"
    shutil.copy2(FIX / "pcsx2" / "PCSX2.ini", cfgf)
    cfg = {"config_file": str(cfgf), "manage_pads": scenarios.MANAGE,
           "pad_classes": scenarios.PAD_CLASSES, "handheld_class": scenarios.HANDHELD}
    return cfg, cfgf


def _setup_xemu(tmp: Path):
    cfgf = tmp / "xemu.toml"
    shutil.copy2(FIX / "xemu" / "xemu.toml", cfgf)
    cfg = {"config_file": str(cfgf), "manage_ports": scenarios.MANAGE,
           "pad_classes": scenarios.PAD_CLASSES, "handheld_class": scenarios.HANDHELD}
    return cfg, cfgf


def _setup_eden(tmp: Path):
    cfgf = tmp / "qt-config.ini"
    tmpl = tmp / "template.ini"
    shutil.copy2(FIX / "eden" / "qt-config.ini", cfgf)
    shutil.copy2(FIX / "eden" / "template.ini", tmpl)
    cfg = {"config_file": str(cfgf), "template_profile": str(tmpl),
           "manage_players": scenarios.MANAGE,
           "pad_classes": scenarios.PAD_CLASSES, "handheld_class": scenarios.HANDHELD}
    return cfg, cfgf


def _setup_rpcs3(tmp: Path):
    cfgf = tmp / "Default.yml"
    shutil.copy2(FIX / "rpcs3" / "Default.yml", cfgf)
    cfg = {"config_file": str(cfgf), "manage_players": scenarios.MANAGE,
           "pad_classes": scenarios.PAD_CLASSES, "handheld_class": scenarios.HANDHELD,
           "name_overrides": {}}
    return cfg, cfgf


_SETUP = {"pcsx2": _setup_pcsx2, "xemu": _setup_xemu,
          "eden": _setup_eden, "rpcs3": _setup_rpcs3}


def run(backend: str, classes, pins_by_port, tmp: Path) -> str:
    """Run ``<backend>_cfg.assign`` against a scenario; return the written text."""
    sdl, devs, pins = scenarios.build(classes, pins_by_port)
    cfg, target = _SETUP[backend](Path(tmp))
    with patch_sdl(sdl):
        mod = importlib.import_module(f"lib.{backend}_cfg")
        # rpcs3 assign() also reads a HOME-based per-user override sidecar
        # (~/.config/rpcs3/.../.mad-input-overrides.yml). Isolate it to an absent
        # path under tmp so the golden reflects the code's hermetic DEFAULT mapping,
        # not whatever the host Deck's live overrides happen to contain (else the
        # golden is machine-specific and fails in CI / on any other Deck). Mirrors
        # the save/swap/restore already used in test_rpcs3_input.py.
        if backend == "rpcs3":
            saved = mod._OVERRIDES_FILE
            mod._OVERRIDES_FILE = Path(tmp) / ".mad-input-overrides.yml"  # absent -> no remaps
            try:
                mod.assign(cfg, _logger(), devs=devs, pins=pins)
            finally:
                mod._OVERRIDES_FILE = saved
        else:
            mod.assign(cfg, _logger(), devs=devs, pins=pins)
    return target.read_text(encoding="utf-8")
