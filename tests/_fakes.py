"""
Test doubles for the controller-assignment backends (pad_assign + *_cfg).

These let the pure-logic part of each backend's ``assign()`` run with NO real
SDL / evdev hardware: a ``FakeDevice`` stands in for an ``evdev``-derived
``devices.Device`` (only the attributes the backends actually read), and
``sd()`` builds a ``devices.SdlDevice`` namedtuple. ``patch_sdl()`` swaps the
real ``sdl_devices()`` enumeration for a fixed fake list across the lib.

Kept deliberately minimal — they exist only to drive ``assign()`` so the
golden-output harness can prove the pad_assign refactor changes no bytes.
"""
from __future__ import annotations

import contextlib
from dataclasses import dataclass, field

from lib.devices import SdlDevice


@dataclass
class FakeDevice:
    """Stand-in for devices.Device — only the fields vidpid()/joypads()/
    class_index()/sdl_index_of() read."""
    vid: int
    pid: int
    path: str
    name: str = ""
    is_joypad: bool = True
    is_sinden: bool = False
    is_steam_virtual: bool = False
    is_mad_virtual: bool = False
    uniq: str = ""


def dev(vidpid: str, path: str, name: str = "") -> FakeDevice:
    """FakeDevice from a 'vvvv:pppp' class string."""
    vid, pid = (int(x, 16) for x in vidpid.split(":"))
    return FakeDevice(vid=vid, pid=pid, path=path, name=name)


def sd(index: int, vidpid: str, guid: str, name: str) -> SdlDevice:
    """A devices.SdlDevice(index, vidpid, guid, name)."""
    return SdlDevice(index, vidpid, guid, name)


@contextlib.contextmanager
def patch_sdl(sdl_list):
    """Make every backend's ``sdl_devices()`` return ``sdl_list``.

    The *_cfg modules do ``from .devices import sdl_devices`` at import time, so
    the name is bound in each module's namespace — patch all of them plus the
    source in lib.devices."""
    import lib.devices as _d
    targets = ["lib.devices", "lib.pcsx2_cfg", "lib.xemu_cfg",
               "lib.eden_cfg", "lib.rpcs3_cfg", "lib.pad_assign"]
    saved = {}
    import importlib
    mods = []
    for name in targets:
        try:
            m = importlib.import_module(name)
        except ImportError:
            continue
        if hasattr(m, "sdl_devices"):
            saved[name] = (m, m.sdl_devices)
            mods.append(m)
    for m in mods:
        m.sdl_devices = lambda pump=True, _l=sdl_list: list(_l)
    try:
        yield
    finally:
        for name, (m, orig) in saved.items():
            m.sdl_devices = orig
