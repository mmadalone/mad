"""
RPCS3 (PS3) controller-assignment backend for the controller-router.

RPCS3's global input config (`input_configs/global/Default.yml`) selects a pad
per player by **SDL device NAME + a 1-based index**, NOT a GUID:

    Player 1 Input:
      Handler: SDL
      Device: "PS4 Controller 1"     # <SDL name> <Nth same-named device>
      Config: { ... }

The Config block is SDL-standard button mappings, so one device-agnostic
template (captured from a known-good SDL profile) is reused for every player and
only the `Device:` string changes. Two identical pads → `… 1` and `… 2`
(ordering falls to SDL enumeration / power-on order — the usual caveat).

PlayStation pads (PS4 treated like DualSense, by vid:pid) are matched via
`devices.sdl_devices()`; unassigned managed players are set to `Handler: Null`.
No pad → bind Player 1 to `handheld_class` or leave the file untouched. RPCS3
rewrites the yml on exit, so we edit while it's closed (ES-DE game-start) and
keep a one-time backup. PyYAML (system package) does the round-trip.

NOTE: RPCS3's SDL name should equal the SDL joystick name (`PS4 Controller`,
`Steam Deck Controller`, …). If a live PS3 launch shows a pad unbound, check the
exact name RPCS3 logged (`name='…'`) and set `name_overrides` in the backend cfg.
"""
from __future__ import annotations

import copy
import shutil
import sys
import threading
from pathlib import Path

try:
    import yaml
except ImportError:                    # PyYAML missing → cannot route RPCS3
    yaml = None

from .devices import sdl_devices
from . import fsutil, mad_paths, pad_assign


def _warn(msg: str) -> None:
    """Append a diagnostic to router.log (Game Mode has no console)."""
    line = f"rpcs3_cfg: {msg}"
    print(line, file=sys.stderr)
    try:
        log = mad_paths.storage("controller-router", "router.log")
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

# SDL Player Input template (Handler + full Config + Buddy), Device set per call.
_SDL_PLAYER: dict = {
    'Handler': 'SDL',
    'Config': {
        'Left Stick Left': 'LS X-', 'Left Stick Down': 'LS Y-',
        'Left Stick Right': 'LS X+', 'Left Stick Up': 'LS Y+',
        'Right Stick Left': 'RS X-', 'Right Stick Down': 'RS Y-',
        'Right Stick Right': 'RS X+', 'Right Stick Up': 'RS Y+',
        'Start': 'Start', 'Select': 'Back', 'PS Button': 'Guide',
        'Square': 'West', 'Cross': 'South', 'Circle': 'East', 'Triangle': 'North',
        'Left': 'Left', 'Down': 'Down', 'Right': 'Right', 'Up': 'Up',
        'R1': 'RB', 'R2': 'RT', 'R3': 'RS', 'L1': 'LB', 'L2': 'LT', 'L3': 'LS',
        'IR Nose': '', 'IR Tail': '', 'IR Left': '', 'IR Right': '',
        'Tilt Left': '', 'Tilt Right': '',
        'Motion Sensor X': {'Axis': 'X', 'Mirrored': False, 'Shift': 0},
        'Motion Sensor Y': {'Axis': 'Y', 'Mirrored': False, 'Shift': 0},
        'Motion Sensor Z': {'Axis': 'Z', 'Mirrored': False, 'Shift': 0},
        'Motion Sensor G': {'Axis': 'RY', 'Mirrored': False, 'Shift': 0},
        'Orientation Reset Button': '', 'Orientation Enabled': False,
        'Pressure Intensity Button': '', 'Pressure Intensity Percent': 50,
        'Pressure Intensity Toggle Mode': False, 'Pressure Intensity Deadzone': 0,
        'Analog Limiter Button': '', 'Analog Limiter Toggle Mode': False,
        'Left Stick Multiplier': 100, 'Right Stick Multiplier': 100,
        'Left Stick Deadzone': 8000, 'Right Stick Deadzone': 8000,
        'Left Stick Anti-Deadzone': 4259, 'Right Stick Anti-Deadzone': 4259,
        'Left Trigger Threshold': 0, 'Right Trigger Threshold': 0,
        'Left Pad Squircling Factor': 8000, 'Right Pad Squircling Factor': 8000,
        'Color Value R': 0, 'Color Value G': 0, 'Color Value B': 20,
        'Blink LED when battery is below 20%': True,
        'Use LED as a battery indicator': False,
        'LED battery indicator brightness': 10, 'Player LED enabled': True,
        'Large Vibration Motor Multiplier': 100,
        'Small Vibration Motor Multiplier': 100, 'Switch Vibration Motors': False,
        'Mouse Movement Mode': 'Relative',
        'Mouse Deadzone X Axis': 60, 'Mouse Deadzone Y Axis': 60,
        'Mouse Acceleration X Axis': 200, 'Mouse Acceleration Y Axis': 250,
        'Left Stick Lerp Factor': 100, 'Right Stick Lerp Factor': 100,
        'Analog Button Lerp Factor': 100, 'Trigger Lerp Factor': 100,
        'Device Class Type': 0, 'Vendor ID': 1356, 'Product ID': 616,
    },
    'Buddy Device': 'Null',
}
_NULL_PLAYER = {'Handler': 'Null', 'Device': 'Null', 'Config': {}, 'Buddy Device': 'Null'}


def _expand(p: str) -> Path:
    return Path(p).expanduser()


_OVERRIDES_FILE = Path.home() / ".config/rpcs3/input_configs/global/.mad-input-overrides.yml"


# ── context-keyed store (docked | handheld) ───────────────────────────────────
# The override sidecar gained a context dimension so handheld play carries its own
# button map: on disk it is `{ "docked": {player: {...}}, "handheld": {...} }`. A
# LEGACY flat sidecar (`{player: {...}}`, pre-handheld) is read as the DOCKED context and
# rewritten into the new shape on the next save, so an existing user's docked remaps are
# never lost. A handheld context that has never been set reads as `{}` -> stock default.
# Mirrors lib/pcsx2_cfg's context-keyed store, adapted to RPCS3's YAML sidecar + int keys.
_CONTEXTS = ("docked", "handheld")

# REENTRANT: save_overrides does a context-preserving read-modify-write; the RLock keeps two
# near-simultaneous saves to DIFFERENT contexts on the one sidecar from lost-updating each other
# (the RPC pool has 4 workers). Mirrors pcsx2_cfg._OVERRIDES_LOCK.
_OVERRIDES_LOCK = threading.RLock()


def _norm_ctx(context) -> str:
    return "handheld" if str(context).strip().lower() == "handheld" else "docked"


def _is_context_keyed(data) -> bool:
    """True if `data` is the context-keyed shape (every top-level key is a context). An empty
    dict counts as new (both readings empty); a legacy flat sidecar keyed by player number is
    False."""
    return isinstance(data, dict) and all(k in _CONTEXTS for k in data)


def _clean_player_map(d) -> dict:
    """`{player(int): {key: token}}` from a raw per-player mapping; non-int players and empty/
    non-dict binds dropped (the same coercion the old flat loader applied)."""
    out: dict = {}
    if isinstance(d, dict):
        for k, v in d.items():
            try:
                pk = int(k)
            except (TypeError, ValueError):
                continue
            if isinstance(v, dict) and v:
                out[pk] = {str(bk): str(bv) for bk, bv in v.items()}
    return out


def _raw_store() -> dict:
    """The whole on-disk sidecar, NORMALISED to `{context: {player(int): {...}}}` (a legacy flat
    sidecar folds under "docked"). Empty contexts omitted. Lets save preserve the other context."""
    if yaml is None or not _OVERRIDES_FILE.is_file():
        return {}
    try:
        data = yaml.safe_load(_OVERRIDES_FILE.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as ex:
        _warn(f"corrupt override sidecar {_OVERRIDES_FILE} ({ex}); dropping ALL remaps this launch")
        return {}
    if not isinstance(data, dict):
        return {}
    if _is_context_keyed(data):
        return {c: m for c in _CONTEXTS if (m := _clean_player_map(data.get(c)))}
    flat = _clean_player_map(data)
    return {"docked": flat} if flat else {}


def load_overrides(context="docked") -> dict:
    """MAD per-button input overrides ``{player(int): {ps3_key: sdl_token}}`` for `context`
    ("docked"|"handheld"), or ``{}``. MAD's source of truth for RPCS3 button remaps — merged into
    the transient SDL profile at launch (see ``_player_block``) so a remap APPLIES in-game without
    touching the user's resting (often native-handler) config, which the launch wrapper restores on
    exit. A legacy flat sidecar reads as the docked context; an unset handheld context reads as
    ``{}`` (=> the pad's stock default at launch). Handheld is its own axis, never the docked map."""
    return _raw_store().get(_norm_ctx(context), {})


def save_overrides(overrides: dict, context="docked") -> None:
    """Write `overrides` into `context`, PRESERVING the other context's map (and migrating a legacy
    flat sidecar into the context-keyed shape on the way). Empty per-player dicts are dropped; an
    emptied context is removed. Atomic; serialized across the RPC pool by _OVERRIDES_LOCK."""
    if yaml is None:
        raise RuntimeError("PyYAML not available — cannot write RPCS3 input overrides")
    ctx = _norm_ctx(context)
    slice_ = {int(k): dict(v) for k, v in sorted(overrides.items()) if v}
    with _OVERRIDES_LOCK:
        full = _raw_store()
        if slice_:
            full[ctx] = slice_
        else:
            full.pop(ctx, None)
        full = {c: full[c] for c in _CONTEXTS if full.get(c)}
        _OVERRIDES_FILE.parent.mkdir(parents=True, exist_ok=True)
        text = yaml.safe_dump(full, sort_keys=True, default_flow_style=False, allow_unicode=True)
        fsutil.atomic_write_text(_OVERRIDES_FILE, text)


def _player_block(existing, device: str, overrides: dict | None = None) -> dict:
    """An SDL `Player N Input` block bound to ``device``. PRESERVES an existing SDL
    per-button ``Config`` when the on-disk block already has one (a user who configured
    RPCS3 with the SDL handler directly), else starts from the canonical ``_SDL_PLAYER``
    template; THEN layers MAD's per-button ``overrides`` on top. This is what makes a
    remap actually APPLY in-game: the user's resting config (commonly RPCS3's native
    DualSense/DualShock handler) is left untouched and restored on exit, while the
    transient SDL profile the game reads carries the remap. ``.router-backup`` is the
    recovery path."""
    if (isinstance(existing, dict) and existing.get('Handler') == 'SDL'
            and isinstance(existing.get('Config'), dict) and existing['Config']):
        block = copy.deepcopy(existing)
    else:
        block = copy.deepcopy(_SDL_PLAYER)
    block['Handler'] = 'SDL'
    block['Device'] = device
    block.setdefault('Buddy Device', 'Null')
    if overrides and isinstance(block.get('Config'), dict):
        block['Config'].update(overrides)
    return block


def assign(cfg: dict, logger, devs=None, pins=None) -> int:
    """Apply the PS3 pad assignment. Returns 0 (launch always continues).

    `pins` ({player: evdev Device}) + `devs` let a GLOBAL pin set a player's
    Device string to the pinned pad's '<SDL name> <rank>' (rank among same-named
    SDL devices) — so a specific DualShock4 lands on the chosen player."""
    if yaml is None:
        logger.warning("rpcs3: PyYAML not available; skipping")
        return 0
    ymlp = _expand(cfg.get("config_file",
                           "~/.config/rpcs3/input_configs/global/Default.yml"))
    manage = int(cfg.get("manage_players", 2))
    pad_classes: list[str] = list(cfg.get("pad_classes", []))
    handheld = cfg.get("handheld_class", "")
    name_overrides: dict = dict(cfg.get("name_overrides", {}))

    if not ymlp.is_file():
        logger.warning(f"rpcs3: config {ymlp} not found; skipping")
        return 0

    sdl = sdl_devices()

    def sdl_name(dev) -> str:
        return name_overrides.get(dev.vidpid, dev.name)

    def _encode_pin(pdev, sdl_devs, evdevs):
        # "<name> <k>" for the pinned pad (k = its rank among same-named SDL
        # devices in index order, matching rpcs3's own enumeration).
        from .devices import sdl_index_of
        si = sdl_index_of(pdev, evdevs, sdl_devs)
        sd = next((s for s in sdl_devs if s.index == si), None) if si is not None else None
        if sd is None:
            return None
        nm = sdl_name(sd)
        same = sorted((s for s in sdl_devs if sdl_name(s) == nm), key=lambda s: s.index)
        kk = next((i + 1 for i, s in enumerate(same) if s.index == si), 1)
        return f"{nm} {kk}"

    # player slot (1-based) -> Device string "<name> <k>" via the shared pipeline.
    # Each pad's "<name> <rank>" string is unique, so collisions are plain
    # value-membership (unit_count=1).
    devices = pad_assign.assign_slots(
        sdl, manage, pins, devs,
        pad_classes=pad_classes, handheld=handheld,
        encode_auto=lambda d, rank: f"{sdl_name(d)} {rank + 1}",
        encode_pin=_encode_pin,
        rank_key=sdl_name, base_index=1,
    )
    if devices is None:
        logger.info("rpcs3: no PlayStation pad and no handheld; leaving yml")
        return 0
    logger.info("rpcs3: players -> "
                + (", ".join(f"P{k}={v!r}" for k, v in sorted(devices.items()))
                   or "(none)"))

    # Full-file round-trip is DELIBERATE: RPCS3 owns Default.yml's schema, so we
    # safe_load the WHOLE doc, mutate ONLY the `Player N Input` blocks below, and
    # safe_dump it back verbatim (sort_keys=False preserves top-level order; the
    # one-time .router-backup above guards the original). Every non-pad RPCS3
    # setting survives untouched — do NOT switch to a partial/in-place edit.
    with ymlp.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    ovr = load_overrides()
    for k in range(1, manage + 1):
        key = f"Player {k} Input"
        if k in devices:
            data[key] = _player_block(data.get(key), devices[k], overrides=ovr.get(k))
        else:
            data[key] = copy.deepcopy(_NULL_PLAYER)

    backup = ymlp.with_name(ymlp.name + ".router-backup")
    if not backup.exists():
        shutil.copy2(ymlp, backup)
        logger.info(f"rpcs3: one-time backup -> {backup.name}")

    # Dump to a string first, then write atomically (a half-written YAML would
    # make RPCS3 drop the pad config on next launch).
    text = yaml.safe_dump(data, sort_keys=False, default_flow_style=False,
                          allow_unicode=True)
    fsutil.atomic_write_text(ymlp, text)
    logger.info(f"rpcs3: wrote {ymlp}")
    return 0


def assign_devices(players, config_path: str | None = None, manage: int = 7,
                   overrides: dict | None = None) -> dict:
    """Configure-once device pick (MAD Standalones 'pads → players'): bind the ordered
    ``players`` (a list of ``devices.SdlDevice`` in priority order) to ``Player 1..N
    Input`` of RPCS3's global ``Default.yml`` by each pad's ``"<SDL name> <rank>"``
    (rank = its 1-based position among same-named SDL devices in index order — matching
    RPCS3's own enumeration); managed slots beyond the connected count are set to
    ``Handler: Null``. The Standalones launch wrapper calls this at game-start (and
    restores the prior ``Player N Input`` blocks on exit).

    Unlike ``assign()`` there is no policy ``pad_classes``/``pins``/handheld — the caller
    already chose the order, so this is the explicit-list writer — but it DOES honor
    ``[backends.rpcs3].name_overrides`` (RPCS3 binds by SDL name, so an override must apply
    on this now-live path too). Every non-pad RPCS3 setting survives (full YAML round-trip;
    one-time ``.router-backup``). Raises FileNotFoundError if Default.yml is missing;
    RuntimeError if PyYAML is unavailable."""
    if yaml is None:
        raise RuntimeError("PyYAML not available — cannot write RPCS3 input config")
    path = _expand(config_path or "~/.config/rpcs3/input_configs/global/Default.yml")
    if not path.is_file():
        raise FileNotFoundError("Default.yml not found — launch a PS3 game once")

    # Honor the documented [backends.rpcs3].name_overrides knob on THIS (now-live, since
    # ps3 is router_skip) path too — assign() used it; assign_devices must as well or the
    # override is inert and an override-dependent pad silently fails to bind. Used for BOTH
    # the Device string AND the same-name rank grouping (rank and name must stay consistent).
    from .policy import load_merged
    be = (load_merged().get("backends", {}) or {}).get("rpcs3", {})
    name_overrides = dict(be.get("name_overrides", {})) if isinstance(be, dict) else {}
    sdl = sdl_devices()

    def sdl_name(dev) -> str:
        return name_overrides.get(dev.vidpid, dev.name)

    def _rank(dev) -> int:
        nm = sdl_name(dev)
        same = sorted((s for s in sdl if sdl_name(s) == nm), key=lambda s: s.index)
        return next((i + 1 for i, s in enumerate(same) if s.index == dev.index), 1)

    slots = max(int(manage), len(players))
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    # Per-game launch merges pass the merged (global + per-game) map; else the global sidecar.
    ovr = overrides if overrides is not None else load_overrides()
    for k in range(1, slots + 1):
        key = f"Player {k} Input"
        if k - 1 < len(players):
            data[key] = _player_block(data.get(key),
                                      f"{sdl_name(players[k - 1])} {_rank(players[k - 1])}",
                                      overrides=ovr.get(k))
        else:
            data[key] = copy.deepcopy(_NULL_PLAYER)

    backup = path.with_name(path.name + ".router-backup")
    if not backup.exists():
        shutil.copy2(path, backup)

    text = yaml.safe_dump(data, sort_keys=False, default_flow_style=False,
                          allow_unicode=True)
    fsutil.atomic_write_text(path, text)
    return {"assigned": [(f"Player {i + 1}", f"{sdl_name(d)} {_rank(d)}")
                         for i, d in enumerate(players[:slots])]}
