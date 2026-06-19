# X-Arcade (Xbox 360 mode) — USB identity & the P1/P2 stability story

Empirical sysfs findings, verified live on the Deck 2026-06-10 (no official doc covers
this — X-Gaming publishes nothing about the tri-mode board's USB descriptors). Filled
as a genuine doc gap per CLAUDE.md rule #1/#2.

## What the cab looks like on USB

```
/sys/bus/usb/devices/3-1.1   idVendor=045e idProduct=0719   product='X-Arcade 2'
├── 3-1.1:1.0   bInterfaceNumber=00  class ff/5d/81  driver=xpad  → /dev/input/eventN (side A)
└── 3-1.1:1.1   bInterfaceNumber=01  class ff/5d/81  driver=xpad  → /dev/input/eventM (side B)
```

- The tri-mode board impersonates a Microsoft **Xbox 360 Wireless Receiver** (045e:0719)
  at the device level, but its **product string is 'X-Arcade 2'** — a robust way to spot
  the cab at the USB-device level if ever needed (a real MS receiver says
  "Xbox 360 Wireless Receiver for Windows").
- Each joystick side is **hard-wired to its own USB interface** (bInterfaceNumber 00/01).
  The interface number is part of the hardware descriptor — it survives replugs,
  re-enumeration, and interface re-registration. Verified: 3-1.1:1.0 → event6,
  3-1.1:1.1 → event10 (2026-06-10).

## Why evdev can't tell the sides apart

Both xpad evdev nodes are **byte-identical**:

| field | side A | side B |
|-------|--------|--------|
| name  | `Xbox 360 Wireless Receiver` | same |
| vid:pid | 045e:02a1 | same |
| phys  | `usb-xhci-hcd.2.auto-1.1/input0` | **identical — both say `input0`** |
| uniq  | (empty) | (empty) |

Event-node numbering is assignment-order and **can flip** (e.g. one interface
error-resets and re-registers with a new higher number while the other keeps its node).

## The stable discriminator

```
/sys/class/input/eventN/device/device/bInterfaceNumber   (hex: "00" / "01")
```

In code: `lib.devices.usb_iface_num("/dev/input/eventN")` → `0` / `1` / `None`
(None for Bluetooth/virtual/platform devices). Consumers (added 2026-06-10, task #15):

- `controller-router.py` x-arcade token branch sorts hits with `_xa_iface_rank`
  → deterministic per-port pick (preview, logs, per-device binds, standalone backends).
- `lib/mad_xarcade_tester.py _xa_start` sorts P1/P2 tags by interface
  → tester readout AND `xarcade-calib.json` `P1:`/`P2:` keys survive replugs.

On 2026-06-10 the live order was iface 00 = lower event number, i.e. the new sort
matches the order the existing calibration was made under — no recalibration needed.

## Honest residual (NOT fixed by this)

**RetroArch in-game P1/P2 for the two sides.** The router binds RA ports via
`input_playerN_reserved_device`, which RA matches **by vid:pid** — both sides share
045e:02a1, so RA's sequential cascade assigns them in **RA's own enumeration order**
(usually interface order on a clean plug, but not guaranteed after a mid-session
interface reset). `input_playerN_joypad_index` pinning was tried and REVERTED
(task #37, 2026-06-05 note in controller-router.py: the router's js_index is not RA's
enumeration index; it broke both pads). Exact identical-pad pinning in RA remains an
open upstream problem; press-to-identify wouldn't help either — there is no way to
address a specific one of two identical pads in an RA reservation.

Practical impact: low — both sides have identical layouts; the residual only matters
for which side is "player 1" in 2-player RA arcade sessions after an abnormal
mid-session re-enumeration.

## Arcade STICK = BTN_TRIGGER_HAPPY buttons, not a hat (2026-06-19)

The X-Arcade's joystick reports as evdev `BTN_TRIGGER_HAPPY1..4` (**0x2c0-0x2c3**), NOT the
`ABS_HAT0X/Y` it also exposes (that hat is **dead/phantom**). Each interface (P1=iface0, P2=iface1)
is byte-identical, so the stick maps the same on either half. RetroArch ranks them as buttons
**11-14** (HAPPY1=left, 2=right, 3=up, 4=down); the SDL standalones read them as a **d-pad**
(gamecontrollerdb `dpleft:b11 … dpdown:b14`). Full mapping, the dead-hat suppression, the dual-emit
capture, and the Eden exception are in [[standalone-input-binding-formats.md]].
