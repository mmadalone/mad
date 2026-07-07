"""pcsx2x6 RETAIL per-gun input pages (x6r_usb1 / x6r_usb2).

Retail exists solely for PS2 GunCon2 lightgun games: the pcsx2x6 fork launched with
`-datapath ~/Applications/pcsx2x6-retail` (a SEPARATE ini). Its Input group is gun-focused,
so the two USB ports are the two guns. Each x6r_usbN namespace is a SINGLE-PORT view of the
shipped unified guncon2_retail page (button binds + per-gun crosshair + the live Sinden
Start/Stop toggle), pinned to one USB port so the port picker is dropped (the C++ shows a
picker only when a page has >1 player). The underlying guncon2_retail.* page stays
registered unchanged; these namespaces just delegate to its helpers with a fixed port.

The DualShock2 [Pad1]/[Pad2] are still bound at launch by switch_bind (ps2guncon), but a
lightgun setup drives movement from the gun itself, so they are not surfaced here (there is
no retail Controller Port / Pads -> players leaf, and no JVS -- retail is PS2 discs).
"""
from __future__ import annotations

from . import guncon2_retail_input_cmds as gr
from .rpc import method


def _single_port(sel: str) -> dict:
    """The guncon2_retail page scoped to one gun: drop the port picker so the C++ renders
    just this USB port's rows + crosshair selectors + the Sinden toggle."""
    pay = gr._get(sel, gr._running())
    pay.pop("players", None)
    pay.pop("player", None)
    return pay


def _register(ns: str, sel: str) -> None:
    # Uncached (like guncon2_retail): the Start/Stop Sinden button reflects the LIVE driver
    # state, so the page must recompute on each open rather than serve a config-keyed cache.
    @method(f"{ns}.input_get", slow=True)
    def _g(params, sel=sel):
        return _single_port(sel)

    @method(f"{ns}.input_set", slow=True)
    def _s(params, sel=sel):
        p = dict(params)
        p["player"] = sel
        return gr._input_set(p)

    @method(f"{ns}.input_clear", slow=True)
    def _c(params, sel=sel):
        p = dict(params)
        p["player"] = sel
        return gr._input_clear(p)

    @method(f"{ns}.selector_set", slow=True)
    def _sv(params, sel=sel):
        p = dict(params)
        p["player"] = sel
        return gr._selector_set(p)

    # Both gun ports write the SAME retail ini, so save/cancel delegate to the single
    # guncon2_retail buffer (ctx = its ini PATH, resolved at call time so a test swap sticks).
    @method(f"{ns}.input_save", slow=True)
    def _save(params):
        return {"saved": gr._buf.save(gr._INI), "dirty": gr._buf.dirty}

    @method(f"{ns}.input_cancel", slow=True)
    def _cancel(params):
        gr._buf.cancel(gr._INI)
        return {"cancelled": True, "dirty": gr._buf.dirty}


for _un, _us in (("x6r_usb1", "usb1"), ("x6r_usb2", "usb2")):
    _register(_un, _us)
