# Wii Remote (Wiimote) — HID protocol, DolphinBar, extensions

Sources: wiibrew.org/wiki/Wiimote, /Extension_Controllers, /Nunchuck, /Classic_Controller
(fetched 2026-06-10). Confirmed live against the quit-watcher + a live DolphinBar capture.

## Transports on the Deck
- **Bluetooth `hid-wiimote`** → kernel exposes a normal **evdev** node `Nintendo Wii Remote`
  (BTN_A/B, BTN_1/2 = keys, dpad, BTN_SELECT=−, BTN_START=+, BTN_MODE=Home); `uniq` = BT MAC.
  → fits the evdev `_gp_*` engine directly as a profile. (None paired during this work.)
- **Mayflash DolphinBar, mode 4** → vid:pid **`057e:0306`**, bound by **`hid-generic`** (NOT
  hid-wiimote) → **raw `/dev/hidrawN`, no evdev**. The bar ALWAYS presents **4 fixed hidraw
  slots** (one per input0..3); awake remotes stream on a slot, empty slots are silent.
  `lib/devices.py`: `dolphinbar_present()`, `_dolphinbar_slot_nodes()` (returns the 4 nodes),
  `_dolphinbar_usb_present()`. Live 2026-06-10: bar on USB port 1.2.4.2 → hidraw7/8/9/10;
  two remotes awake on hidraw7+hidraw8, both already streaming report `0x30` (`30 00 00` at
  rest) with NO writes needed. Disambiguate the 2 remotes by **hidraw slot / input index**
  (empty `uniq`). Co-reading a slot does NOT steal input (Linux fans HID input reports to all
  readers) — same trick `wiimote-quit-watcher.py` uses. READ-ONLY = always safe.

## Core button bitmap (CONFIRMED — matches wiimote-quit-watcher.py)
Any data report id `>= 0x20`: report-id = buf[0], then 2 core-button bytes buf[1], buf[2].
A pressed button = 1-bit (core buttons are **active-HIGH**, unlike the extensions below).
- **buf[1]**: 0x01 Left · 0x02 Right · 0x04 Down · 0x08 Up · 0x10 **Plus(+)**
- **buf[2]**: 0x01 **Two** · 0x02 **One** · 0x04 **B** · 0x08 **A** · 0x10 **Minus(−)** · 0x80 **Home**

## Set Data Reporting Mode — output report 0x12 (REQUIRES WRITING)
`(a2) 12 TT MM` — TT bit2 (0x04)=continuous; MM=mode. Common modes:
- `0x30` core buttons only (3-byte report; what the bar streams by default) — NO extension data
- `0x31` buttons+accel · `0x32` **buttons + 8 ext bytes** · `0x34` buttons + 19 ext bytes
- `0x35` buttons+accel+16 ext · `0x3d` 21 ext bytes only
For accessory readout we'd write `a2 12 04 32` (continuous, buttons+8 ext) — only safe when
nothing else (Dolphin) owns the remote. In the standalone tester that's fine; never on the
game path.

## Status report 0x20 / extension-connected flag
Input report `(a1) 20 BB BB LF 00 00 VV`: LF bit1 (**0x02**) = extension connected; LF bits4-7
= LED state; VV = battery. If a 0x20 arrives unrequested you MUST re-send 0x12 or reports stop.

## Reading/writing registers (for extension init)
- Write `(a2) 16 MM AddrHi AddrMid AddrLo SS data...` — MM bit2 (0x04)=registers (not EEPROM).
- Read `(a2) 17 MM AddrHi AddrMid AddrLo SizeHi SizeLo` → returns via input report **0x21**.
- Extension register space base `0xA400xx` (low byte is the register).

## Extension init + ID (unencrypted mode — works for all)
1. Write **0x55 → 0xA400F0**, then **0x00 → 0xA400FB** (enables UNencrypted mode; no decrypt math).
2. Read 6 bytes at **0xA400FA** = the extension ID.
   - **Nunchuk**:           `00 00 A4 20 00 00`
   - **Classic Controller**:`00 00 A4 20 01 01`
3. Set reporting mode 0x32 → the 8 ext bytes (first 6 are the controller data below).

### Nunchuk data (6 ext bytes; unencrypted)
- byte0 SX (stick X, ~center 128, range ~35..228)
- byte1 SY (stick Y, ~center 128, range ~27..220)
- byte2..4 accel X/Y/Z high bits
- **byte5**: bit1 (0x02)=**C**, bit0 (0x01)=**Z** — both **active-LOW (0 = pressed)**.

### Classic Controller data (6 ext bytes, 0x01 format; ALL buttons active-LOW 0=pressed)
- byte0: RX<4:3>(b7-6) · LX<5:0>(b5-0)   (LX 0-63)
- byte1: RX<2:1>(b7-6) · LY<5:0>(b5-0)   (LY 0-63)
- byte2: RX<0>(b7) · LT<4:3>(b6-5) · RY<4:0>(b4-0)   (RX/RY 0-31)
- byte3: LT<2:0>(b7-5) · RT<4:0>(b4-0)   (LT/RT 0-31)
- **byte4**: 0x80 D-Right · 0x40 D-Down · 0x20 **LT(full)** · 0x08 **Home** · 0x04 **Plus(+)** · 0x02 **RT(full)** · (b0 const 1)
- **byte5**: 0x80 **ZL** · 0x40 **B** · 0x20 **Y** · 0x10 **A** · 0x08 **X** · 0x04 **ZR** · 0x02 D-Left · 0x01 D-Up
- (byte4 bit4 unused; "−/Minus" is byte4 bit?  — Minus = byte4 0x10 per other refs; verify live)

## DolphinBar MODES — decisive for the tester (Batocera wiki, 2026-06-10; live-confirmed)
The bar's physical MODE button picks how it presents the remote — this is the whole ballgame:
- **Mode 1**: Wiimote only (no nunchuk); IR pointer = mouse.
- **Mode 2**: Wiimote + Nunchuk; IR pointer = mouse; extended buttons.
- **Mode 3**: Wiimote / Nunchuk / **Classic Controller → a standard gamepad (evdev joystick)**. The
  BAR decodes the accessory; Linux sees a normal pad. "Works with all other emulators." → fits MAD's
  existing evdev `_gp_*` engine directly (accessory included), NO hidraw / writes / custom decode.
- **Mode 4**: raw passthrough for Dolphin — the APP must init the remote (write 0x12 etc.) and read its
  BT-HID stream. **On Linux this doesn't work** (wiiuse #66): the bar just emits an idle `30 00 00`
  heartbeat and ignores host output-report writes (verified live 2026-06-10 — writes had zero effect,
  button presses never changed the bytes). The quit-watcher only works because DOLPHIN does the init
  first, then we co-read. So a standalone Mode-4 hidraw tester is a dead end on Linux.

**Tester strategy:** test Wii Remotes in **Mode 3** (evdev). Mode 4 is the gaming/Dolphin mode; the user
toggles the bar's MODE button. The "awake-slot" hidraw probe is unreliable in Mode 4 (it flags the idle
`30 00 00` heartbeat as 'awake'), so don't rely on it.

## ✅ WORKING MODE-4 RECIPE (verified live 2026-06-10, full nunchuk+classic decode)
Dolphin DOES make mode-4 work on Linux; we replicated its `WiimoteReal` IOhidapi path. Reference:
Dolphin `Source/Core/Core/HW/WiimoteReal/{IOhidapi,WiimoteReal}.cpp`. Output reports are written
WITHOUT the `0xa2` BT header (Dolphin strips it in `IOWrite`); read reports start at the report id.
1. **Open BLOCKING** `os.open(node, os.O_RDWR)` — NOT O_NONBLOCK (a non-blocking write returns EAGAIN
   and silently drops the report; that was my first failure).
2. **Presence test** = write `RequestStatus` `[0x15,0x00]`:
   - `BrokenPipeError`/EPIPE → **empty slot** (no remote).
   - `OSError errno ETIMEDOUT(110)` → **stale/asleep link** — the remote is associated but not
     responding; needs a **re-sync (press 1+2)** or a **bar reset**. A plain button press or new
     batteries does NOT revive a stale link; a fresh sync does. The Tk thread must NOT block on this
     (the timeout is multi-second) — probe off-thread or non-blockingly.
   - success → remote is live.
3. **Init ONCE** (don't repeat per tick): enable unencrypted extension + ask its ID:
   `[0x16,0x04,0xa4,0x00,0xf0,0x01,0x55]`, `[0x16,0x04,0xa4,0x00,0xfb,0x01,0x00]`,
   `[0x17,0x04,0xa4,0x00,0xfa,0x00,0x06]` (read 6-byte ID).
4. **Set reporting mode** `[0x12,0x04,0x32]` (continuous, core buttons + 8 extension bytes).
5. **On a `0x20` status report**: re-send ONLY `[0x12,0x04,0x32]` (set_mode). **Do NOT re-write F0/FB**
   on every status/tick — re-initialising the extension mid-stream **wipes the nunchuk stick + C/Z**
   (they read static while only the accelerometer changes). Re-init the extension ONLY when the
   status flag shows the extension state actually changed.
6. **Keep-alive**: if no report for ~1.5 s, re-send `set_mode` (`0x12 0x04 0x32`).
7. **Identify** from the `0x21` read-reply: data bytes [4],[5] (= 6-byte ID bytes 5,6) → `00 00`=Nunchuk,
   `01 01`=Classic, `04 02`=Balance Board.
8. **Decode the `0x32` report**: `buf[0]=0x32`, `buf[1..2]`=core buttons (bitmap above), `buf[3..10]`=8
   ext bytes → Nunchuk (ext[0]SX ext[1]SY, ext[5] bit1=C bit0=Z active-low) / Classic (ext byte4/5
   active-low buttons + packed sticks) per the offsets below.

Working scratch tool: `~/Emulation/tools/launchers/wii-monitor.py` (this exact recipe + live decode →
`~/wii-monitor.log`). Slots reassign by sync order on each reset (slot↔remote not fixed).

## MAD tester implications
- DolphinBar core-button tester = pure co-read of 0x30 → light a/b/one/two/plus/minus/home/dpad
  sprites (`icons/wiimote-tester/`). No writing, no risk. Each awake slot = one picker tile.
- Accessory (nunchuk/classic) readout REQUIRES writing (0x12 mode 0x32 + the F0/FB init). Only do
  it in the standalone tester (Dolphin not running). No accessory sprites yet → text readout first.
- BT-paired wiimote = plain evdev profile in the `_gp_*` engine.
