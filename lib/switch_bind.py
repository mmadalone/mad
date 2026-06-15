"""Launch-time controller binding for ES-DE Switch games (Ryujinx + Eden).

Flow (Steam Input OFF for ES-DE, so the emulator sees raw pads):
  • `mad-switch-launch.py <emu> <rom> -- <cmd>` calls bind(), then execs the
    emulator (becoming it — so nothing matching the quit-combo's `pkill -f
    'Ryujinx|Eden|…'` lingers as a separate wrapper process).
  • bind() rewrites ONLY the input portion of the emulator's config (Ryujinx
    per-game by titleid, else global; Eden global) to the connected pads in the
    user's stored priority order, and writes a sidecar `<config>.mad-restore`
    recording {emu, snapshot-of-the-input}.
  • An ES-DE game-end hook calls restore_all(), which finds every sidecar and
    re-applies its snapshot — reverting the input to the on-the-go (Steam-direct)
    default while KEEPING every SETTING (60 FPS mod, graphics, res scale) the
    emulator wrote. The hook fires whether the game exited normally or was
    quit-combo-killed, so the restore is robust.

The SDL slot index in the Ryujinx id is computed in the launch session, so it
matches what the emulator enumerates moments later. Everything is best-effort: a
failure here must never block the game launch.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from . import eden_cfg, fsutil, inifile
from .madsrv import pads_cmds, ryujinx_cfg, ryujinx_json

_RYUJINX_GLOBAL = Path.home() / ".config/Ryujinx/Config.json"
_RYUJINX_GAMES = Path.home() / ".config/Ryujinx/games"
_EDEN_INI = Path.home() / ".config/eden/qt-config.ini"
_TITLEID_RE = re.compile(r"\[([0-9A-Fa-f]{16})\]")
_PLAYERS = {"ryujinx": 2, "eden": 2}      # managed slots (matches pads_cmds._EMUS)
_SIDECAR_SUFFIX = ".mad-restore"
_LOG_FILE = Path.home() / "Emulation/storage/controller-router/router.log"


def _log(msg: str) -> None:
    line = f"mad-switch: {msg}"
    print(line, file=sys.stderr, flush=True)
    try:                                  # persist (the wrapper's stderr is lost in Game Mode)
        _LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with _LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _log_sdl_view() -> None:
    """Log the full SDL enumeration as Ryujinx-format ids, so a launch-time index
    mismatch (the wrapper's view vs the emulator's) is visible in the log."""
    try:
        from .madsrv import ryujinx_cfg as _rc
        sdl = pads_cmds.sdl_devices()
        _log("sdl view: " + " | ".join(
            f"idx{d.index} {d.vidpid} '{d.name}' -> {_rc.ryujinx_id(d.index, d.guid)}"
            for d in sdl))
    except Exception as e:
        _log(f"sdl view unavailable ({e!r})")


def _titleid(rom: str) -> str | None:
    m = _TITLEID_RE.search(Path(rom).name)
    return m.group(1).lower() if m else None


def _target(emu: str, rom: str) -> Path:
    """The config file the launched game actually reads."""
    if emu == "eden":
        return _EDEN_INI
    tid = _titleid(rom)
    if tid:
        per = _RYUJINX_GAMES / tid / "Config.json"
        if per.is_file():
            return per
    return _RYUJINX_GLOBAL


def _sidecar(target: Path) -> Path:
    return target.with_name(target.name + _SIDECAR_SUFFIX)


def _resolve_pads(emu: str):
    """Top-N supported connected pads by the stored priority. Reuses pads_cmds;
    runs in the launch session so SDL indices match the emulator's."""
    pads = pads_cmds._supported(emu, pads_cmds._real_pads())
    return pads_cmds._ordered(emu, pads)[: _PLAYERS.get(emu, 2)]


def _snapshot(emu: str, target: Path):
    """The input portion to restore later (input only — never settings)."""
    if emu == "ryujinx":
        return ryujinx_json.load(target).get("input_config", [])
    text = target.read_text(encoding="utf-8", errors="replace")
    return inifile.section_body(text, "Controls") or ""


def bind(emu: str, rom: str) -> None:
    """Snapshot the input portion (once), then write the connected pads to the
    target config (input only — button maps + settings untouched)."""
    try:
        _log(f"--- bind: emu={emu} rom={Path(rom).name!r} ---")
        _log_sdl_view()
        target = _target(emu, rom)
        if not target.is_file():
            _log(f"{emu}: no config at {target}; leaving input untouched")
            return
        pads = _resolve_pads(emu)
        _log(f"{emu}: stored order={pads_cmds._stored_order(emu)} "
             f"resolved={[(d.index, d.vidpid) for d in pads]} -> {target}")
        if not pads:
            _log(f"{emu}: no connected pads; leaving input untouched")
            return
        side = _sidecar(target)
        if not side.exists():
            side.write_text(json.dumps({"emu": emu, "input": _snapshot(emu, target)}),
                            encoding="utf-8")
        if emu == "ryujinx":
            ryujinx_cfg.assign_devices(pads, config_path=target)
        else:
            eden_cfg.assign_devices(pads, ini_path=str(target), manage=_PLAYERS["eden"])
        _log(f"{emu}: bound {len(pads)} pad(s) -> {target.name}")
    except Exception as e:               # never block the launch
        _log(f"{emu}: bind failed ({e!r}); launching unchanged")


def restore_target(target: Path) -> None:
    """Re-apply the sidecar's input snapshot to `target` (the emulator-rewritten
    config), then drop the sidecar. SETTINGS the emulator wrote are kept."""
    side = _sidecar(target)
    try:
        if not (target.is_file() and side.exists()):
            return
        meta = json.loads(side.read_text(encoding="utf-8"))
        emu, snap = meta.get("emu"), meta.get("input")
        if emu == "ryujinx":
            data = ryujinx_json.load(target)        # has the emulator's settings
            data["input_config"] = snap
            ryujinx_json.write(data, target)
        elif emu == "eden":
            text = target.read_text(encoding="utf-8", errors="replace")
            fsutil.atomic_write(target, inifile.set_section(text, "Controls", snap))
        side.unlink()
        _log(f"{emu}: restored input on {target.name}")
    except Exception as e:
        _log(f"restore failed on {target} ({e!r})")


def _known_configs():
    yield _RYUJINX_GLOBAL
    yield _EDEN_INI
    try:
        yield from _RYUJINX_GAMES.glob("*/Config.json")
    except OSError:
        pass


def restore_all() -> None:
    """Restore every pending sidecar (called by the ES-DE game-end hook). Idempotent
    — a no-op when nothing is pending (normal: only one switch game ran)."""
    for cfg in _known_configs():
        if _sidecar(cfg).exists():
            restore_target(cfg)
