"""Lutris game facts - the single owner of every Lutris-side lookup MAD makes.

A non-Steam shortcut that launches through Lutris (lutris:rungameid/<id>) keeps its
game data in a LUTRIS wine prefix, not in Steam's compatdata - so the steam backup
tile resolves those games here. Everything is DYNAMIC and read-only:

- pga.db (sqlite, Lutris' game table) maps the rungameid to the game's slug and its
  per-game config name;
- the per-game YAML (data/lutris/games/<configpath>.yml) carries the wine `prefix:`,
  `exe:` and `working_dir:` - the prefix is where saves live (drive_c/users/<user>/...),
  the exe/working_dir parent is the game folder for repack installs outside the prefix.

Verified layout on this Deck (2026-07-30): flatpak Lutris at
~/.var/app/net.lutris.Lutris/data/lutris (pga.db + games/*.yml); prefixes under
~/Games/<slug> and ~/games/Prefix; SEVERAL GAMES CAN SHARE ONE PREFIX (Transformers
FoC/Devastation/Ultimate Spider-Man/Deadpool all use ~/Games/transformers-fall-of-
cybertron), so shared_prefix_count() lets the UI say so - backing up or restoring a
shared prefix touches every game in it.

YAML parsing: yaml.safe_load when PyYAML is importable (it is, on SteamOS), else a
minimal `game:`-block scalar parser (enough for prefix/exe/working_dir; a wrapped long
path would be truncated by the fallback and then fail the exists() check - safe).

Path providers are plain module functions so tests can monkeypatch them, same
convention as lib/steam_shortcuts.py.
"""
from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path


def home() -> Path:
    return Path.home()


def data_dir() -> Path:
    """The Lutris data dir: flatpak first (this Deck), then a native install."""
    flatpak = home() / ".var/app/net.lutris.Lutris/data/lutris"
    if flatpak.is_dir():
        return flatpak
    return home() / ".local/share/lutris"


def pga_db() -> Path:
    return data_dir() / "pga.db"


def _game_row(game_id: int):
    """One pga.db row: {name, slug, runner, directory, configpath, installed} or None.
    Read-only URI so a running Lutris can't be disturbed; any sqlite trouble = None."""
    db = pga_db()
    if not db.is_file():
        return None
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
        try:
            cur = con.execute(
                "SELECT name, slug, runner, directory, configpath, installed "
                "FROM games WHERE id = ?", (int(game_id),))
            row = cur.fetchone()
        finally:
            con.close()
    except (sqlite3.Error, OSError, ValueError):
        return None
    if row is None:
        return None
    return {"name": row[0] or "", "slug": row[1] or "", "runner": row[2] or "",
            "directory": row[3] or "", "configpath": row[4] or "",
            "installed": bool(row[5])}


def _parse_game_block(text: str) -> dict:
    """prefix/exe/working_dir from a Lutris per-game YAML. PyYAML when available;
    else a minimal parser over the top-level `game:` block's scalar lines."""
    try:
        import yaml
        doc = yaml.safe_load(text) or {}
        game = doc.get("game") if isinstance(doc, dict) else None
        return dict(game) if isinstance(game, dict) else {}
    except ImportError:
        pass
    except Exception:
        return {}
    out: dict = {}
    in_game = False
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if indent == 0:
            in_game = line.rstrip() == "game:"
            continue
        if in_game and ":" in line:
            key, _, val = line.strip().partition(":")
            if key in ("prefix", "exe", "working_dir"):
                out[key] = val.strip().strip("'\"")
    return out


def config_path(game_id: int):
    """The game's per-game YAML config file (a real Path), or None. Backed up as the
    'Lutris config' group: it names the wine prefix/exe, so a disaster-recovery restore
    has everything needed to re-create the game entry in Lutris."""
    row = _game_row(game_id)
    if not row or not row["configpath"]:
        return None
    p = data_dir() / "games" / f"{row['configpath']}.yml"
    return p if p.is_file() else None


def installed() -> bool:
    """Whether Lutris itself is present. Checks the flatpak INSTALL dirs too, not just
    the app data dirs - flatpak only creates ~/.var/app/<id> on the app's first RUN,
    so right after "flatpak install" the data dir does not exist yet and the restore
    flow's install prompt would loop forever."""
    return (home() / ".var/app/net.lutris.Lutris").is_dir() or \
        (home() / ".local/share/lutris").is_dir() or \
        (home() / ".local/share/flatpak/app/net.lutris.Lutris").is_dir() or \
        os.path.isdir("/var/lib/flatpak/app/net.lutris.Lutris")


def game_config(game_id: int) -> dict:
    """{prefix, exe, working_dir} (whatever the config carries) for one Lutris game,
    or {} - joins pga.db's configpath to data/lutris/games/<configpath>.yml."""
    row = _game_row(game_id)
    if not row or not row["configpath"]:
        return {}
    cfg = data_dir() / "games" / f"{row['configpath']}.yml"
    try:
        return _parse_game_block(cfg.read_text(errors="replace"))
    except OSError:
        return {}


def _accepted_dir(cand: str):
    """A game-data dir MAD may back up: exists, realpaths INSIDE $HOME (not $HOME
    itself), and not under a root another backup category owns. Same containment as
    steam_shortcuts.game_dir - the deny list is imported from there so the two can
    never drift."""
    from . import steam_shortcuts as ss
    if not cand:
        return None
    rp = os.path.realpath(os.path.expanduser(str(cand)))
    if not os.path.isdir(rp):
        return None
    h = os.path.realpath(str(home()))
    if rp == h or not rp.startswith(h + os.sep):
        return None
    for d in ss._DENY_ROOTS:
        droot = os.path.join(h, d)
        if rp == droot or rp.startswith(droot + os.sep):
            return None
    return Path(rp)


def prefix_for(game_id: int):
    """The game's LIVE wine prefix as a realpath'd Path, or None. Must look like a
    prefix (a drive_c inside) so a stray path in a hand-edited config never lets a
    whole home subtree ride the backup as 'the prefix'."""
    p = _accepted_dir(game_config(game_id).get("prefix"))
    if p is None or not (p / "drive_c").is_dir():
        return None
    return p


def game_dir_for(game_id: int):
    """The game's install folder (working_dir, else the exe's parent), accepted under
    the same containment rules, or None. Distinct from the prefix (a repack living
    inside the prefix - e.g. via a dosdevices drive - realpaths outside $HOME or
    inside the prefix and is covered by the prefix backup instead)."""
    cfg = game_config(game_id)
    pfx = prefix_for(game_id)
    for cand in (cfg.get("working_dir"), os.path.dirname(cfg.get("exe") or "")):
        d = _accepted_dir(cand)
        if d is None:
            continue
        if pfx is not None:
            ds = str(d)
            ps = str(pfx)
            if ds == ps or ds.startswith(ps + os.sep):
                continue          # inside the prefix: the prefix row already covers it
        return d
    return None


def shared_prefix_count(game_id: int) -> int:
    """How many OTHER installed Lutris games use the same prefix (0 = exclusive).
    Lets the asset row say 'shared with N other games' - restoring a shared prefix
    touches all of them."""
    pfx = prefix_for(game_id)
    db = pga_db()
    if pfx is None or not db.is_file():
        return 0
    target = str(pfx)
    n = 0
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
        try:
            for (gid,) in con.execute(
                    "SELECT id FROM games WHERE installed = 1 AND id != ?",
                    (int(game_id),)):
                other = _accepted_dir(game_config(gid).get("prefix"))
                if other is not None and str(other) == target:
                    n += 1
        finally:
            con.close()
    except (sqlite3.Error, OSError, ValueError):
        return 0
    return n
