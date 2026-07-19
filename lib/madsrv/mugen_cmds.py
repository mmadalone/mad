"""mugen.* methods - the MUGEN / Ikemen GO per-game config tree (MAD).

Every MUGEN game is a self-contained Ikemen GO install under ~/ROMs/mugen/<folder>/,
launched by a ~/ROMs/mugen/<name>.mugen script whose exec line names the folder AND
the launch mode (ikemen | native). The engine keeps ONE config per game at
<folder>/save/config.ini (a sectioned, comment-heavy INI). So this is a GAME-FIRST
flow (pick a game once, edit that game's config.ini), the same settings_pergame_menu
pattern as Lindbergh / the Switch emus.

The .mugen file's basename is the game IDENTITY (matches the ES-DE ROM + media), and
is often NOT the config folder name (AvengersVsX-Men.mugen launches folder "AvX",
smfn.mugen launches "Spider-Man-FightNight"), so the folder is resolved by PARSING
the launcher's exec line - never guessed from the filename.

Only Video / Audio / Gameplay knobs the engine owns and that are safe to tune are
offered. NEVER: Motif (mugen.sh passes it via -r), FirstRun / System / WindowTitle /
GamepadMappings (engine bookkeeping), Netplay, or the [Keys_*] / [Joystick_*] input
blocks (those belong to the controller pipeline). Live-save via the shared cfgutil
engine, byte-preserving (comments + alignment kept), with a one-time .bak; refused
while a game is running (Ikemen rewrites config.ini on exit).
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

from . import cfgutil
from .rpc import RpcError, method

MUGEN_ROOT = Path.home() / "ROMs" / "mugen"
GAMELIST = Path.home() / "ES-DE" / "gamelists" / "mugen" / "gamelist.xml"
_PROC = "mugen"
_LABEL = "M.U.G.E.N"
_F = "config.ini"

# The launcher's exec line: `exec ".../mugen.sh" <mode> "<target>"` (the path to
# mugen.sh is itself quoted, so a closing quote sits between it and the mode).
_EXEC_RE = re.compile(r'mugen\.sh"?\s+(ikemen|native|win)\s+"?([^"\n]+?)"?\s*$')


# -- game list + per-game config path ------------------------------------------
def _parse_launcher(mugen_file: Path):
    """(mode, target) from a .mugen launcher's exec line, or None."""
    try:
        for line in mugen_file.read_text().splitlines():
            m = _EXEC_RE.search(line)
            if m:
                return m.group(1), m.group(2).strip()
    except OSError:
        return None
    return None


def _config_ini(titleid: str) -> Path:
    """The save/config.ini for a game, resolved from its .mugen launcher. Path-traversal
    guarded to ~/ROMs/mugen. The file may not exist yet (a game not launched under the
    current engine); callers handle that. Raises EINVAL for an unknown/unsafe titleid."""
    if not titleid or "/" in titleid or ".." in titleid:
        raise RpcError("EINVAL", f"bad titleid {titleid!r}")
    parsed = _parse_launcher(MUGEN_ROOT / f"{titleid}.mugen")
    if not parsed:
        raise RpcError("EINVAL", f"no MUGEN launcher for {titleid!r}")
    mode, target = parsed
    # ikemen: target IS the game folder. native: target is <folder>/<binary>.
    folder = (MUGEN_ROOT / target).parent if mode == "native" else (MUGEN_ROOT / target)
    ini = (folder / "save" / "config.ini").resolve()
    try:
        ini.relative_to(MUGEN_ROOT.resolve())
    except ValueError:
        raise RpcError("EINVAL", f"config path escapes ROM dir for {titleid!r}")
    return ini


def _game_names() -> dict:
    """titleid (ES-DE stem) -> display name, from the mugen gamelist."""
    out: dict = {}
    try:
        for g in ET.parse(GAMELIST).getroot().findall("game"):
            stem = Path((g.findtext("path") or "").strip()).stem
            name = (g.findtext("name") or "").strip()
            if stem and name:
                out[stem] = name
    except Exception:
        pass
    return out


def _games() -> list:
    """Every .mugen game with a resolvable launcher, in display order. `stem` == titleid
    (the .mugen basename), which is what the media browser resolves art/video from."""
    names = _game_names()
    out = []
    if MUGEN_ROOT.is_dir():
        for f in sorted(MUGEN_ROOT.glob("*.mugen")):
            titleid = f.stem
            if not _parse_launcher(f):
                continue
            try:
                has_cfg = _config_ini(titleid).is_file()
            except RpcError:
                has_cfg = False
            out.append({"titleid": titleid, "name": names.get(titleid, titleid),
                        "stem": titleid,
                        "summary": "Per-game config" if has_cfg
                        else "Launch the game once to create its config"})
    out.sort(key=lambda g: g["name"].lower())
    return out


@method("mugen.games", slow=True)
def _games_cmd(params):
    # system = the ES-DE system whose media the browser resolves.
    return {"games": _games(), "system": "mugen"}


# -- per-game settings schema (Video / Audio / Gameplay) -----------------------
def _bool(key, label, section, name=None):
    it = {"key": key, "label": label, "file": _F, "section": section,
          "type": "bool", "bool_true": "1", "bool_false": "0"}
    if name:
        it["name"] = name
    return it


def _enum(key, label, section, stored, display=None, name=None):
    it = {"key": key, "label": label, "file": _F, "section": section, "type": "enum",
          "write_mode": "option", "options_stored": stored,
          "options_display": display or stored}
    if name:
        it["name"] = name
    return it


def _int(key, label, section, lo, hi, step=1, name=None):
    it = {"key": key, "label": label, "file": _F, "section": section,
          "type": "int", "min": lo, "max": hi, "step": step}
    if name:
        it["name"] = name
    return it


GROUPS = [
    {"title": "Video", "note": "", "items": [
        _enum("RenderMode", "Renderer", "Video",
              ["OpenGL 3.3", "Vulkan 1.3"], ["OpenGL 3.3", "Vulkan 1.3 (faster)"]),
        _enum("GameWidth", "Render width", "Video",
              ["640", "1280", "1920", "2560", "3840"]),
        _enum("GameHeight", "Render height", "Video",
              ["480", "720", "1080", "1440", "2160"]),
        _bool("Fullscreen", "Fullscreen", "Video"),
        _bool("Borderless", "Borderless", "Video"),
        _bool("VSync", "VSync", "Video"),
        _enum("MSAA", "Anti-aliasing (MSAA)", "Video",
              ["0", "2", "4", "8", "16", "32"],
              ["Off", "2x", "4x", "8x", "16x", "32x"]),
        _int("Framerate", "Frame rate cap", "Video", 30, 240, 10),
        _bool("KeepAspect", "Keep aspect ratio", "Video"),
    ]},
    {"title": "Audio", "note": "", "items": [
        _int("MasterVolume", "Master volume", "Sound", 0, 100, 5),
        _int("BGMVolume", "Music volume", "Sound", 0, 100, 5),
        _int("WavVolume", "Effects volume", "Sound", 0, 100, 5),
        _enum("SampleRate", "Sample rate", "Sound",
              ["22050", "44100", "48000"], ["22050 Hz", "44100 Hz", "48000 Hz"]),
        _bool("StereoEffects", "Stereo effects", "Sound"),
        _bool("AudioDucking", "Audio ducking", "Sound"),
    ]},
    {"title": "Gameplay", "note": "", "items": [
        _int("Difficulty", "Difficulty (1-8)", "Options", 1, 8, 1),
        _int("Life", "Life %", "Options", 30, 300, 10),
        _int("Time", "Round time (seconds)", "Options", 10, 99, 1),
        _int("MatchWins", "Rounds to win", "Options", 1, 5, 1, name="Match.Wins"),
        _int("Credits", "Credits", "Options", 0, 99, 1),
        _bool("AutoGuard", "Auto guard", "Options"),
        _bool("QuickContinue", "Quick continue", "Options"),
        _int("Players", "Max players", "Config", 2, 8, 1),
        _bool("ZoomActive", "Stage zoom", "Config"),
    ]},
]


@method("mugen.get", slow=True)
def _get(params):
    titleid = params.get("titleid", "")
    if not titleid:
        raise RpcError("EINVAL", "mugen.get needs a titleid")
    return cfgutil.do_get(GROUPS, _config_ini(titleid), cfgutil.ini_read,
                          proc=_PROC, label=_LABEL)


@method("mugen.set", slow=True)
def _set(params):
    titleid = params.get("titleid", "")
    if not titleid:
        raise RpcError("EINVAL", "mugen.set needs a titleid")
    res = cfgutil.do_set(GROUPS, params, _config_ini(titleid), cfgutil.ini_read,
                         cfgutil.ini_replace, proc=_PROC, label=_LABEL)
    from .. import staterev
    staterev.bump("config")
    return res
