"""Launch-time controller binding for ES-DE standalone emulators (Switch:
Ryujinx + Eden; plus PCSX2 — the Standalones migration adds one emulator at a
time via `_write`/`_target`/`_snapshot` branches + `pads_cmds._EMUS`).

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
import os
import re
import sys
from pathlib import Path

from . import eden_cfg, fsutil, inifile, mad_paths, pcsx2_cfg, rpcs3_cfg, xemu_cfg
from .madsrv import pads_cmds, ryujinx_cfg, ryujinx_json

_RYUJINX_GLOBAL = Path.home() / ".config/Ryujinx/Config.json"
_RYUJINX_GAMES = Path.home() / ".config/Ryujinx/games"
_EDEN_INI = Path.home() / ".config/eden/qt-config.ini"
_PCSX2_INI = Path.home() / ".config/PCSX2/inis/PCSX2.ini"
_XEMU_TOML = Path.home() / ".var/app/app.xemu.xemu/data/xemu/xemu/xemu.toml"
_RPCS3_YML = Path.home() / ".config/rpcs3/input_configs/global/Default.yml"
_PLAYER_RE = re.compile(r"Player \d+ Input$")
_TITLEID_RE = re.compile(r"\[([0-9A-Fa-f]{16})\]")
_PLAYERS = {"ryujinx": 8, "eden": 8, "pcsx2": 8, "xemu": 4, "rpcs3": 7}   # managed slots (matches pads_cmds._EMUS)
# TRANSIENT emulators snapshot their input before binding and restore it on exit.
# CRITERION (the default for EVERY writer-backed standalone): the emulator is ALSO
# launched via the Steam UI on the go — Steam Input ON, so it sees the virtual Deck
# pad (28de:11ff), different from the RAW pads ES-DE sees (Steam Input OFF) — while
# sharing ONE config file. So an ES-DE bind must revert on exit, leaving the
# Steam-UI-compatible resting config. The user runs Switch AND PS2 (and others) this
# way. (RetroArch does the same via per-game reservations stripped by the game-end
# cleanup hook; OpenBOR self-reads a whitelist so has no config to revert.)
_TRANSIENT = {"ryujinx", "eden", "pcsx2", "xemu", "rpcs3"}
# PCSX2's "input" = its [PadN] slot sections PLUS the [Pad] control section (which
# holds MultitapPort1/2 — the writer toggles those for 3+ players, so they must revert).
_PCSX2_SECTIONS = ("Pad",) + tuple(f"Pad{k}" for k in range(1, _PLAYERS["pcsx2"] + 1))
_SIDECAR_SUFFIX = ".mad-restore"
_LOG_FILE = mad_paths.storage("controller-router", "router.log")


# MAD_DEBUG=1 raises launch-binder verbosity (deeper _resolve_pads detail) without
# editing code; default off = zero added per-launch spam. Also flips the router logger
# to DEBUG (see controller-router.py _setup_logging).
_DEBUG = os.environ.get("MAD_DEBUG") == "1"


def _log(msg: str) -> None:
    line = f"mad-switch: {msg}"
    print(line, file=sys.stderr, flush=True)
    try:                                  # persist (the wrapper's stderr is lost in Game Mode)
        _LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with _LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _dbg(msg: str) -> None:
    """Verbose line — emitted only when MAD_DEBUG=1."""
    if _DEBUG:
        _log(msg)


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
    if emu == "pcsx2":
        return _PCSX2_INI
    if emu == "xemu":
        return _XEMU_TOML
    if emu == "rpcs3":
        return _RPCS3_YML
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
    runs in the launch session so SDL indices match the emulator's.

    HANDHELD FALLBACK: the Deck's built-in pad (the emulator's `handheld_class`) is
    bound ONLY when no external pad is present — so docked play uses the external
    pad(s), and ES-DE on the go falls back to the Deck for Player 1."""
    real = pads_cmds._real_pads()
    pads = pads_cmds._supported(emu, real)
    ordered = pads_cmds._ordered(emu, pads, real)
    hh = pads_cmds._handheld_class(emu)
    external = [d for d in ordered if d.vidpid != hh] if hh else ordered
    chosen = external if external else ordered      # Deck only when nothing else
    _dbg(f"{emu}: supported={[d.vidpid for d in pads]} ordered={[d.vidpid for d in ordered]} "
         f"handheld_class={hh!r}")
    if hh and not external:
        _log(f"{emu}: no external pad -> handheld fallback to Deck ({hh})")
    elif hh:
        _log(f"{emu}: external pad(s) present -> using them (Deck fallback skipped)")
    return chosen[: _PLAYERS.get(emu, 2)]


def _snapshot(emu: str, target: Path):
    """The input portion to restore later (input only — never settings), for the
    TRANSIENT emulators."""
    if emu == "ryujinx":
        return ryujinx_json.load(target).get("input_config", [])
    if emu == "rpcs3":   # RPCS3 owns the `Player N Input` blocks (YAML doc).
        data = rpcs3_cfg.yaml.safe_load(target.read_text(encoding="utf-8")) or {}
        return {k: v for k, v in data.items() if _PLAYER_RE.match(k)}
    text = target.read_text(encoding="utf-8", errors="replace")
    if emu == "pcsx2":   # PCSX2 owns [Pad] (multitap) + the [PadN] sections.
        # Record absent sections as None so restore can DELETE the ones the bind adds
        # (the writer always creates [Pad] + [Pad1..8]); else multitap/phantom pads
        # would drift into a later Steam-UI launch. None round-trips via the sidecar JSON.
        return {n: inifile.section_body(text, n) for n in _PCSX2_SECTIONS}
    if emu == "xemu":    # xemu owns the [input.bindings] section.
        return inifile.section_body(text, "input.bindings") or ""
    return inifile.section_body(text, "Controls") or ""


def _write(emu: str, target: Path, pads):
    """Write the resolved pads to the emulator's INPUT config (input only — button
    maps + settings untouched) and RETURN the writer's summary dict (what was actually
    written — slots/GUIDs/device strings/multitap flags) so bind() can log it. One
    branch per emulator; add an entry here plus `pads_cmds._EMUS` to onboard a new one."""
    if emu == "ryujinx":
        return ryujinx_cfg.assign_devices(pads, config_path=target)
    if emu == "pcsx2":
        return pcsx2_cfg.assign_devices(pads, ini_path=str(target), manage=_PLAYERS["pcsx2"])
    if emu == "xemu":
        return xemu_cfg.assign_devices(pads, config_path=str(target), manage=_PLAYERS["xemu"])
    if emu == "rpcs3":
        return rpcs3_cfg.assign_devices(pads, config_path=str(target), manage=_PLAYERS["rpcs3"])
    return eden_cfg.assign_devices(pads, ini_path=str(target), manage=_PLAYERS["eden"])


def bind(emu: str, rom: str) -> None:
    """Snapshot the input portion (once), then write the connected pads to the
    target config (input only — button maps + settings untouched)."""
    try:
        _log(f"--- bind: emu={emu} rom={Path(rom).name!r} ---")
        if pads_cmds._hands_off(emu):
            _log(f"{emu}: hands-off is set — leaving its own controller config untouched")
            return
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
        if emu in _TRANSIENT:    # snapshot for the on-exit restore (Switch dual-context)
            side = _sidecar(target)
            if not side.exists():
                side.write_text(json.dumps({"emu": emu, "input": _snapshot(emu, target)}),
                                encoding="utf-8")
        res = _write(emu, target, pads)
        # Log WHAT was written (slots/ports/GUIDs/device strings/multitap flags) — the
        # exact data needed to diagnose a bad bind from router.log with no display.
        _log(f"{emu}: bound {len(pads)} pad(s) -> {target.name} :: {res}")
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
        elif emu == "pcsx2":
            text = target.read_text(encoding="utf-8", errors="replace")
            for name, body in (snap or {}).items():
                # body is None ⇒ the section didn't exist pre-bind ⇒ remove the one the
                # bind added (multitap [Pad], extra [PadN]); else re-apply the original.
                text = (inifile.remove_section(text, name) if body is None
                        else inifile.set_section(text, name, body))
            fsutil.atomic_write(target, text)
        elif emu == "xemu":
            text = target.read_text(encoding="utf-8", errors="replace")
            fsutil.atomic_write(target, inifile.set_section(text, "input.bindings", snap))
        elif emu == "rpcs3":
            data = rpcs3_cfg.yaml.safe_load(target.read_text(encoding="utf-8")) or {}
            snap = snap or {}
            for k in [k for k in data if _PLAYER_RE.match(k) and k not in snap]:
                del data[k]                       # drop a Player block the bind added
            for k, v in snap.items():
                data[k] = v                       # restore the original blocks
            fsutil.atomic_write_text(target, rpcs3_cfg.yaml.safe_dump(
                data, sort_keys=False, default_flow_style=False, allow_unicode=True))
        side.unlink()
        _log(f"{emu}: restored input on {target.name}")
    except Exception as e:
        _log(f"restore failed on {target} ({e!r})")


def _known_configs():
    # The TRANSIENT emulators' configs — the ones restore_all may need to revert.
    yield _RYUJINX_GLOBAL
    yield _EDEN_INI
    yield _PCSX2_INI
    yield _XEMU_TOML
    yield _RPCS3_YML
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
