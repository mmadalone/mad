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
from pathlib import Path

try:
    import yaml
except ImportError:                    # PyYAML missing → cannot route RPCS3
    yaml = None

from .devices import sdl_devices
from . import fsutil, pad_assign

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

    for k in range(1, manage + 1):
        key = f"Player {k} Input"
        if k in devices:
            block = copy.deepcopy(_SDL_PLAYER)
            block['Device'] = devices[k]
            data[key] = block
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
