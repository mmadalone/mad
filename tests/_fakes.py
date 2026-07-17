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
    class_index()/sdl_index_of()/pad_labels.device_label() read.

    is_sinden / is_steam_virtual / is_mad_virtual DERIVE from vid:pid, exactly as the real
    devices.Device computes them as @property. They used to default to a flat False, which let a
    fixture LIE about itself: FakeDevice(vid=0x28de, pid=0x11ff) claimed not to be Steam-virtual,
    so routing.resolve_ports (which excludes those phantoms) happily seated it on a port -- a
    device the real router can never see. A baseline captured that way measures a fiction, and it
    is the shape of bug the "a replica is not a measurement" lesson keeps costing us.
    Pass an explicit True/False to override; None (the default) derives.
    """
    vid: int
    pid: int
    path: str
    name: str = ""
    phys: str = ""
    is_joypad: bool = True
    is_sinden: bool | None = None
    is_steam_virtual: bool | None = None
    is_mad_virtual: bool | None = None
    uniq: str = ""

    def __post_init__(self):
        from lib.devices import SINDEN_PID_P1, SINDEN_PID_P2
        if self.is_sinden is None:
            self.is_sinden = (self.pid in (SINDEN_PID_P1, SINDEN_PID_P2)
                              or "Sinden" in self.name)
        if self.is_steam_virtual is None:
            self.is_steam_virtual = (self.vid == 0x28DE and self.pid == 0x11FF)
        if self.is_mad_virtual is None:
            self.is_mad_virtual = (self.vid == 0x4D41)


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
