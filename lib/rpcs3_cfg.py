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
from . import fsutil

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
    prio = {c: i for i, c in enumerate(pad_classes)}
    ps = sorted((d for d in sdl if d.vidpid in prio),
                key=lambda d: (prio[d.vidpid], d.index))

    def sdl_name(dev) -> str:
        return name_overrides.get(dev.vidpid, dev.name)

    # Global pins -> "<name> <k>" for the pinned pad (k = its rank among same-named
    # SDL devices in index order, matching rpcs3's own enumeration).
    pinned_str: dict[int, str] = {}
    if pins and devs:
        from .devices import sdl_index_of
        for port, pdev in pins.items():
            if port > manage:
                continue
            si = sdl_index_of(pdev, devs, sdl)
            sd = next((s for s in sdl if s.index == si), None) if si is not None else None
            if sd is None:
                continue
            nm = sdl_name(sd)
            same = sorted((s for s in sdl if sdl_name(s) == nm), key=lambda s: s.index)
            kk = next((i + 1 for i, s in enumerate(same) if s.index == si), 1)
            pinned_str[port] = f"{nm} {kk}"

    # player slot (1-based) -> Device string "<name> <k>"
    devices: dict[int, str] = {}
    if ps:
        seen: dict[str, int] = {}
        for k in range(1, manage + 1):
            if k - 1 < len(ps):
                d = ps[k - 1]
                nm = sdl_name(d)
                idx = seen.get(nm, 0) + 1
                seen[nm] = idx
                devices[k] = f"{nm} {idx}"
        logger.info("rpcs3: players -> "
                    + ", ".join(f"P{k}={v!r}" for k, v in devices.items()))
    elif not pinned_str:
        deck = next((d for d in sdl if d.vidpid == handheld), None)
        if not handheld or deck is None:
            logger.info("rpcs3: no PlayStation pad and no handheld; leaving yml")
            return 0
        devices[1] = f"{sdl_name(deck)} 1"
        logger.info(f"rpcs3: no PlayStation pad -> P1={devices[1]!r} (handheld)")

    # Pins win on their ports; drop any in-order assignment that collides with a
    # pinned pad so two players never point at the same physical controller.
    for port, s in sorted(pinned_str.items()):
        devices[port] = s
    pinned_vals = set(pinned_str.values())
    for port in [p for p in devices if p not in pinned_str and devices[p] in pinned_vals]:
        del devices[port]
    if pinned_str:
        logger.info("rpcs3: pins -> "
                    + ", ".join(f"P{k}={v!r}" for k, v in sorted(pinned_str.items())))

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
