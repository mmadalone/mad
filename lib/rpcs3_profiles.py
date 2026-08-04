"""Resolve an RPCS3 (PS3) docked/handheld input profile stem from policy + the per-game store,
plus the RESTING-TARGET ladder (which input config the launching game will actually read).

RPCS3's native profiles live as ``~/.config/rpcs3/input_configs/global/<stem>.yml`` — each a
FULL 7-player input config (``Player N Input``: Handler/Device/Config/Buddy Device) AUTHORED in
RPCS3's own desktop UI (Configuration → Gamepads, save the config file under a new name); MAD
only PICKS one per context. The picks live in ``[backends.rpcs3].profile_docked`` /
``.profile_handheld`` (global) and in the per-game store entry's sparse
``"profiles": {"docked": ..., "handheld": ...}`` dict
(``~/Emulation/storage/rpcs3/pergame-input.json``, see lib/madsrv/rpcs3_pergame_input_cmds).

Resolution at launch is per-context: per-game -> global -> None. ``None`` = leave the resting
``Player N Input`` layout untouched (today's behaviour). HANDHELD NEVER INHERITS DOCKED — an
unset handheld chain resolves to None, never to the docked profile (the codebase-wide context
convention; a docked DS4 layout silently applied to the Deck pad would mis-bind).

CRITICAL: the chosen profile is applied by COPYING its usable ``Player N Input`` blocks into
the resting target transiently at launch (rpcs3_cfg.apply_profile_players). RPCS3's
``--input-config`` CLI is NEVER used — it hard-requires ``--no-gui`` and makes RPCS3 load the
profile FILE directly, bypassing the file the launch binder seats devices into.

THE TARGET LADDER (``resting_target``) mirrors what RPCS3's own pad_thread::Init resolves at
every game boot (verified against master source, 2026-08-04):
  1. ``input_configs/<SERIAL>/Default.yml`` if that file exists (a per-title "custom gamepad
     configuration" created from RPCS3's game-list right-click; detected purely by presence)
  2. ``input_configs/global/<Active Configurations[<SERIAL>] or [global]>.yml`` if it exists
     (the pointer file ``active_input_configurations.yml``)
  3. ``input_configs/global/Default.yml``
``active_profiles.yml`` (the pre-2023 pointer, renamed by RPCS3 PR #14579) is NEVER read —
RPCS3 master neither reads nor migrates it, so honoring it would diverge from the emulator.

Leaf module: imports only lib.handheld_input (+ optional PyYAML for the pointer file), so the
launch hot path stays cheap.
"""
from __future__ import annotations

from pathlib import Path

try:
    import yaml
except ImportError:                    # PyYAML missing → pointer file unreadable → Default
    yaml = None

from . import handheld_input

_DEFAULT_YML = "~/.config/rpcs3/input_configs/global/Default.yml"
_ACTIVE_FILE = "active_input_configurations.yml"
_ACTIVE_KEY = "Active Configurations"


def valid_stem(s) -> bool:
    """A safe profile stem: non-empty string, no path separators, no leading dot."""
    if not isinstance(s, str):
        return False
    s = s.strip()
    if not s or s.startswith("."):
        return False
    return "/" not in s and "\\" not in s and ".." not in s


def config_file(rpcs3_cfg: dict) -> Path:
    """The backend's global ``Default.yml`` path (``[backends.rpcs3].config_file``)."""
    cfg_file = _DEFAULT_YML
    if isinstance(rpcs3_cfg, dict):
        v = rpcs3_cfg.get("config_file")
        if isinstance(v, str) and v.strip():
            cfg_file = v.strip()
    return Path(cfg_file).expanduser()


def profiles_dir(rpcs3_cfg: dict) -> Path:
    """``input_configs/global`` — where the pickable ``<stem>.yml`` profiles live, derived
    from the backend's ``config_file`` (its parent), so a portable tree resolves too."""
    return config_file(rpcs3_cfg).parent


def input_configs_dir(rpcs3_cfg: dict) -> Path:
    """``input_configs`` — holds ``global/``, the per-title ``<SERIAL>/`` dirs and the
    active-configuration pointer file."""
    return profiles_dir(rpcs3_cfg).parent


def list_stems(profiles: Path) -> list[str]:
    """Sorted stems of ``<dir>/*.yml`` (the pickable profiles); ``[]`` on any I/O error.
    The ``valid_stem`` filter is LOAD-BEARING: pathlib's glob DOES match dotfiles (unlike the
    glob module), so it is what keeps ``.mad-input-overrides.yml`` out of the picker."""
    try:
        return sorted(p.stem for p in Path(profiles).glob("*.yml")
                      if p.is_file() and valid_stem(p.stem))
    except OSError:
        return []


def _clean(v) -> str | None:
    """A husk-tolerant stem read: non-string / blank / path-y values degrade to None."""
    if not isinstance(v, str):
        return None
    v = v.strip()
    return v if v and valid_stem(v) else None


def _active_map(rpcs3_cfg: dict) -> dict:
    """The pointer file's ``Active Configurations`` mapping, or ``{}`` on ANY failure
    (missing file, corrupt YAML, PyYAML absent, non-dict payload). Reads ONLY
    ``active_input_configurations.yml`` — never the legacy ``active_profiles.yml``."""
    if yaml is None:
        return {}
    try:
        data = yaml.safe_load(
            (input_configs_dir(rpcs3_cfg) / _ACTIVE_FILE).read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    m = data.get(_ACTIVE_KEY)
    return m if isinstance(m, dict) else {}


def active_global(rpcs3_cfg: dict) -> str:
    """The active GLOBAL config stem from the pointer file, defaulting to ``"Default"``
    (RPCS3's own fallback). This is the resting layout — the picker excludes it."""
    return _clean(_active_map(rpcs3_cfg).get("global")) or "Default"


def resting_target(rpcs3_cfg: dict, serial: str | None) -> Path:
    """The input config the launching game will actually read (RPCS3's own ladder — see the
    module docstring). ``serial`` is pre-resolved by the caller (rpcs3_games.path_to_serial);
    None/unresolvable skips the per-title rungs. Never raises: worst case is the global
    ``Default.yml`` (== the old hardcoded target)."""
    root = input_configs_dir(rpcs3_cfg)
    gdir = profiles_dir(rpcs3_cfg)
    ser = _clean(serial)
    try:
        if ser:
            per = root / ser / "Default.yml"
            if per.is_file():
                return per
        amap = _active_map(rpcs3_cfg)
        stem = (_clean(amap.get(ser)) if ser else None) or _clean(amap.get("global"))
        if stem:
            p = gdir / f"{stem}.yml"
            if p.is_file():
                return p
    except OSError:
        pass
    return config_file(rpcs3_cfg)


def global_profile(rpcs3_cfg: dict, context) -> str | None:
    """The global ``profile_docked`` / ``profile_handheld`` stem for ``context``, or None."""
    if not isinstance(rpcs3_cfg, dict):
        return None
    ctx = handheld_input.normalize(context)
    return _clean(rpcs3_cfg.get("profile_handheld" if ctx == "handheld" else "profile_docked"))


def pergame_profile(entry, context) -> str | None:
    """The per-game ``profiles[context]`` stem from a store entry, or None (husk-tolerant)."""
    if not isinstance(entry, dict):
        return None
    profiles = entry.get("profiles")
    if not isinstance(profiles, dict):
        return None
    return _clean(profiles.get(handheld_input.normalize(context)))


def resolve(entry, rpcs3_cfg: dict, context) -> str | None:
    """The stem to apply for this launch: per-game -> global -> None. No cross-context
    fallback (see the module docstring: handheld never inherits docked)."""
    return pergame_profile(entry, context) or global_profile(rpcs3_cfg, context)


def profile_path(profiles: Path, stem: str) -> Path | None:
    """``<dir>/<stem>.yml`` if the stem is safe and the file exists, else None."""
    if not valid_stem(stem):
        return None
    try:
        p = Path(profiles) / f"{stem.strip()}.yml"
        return p if p.is_file() else None
    except OSError:
        return None
