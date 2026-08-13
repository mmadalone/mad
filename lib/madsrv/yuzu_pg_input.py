r"""Shared Yuzu-fork (Citron/Eden) PER-GAME Input Profiles engine (citron_pg_input.* /
eden_pg_input.*).

One selector per player (Player 1..8): "Use global input configuration" or a named profile
from <input dir>/*.ini. Stored in custom/<TITLEID>.ini's [Controls] as
`player_N_profile_name`.

CRITICAL (verified vs citron-neo/emulator; shared Yuzu-fork behaviour): a per-game profile
is NOT read from input/<name>.ini at boot -- the fork BAKES the resolved bindings inline
into the per-game ini at save time and reads that snapshot. So selecting a profile here
writes `player_N_profile_name="<name>"` AND copies the profile's
`player_N_button_*`/stick/motion bindings in (each with its \default=false twin). Writing
only the name would drop that player to KEYBOARD at boot. "Use global" removes the
player's keys (absent = inherit the global config). The baked bindings keep the profile's
OWN device (guid/port), which is exactly the intended per-game device PIN that bypasses
MAD's pads->players routing (see memory switch-per-game-profile-routing).

Rendered per game by GuiMadPageEmuSettings (enum rows). Writes refuse while the fork runs.

One PgInputEngine instance per fork; constructing it registers <ns>.get/set. The input-dir
getter and the games-shim pergame_path function are read/called PER CALL, so tests
repointing the shim's _INPUT_DIR and the games module's _CUSTOM are honored. Both forks
bake through the shared eden_cfg writer (already the de-facto Yuzu-fork engine).
"""
from __future__ import annotations

import re
from pathlib import Path

from .. import eden_cfg, inifile, proc_guard, staterev
from . import cfgutil
from .rpc import RpcError, method

_PLAYERS = 8                                          # Player 1..8 = player_0..player_7
_TID_RE = re.compile(r"^[0-9A-Fa-f]{16}$")
_SPECIAL = re.compile(r"[^A-Za-z0-9_.-]")
_GLOBAL = "Use global input configuration"


def _tid(params) -> str:
    t = params.get("titleid") or ""
    if not _TID_RE.match(t):
        raise RpcError("EINVAL", f"bad game id {t!r}")
    return t


def _quote(v: str) -> str:
    return f'"{v}"' if (v == "" or _SPECIAL.search(v)) else v


def _profiles(input_dir: Path) -> list[str]:
    try:
        return sorted((p.stem for p in input_dir.glob("*.ini")), key=str.lower)
    except OSError:
        return []


def _profile_name(body: str, n: int) -> str:
    """The player's selected per-game profile name ('' = use global)."""
    raw = cfgutil.ini_read("[Controls]\n" + body, "Controls", f"player_{n}_profile_name")
    if raw is None:
        return ""
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"':
        raw = raw[1:-1]
    return raw


def _clear_player(body: str, n: int) -> str:
    """Remove every player_{n}_* line (bindings + \\default twins + profile_name) -> inherit global."""
    pref = f"player_{n}_"
    return "\n".join(ln for ln in body.splitlines() if not ln.lstrip().startswith(pref))


def _player_lines(body: str, n: int, key: str) -> list[str]:
    """The verbatim `player_{n}_{key}` line and its `\\default` twin from a [Controls] body,
    in source order ([] if the key isn't present at all). Lets a caller carry a value+twin
    pair through unchanged instead of re-deriving it via _apply_player (which always forces
    \\default=false, i.e. "explicit") or hardcoding a value."""
    pref = f"player_{n}_{key}"
    return [ln for ln in body.splitlines()
            if (ln.split("=", 1)[0].strip() if "=" in ln else "") in (pref, pref + "\\default")]


def _global_body(input_dir: Path) -> str:
    """The GLOBAL qt-config.ini's [Controls] section body, read from the file next to the
    fork's input/ profile directory (Eden: ~/.config/eden/qt-config.ini beside .../input/;
    Citron: the same shape under .../citron/ -- the SAME layout eden_settings._FILE /
    citron_settings._FILE already assume). Derived from input_dir instead of importing those
    settings modules, so this reuses the getter eden_pg_input_cmds.py / citron_pg_input_cmds.py
    already supply (and that tests already redirect) rather than threading a new path through
    those two files."""
    text = cfgutil.read_text(input_dir.parent / "qt-config.ini") or ""
    return inifile.section_body(text, "Controls") or ""


def _bake(input_dir: Path, body: str, n: int, name: str) -> str:
    """Write player_{n}_profile_name + the profile's resolved inline bindings (kept
    verbatim, so the profile's own device is pinned) into the [Controls] body, each with
    its \\default=false twin."""
    prof = input_dir / f"{name}.ini"
    if not prof.is_file():
        raise RpcError("ENOENT", f"input profile {name!r} not found")
    # Capture the player's controller TYPE lines (value + \default twin) BEFORE
    # _clear_player wipes them below, so they can be re-emitted untouched after the bake.
    # Was `ov["type"] = "0"`: _apply_player forces \default=false onto every key it is
    # given, so hardcoding "0" here both discarded whatever type the player had AND
    # stamped it "explicit", silently downgrading a non-default controller type
    # (Handheld=4, GameCube=5, dual joycon=1, ...) the user picked in the emulator's own
    # Controls dialog to Pro Controller on every per-game profile bake (audit phase-5,
    # site 4). The sibling bugs in eden_cfg.assign()/assign_devices() were fixed by simply
    # leaving "type" out of `ov` -- that trick does not work here because _clear_player
    # below removes ALL player_n_* lines first, so there is nothing left to "leave alone";
    # the pair has to be captured and put back explicitly. Prefer the per-game body's own
    # lines (this game already had a pick); else the GLOBAL config's lines for this player
    # (what it inherits today); else carry nothing -- a config with no type key anywhere
    # keeps having none, the same contract as the eden_cfg fix.
    typ = _player_lines(body, n, "type") or _player_lines(_global_body(input_dir), n, "type")
    body = _clear_player(body, n)                     # replace any existing selection cleanly
    ov = dict(eden_cfg._template_bindings(prof))      # {button_a: '"engine:sdl,..."', ...}
    ov["profile_name"] = _quote(name)
    # A per-game override player STOPS inheriting from global once profile_name is set, so
    # it must carry connected itself - else the fork boots Players 2-8 disconnected (their
    # default) and the device PIN silently does nothing. Mirrors eden_cfg.assign()/
    # assign_devices(). type is captured/re-emitted above and below instead, for the
    # identical must-not-inherit reason, without clobbering the user's own type pick.
    ov["connected"] = "true"
    body = eden_cfg._apply_player(body, n, ov)
    if typ:                                            # re-emit the captured pair verbatim
        body = "\n".join(body.splitlines() + typ)
    return body


class PgInputEngine:
    """One instance per fork; constructing it registers <ns>.get/set."""

    def __init__(self, *, ns: str, display: str, input_dir_getter, input_hint: str,
                 pergame_path_fn, proc: str):
        self.ns, self.display = ns, display
        self._input_dir, self._input_hint = input_dir_getter, input_hint
        self._pergame_path, self._proc = pergame_path_fn, proc
        self._register()

    def _running(self) -> bool:
        return proc_guard.emulator_running(self._proc)

    def _do_get(self, params):
        tid = _tid(params)
        pg_text = cfgutil.read_text(self._pergame_path(tid)) or ""
        body = inifile.section_body(pg_text, "Controls") or ""
        profiles = _profiles(self._input_dir())
        options = [_GLOBAL] + profiles
        rows = []
        for n in range(_PLAYERS):
            name = _profile_name(body, n)
            value = (profiles.index(name) + 1) if name in profiles else 0
            rows.append({"key": f"player_{n}", "label": f"Player {n + 1} profile",
                         "type": "enum", "options": options, "value": value})
        note = ("Pick a named input profile per player, or 'Use global input configuration' "
                "to route the player normally. A named profile PINS that player's device "
                "for this game (overrides pads -> players)." if profiles else
                f"No input profiles found. Create named profiles in {self.display}'s "
                f"Controls dialog (saved under {self._input_hint}) to assign them per "
                "game here.")
        return {"exists": True, "running": self._running(), "note": note,
                "groups": [{"title": "Input Profiles", "note": "", "settings": rows}]}

    def _do_set(self, params):
        if self._running():
            raise RpcError("EBUSY", f"close {self.display} first - it rewrites its config on exit.")
        tid = _tid(params)
        key = params.get("key", "")
        m = re.fullmatch(r"player_([0-7])", key)
        if not m:
            raise RpcError("EINVAL", f"{key!r} is not a player profile selector")
        n = int(m.group(1))
        profiles = _profiles(self._input_dir())
        try:
            idx = int(float(params.get("value")))
        except (TypeError, ValueError):
            raise RpcError("EINVAL", "pick a profile or 'Use global input configuration'")
        pg = self._pergame_path(tid)
        text = cfgutil.read_text(pg) or ""
        body = inifile.section_body(text, "Controls") or ""
        if idx <= 0:                                       # Use global -> clear this player
            new_body = _clear_player(body, n)
        else:
            if idx - 1 >= len(profiles):
                raise RpcError("EINVAL", "profile no longer exists")
            new_body = _bake(self._input_dir(), body, n, profiles[idx - 1])
        if inifile.section_body(text, "Controls") is not None:
            if new_body.strip():
                new_text = inifile.set_section(text, "Controls", new_body)
            else:
                # Last override removed: drop ONLY the emptied [Controls] section - the
                # per-game ini can hold other (settings) sections and the FILE is never
                # deleted. Also self-heals pre-fix junk header-only [Controls] files on
                # the next Use-global set.
                new_text = inifile.remove_section(text, "Controls")
        elif new_body:                                     # only create [Controls] if there is something to write
            new_text = (text + ("" if not text or text.endswith("\n") else "\n")
                        + "[Controls]\n" + new_body + ("\n" if not new_body.endswith("\n") else ""))
        else:                                              # Use-global on a game with no per-game ini -> no-op:
            new_text = text                                # don't create an otherwise-nonexistent empty-[Controls] file
        if new_text != text:
            pg.parent.mkdir(parents=True, exist_ok=True)
            cfgutil.ensure_bak(pg)
            cfgutil.atomic_write(pg, new_text)
            staterev.bump("config")
        return {"key": key, "value": idx if idx > 0 else 0}

    def _register(self) -> None:
        @method(f"{self.ns}.get", slow=True)
        def _get(params, _s=self):
            return _s._do_get(params)

        @method(f"{self.ns}.set", slow=True)
        def _set(params, _s=self):
            return _s._do_set(params)
