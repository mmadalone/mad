# Standalone emulator controller-binding formats (for MAD input mapping)

Reference for the per-emulator input-map feature (`<emu>.input_get/.input_set`,
`GuiMadPageEmuInputMap`). How each standalone stores ONE controller button
binding, so we can write bindings programmatically. Gathered 2026-06-15 from
official docs + verified against live configs on this Deck. Each emulator already
has a router-side config module (`lib/<emu>_cfg.py`) that knows its format — reuse
it. **Key fact:** the controller-router rewrites these at launch (`*_cfg.assign()`)
but PCSX2-style writers clone the EXISTING button layout and change only the
device index, so a per-button remap PERSISTS — verify this per emulator before
shipping its writer.

## PCSX2 (PS2) — DONE (Phase 0)
- File: `~/.config/PCSX2/inis/PCSX2.ini`, INI, `[Pad1]`/`[Pad2]`.
- Binding: `Cross = SDL-2/FaceSouth` → `SDL-<idx>/<SDLsource>`. Sources:
  FaceSouth/East/West/North, LeftShoulder, RightShoulder, +LeftTrigger,
  +RightTrigger, Back, Start, Guide, LeftStick, RightStick, DPadUp/…, and axes
  `+LeftX`/`-LeftY`/etc. Router (`lib/pcsx2_cfg._bind_template`) abstracts the
  index → remaps persist. Verified live + router-gate.
- Src: github.com/PCSX2/pcsx2 (Qt input). Booleans lowercase.

## Eden (Switch, Yuzu fork) — DONE (Phase 1, run 42/43)
- File: `~/.config/eden/qt-config.ini`, Qt INI, `[Controls]`.
- Binding: `player_0_button_a="engine:sdl,port:0,guid:<32hex>,button:M"`.
- **`button:M` is the SDL joystick BUTTON RANK, not the evdev code-0x130.** SDL
  enumerates only the buttons the pad actually reports, so absent ones (BTN_C
  0x132, BTN_Z 0x135 on most pads) are skipped and the rank shifts. Mapping used
  by `input_translate.sdl_button_index(code)`: A 0x130→0, B 0x131→1, X 0x133→2,
  Y 0x134→3, L 0x136→4, R 0x137→5, ZL 0x138→6, ZR 0x139→7, Minus 0x13A→8,
  Plus 0x13B→9, Guide 0x13C→10, LStick 0x13D→11, RStick 0x13E→12.
  Axes: `axis:N`, with `axis_x/axis_y`, `invert_*`, `deadzone`.
- **GUID note (corrected 2026-06-15):** the live `guid:` is a **CRC-based SDL
  GUID** (NOT the bare vid:pid I first noted). MAD's per-button writer
  (`eden_input_cmds.input_set`) does NOT compute or touch the GUID — it
  **preserves the on-disk `guid:`/`port:`** and rewrites only the `button:M`
  token (`_BTN_RE = re.compile(r"button:(\d+)")`). So no GUID derivation is
  needed and the device identity stays whatever Eden already wrote.
- Router gate: **N/A** — `[systems.switch] router_skip = true`
  (`controller-policy.toml`), so `eden_cfg.assign()` never runs for Switch and
  there is no launch-time clobber; the remap persists by construction. (The
  EBUSY guard `proc_guard.emulator_running("eden")` still blocks writes while
  Eden runs, since Eden rewrites the file itself on exit.)
- Existing router writer: `lib/eden_cfg.assign`. Qt paired lines
  (`key\default=…` + `key=…`) — match only `key=` (`cfgutil.ini_replace`).
- Src: github.com/yuzu-emu/yuzu (input parser). Verified live + headless.

## Ryujinx (Switch) — DONE (Phase 1, run 43)
- File: `~/.config/Ryujinx/Config.json`, **JSON** (cfgutil is INI-only → uses the
  `lib/madsrv/ryujinx_json.py` load/write helper; Ryujinx rewrites the file on
  exit, so a full parse→modify→reserialize round-trip is byte-safe).
- Bindings live in the `input_config` array, one object per player
  (`player_index: "Player1"`…`"Player8"`, `"Handheld"`). Buttons are nested in
  two objects: `right_joycon` (A/B/X/Y, R, ZR, Plus) and `left_joycon`
  (L, ZL, Minus, + d-pad). KEY = Switch button (`button_a`, `button_r`,
  `dpad_up`, …); **VALUE = a `GamepadInputId` enum TOKEN string**, e.g.
  `"A"`,`"B"`,`"X"`,`"Y"`,`"LeftShoulder"`,`"RightShoulder"`,`"LeftTrigger"`,
  `"RightTrigger"`,`"Minus"`,`"Plus"`,`"Guide"`,`"LeftStick"`,`"RightStick"`,
  `"DpadUp"`… (NOT an index). Mapping by `input_translate.ryujinx_button(code)`
  (evdev code → token; note the two north/west face codes map "crossed" per the
  Switch face layout): 0x130→"A", 0x131→"B", 0x133→"Y", 0x134→"X",
  L 0x136→"LeftShoulder", R 0x137→"RightShoulder",
  ZL 0x138→"LeftTrigger", ZR 0x139→"RightTrigger", Minus 0x13A→"Minus",
  Plus 0x13B→"Plus", Guide 0x13C→"Guide", LStick 0x13D→"LeftStick",
  RStick 0x13E→"RightStick". (d-pad / sticks read-only in v1 — capture skips
  hats/axes, same scope as PCSX2.)
- v1 remaps **Player 1 only** (the first `input_config` entry whose
  `player_index=="Player1"`, else index 0); Handheld is left as-is.
- Router gate: **N/A** — Ryujinx is NOT router-managed (no `[backends.ryujinx]`,
  no `ryujinx_cfg.py`; Switch is `router_skip=true`), so nothing rewrites the
  config at launch → the remap persists. EBUSY guard
  `proc_guard.emulator_running("ryujinx")` blocks writes while it runs.
- Writers: `lib/madsrv/ryujinx_input_cmds.py` (input), `lib/madsrv/ryujinx_cmds.py`
  (Settings: top-level `graphics_backend`/`res_scale`/`aspect_ratio`/
  `anti_aliasing`/`scaling_filter`/`enable_vsync`/`backend_threading`).
  ⚠️ One-time `.router-backup` taken before MAD's first write.
- Src: github.com/Ryujinx/Ryujinx (`Common/Configuration`, `GamepadInputId`).
  Verified headless (real Config.json sha256 unchanged after a no-op test;
  see session notes). Verified live config layout on this Deck.

### Ryujinx device id (pads → players) — DONE (run 44)
- Each `input_config[]` player entry has an `id` (which physical pad drives it),
  `backend = "GamepadSDL2"`, `controller_type`, and `player_index`
  (`Player1`…`Player8`, `Handheld`). The MAD **Controllers → pads → players** page
  (configure-once; `pads.get`/`pads.set` → `lib/madsrv/ryujinx_cfg.assign_devices`)
  rewrites only `id` per player, **preserving** the `left_joycon`/`right_joycon`
  button maps.
- **id format (verified):** `"{sdl_index}-{guid}"` where `guid` is the **.NET
  `Guid.ToString()`** of the 16-byte SDL GUID — i.e. the first three fields are
  little-endian and the last eight bytes are emitted as-is. So from the SDL GUID
  hex `bytes`: `d1=LE(b0..3)`, `d2=LE(b4..5)`, `d3=LE(b6..7)`, then
  `b8b9-b10..b15`. Examples (both live):
  `28de:1205` SDL GUID `03000000de2800000512000000026800` → `0-00000003-28de-0000-0512-000000026800`;
  `28de:11ff` → `0-00000003-28de-0000-ff11-000001000000`.
  Implemented as `ryujinx_cfg.ryujinx_id(index, sdl_guid)`.
- ⚠️ **The index prefix MUST match** the device's live SDL index — Ryujinx
  `SDL2GamepadDriver.GetGamepad(id)` re-derives the id from the joystick at the
  parsed index and returns null on mismatch (so it is NOT GUID-only matching).
  We write the current `devices.sdl_devices()` index; if the connected set
  changes the index can shift → re-apply the order. (Configure-once contract.)
- Src: Ryujinx `src/Ryujinx.Input.SDL2/SDL2GamepadDriver.cs`
  (`GenerateGamepadId` = `joystickIndex + "-" + guid`; `GetGamepad` index check).
- **Eden device pick** (same MAD page) reuses `eden_cfg.assign_devices` →
  `_eden_guid(vidpid)` + `_retarget` on the LIVE `[Controls]` `player_N_*` lines
  (only `guid:`/`port:` change; every `button:M` preserved). Router gate N/A
  (Switch router_skip); EBUSY while the emulator runs.

## RPCS3 (PS3) — clean, Phase 1
- File: `~/.config/rpcs3/input_configs/global/Default.yml` (EmuDeck path:
  `~/.config/EmuDeck/backend/configs/rpcs3/…`), YAML, `Player N Input:`.
- Binding: under `Config:` → `Cross: A`, `L1: TL`, `L2: LZ+`, d-pad `Hat0 X-`,
  sticks `LX+`/`RY-`. `Handler:` = `Evdev`|`SDL`, `Device:` = NAME+rank
  (`"PS4 Controller 1"`). Existing writer: `lib/rpcs3_cfg.assign`.
- Src: github.com/RPCS3/rpcs3. Verified live.

## Dolphin (GC/Wii) — clean-ish, Phase 1
- File: `~/.var/app/org.DolphinEmu.dolphin-emu/config/dolphin-emu/GCPadNew.ini`
  (+ `WiimoteNew.ini`), INI, `[GCPad1]`.
- Binding: `Device = evdev/1/<name>` (or `SDL/0/<name>`) + `Buttons/A = EAST`
  (evdev names: EAST/SOUTH/NORTH/WEST/START/SELECT) or backtick `` `Button E` ``
  (SDL). D-pad `D-Pad/Up = `, triggers `Triggers/L = `. Pipe `|` = OR.
  NOTE: existing `dolphin_cmds` only does Wii via `dolphin-wii-mode.sh`; GameCube
  pad writer is NEW.
- Src: github.com/dolphin-emu/dolphin. Verified live.

## Cemu (Wii U) — HARDER, Phase 2
- File: `~/.config/Cemu/controllerProfiles/controller{0-3}.xml`, XML.
- Binding: `<mappings><entry><mapping>25</mapping><button>40</button></entry>` —
  `<mapping>` = emulated-button code, `<button>` = SDL internal input code; both
  poorly documented (reverse-engineer from live + SDL enums). `<uuid>` =
  `<sdl_index>_<GUID>`, `<api>SDLController</api>`. Existing writer:
  `lib/cemu_cfg.assign` uses vid:pid TEMPLATES → per-button edits the template.
- Src: github.com/cemu-project/Cemu (sparse docs — CONFIRM codes before shipping).

## Supermodel (Model 3) — text but token map needed, Phase 2
- File: `~/.supermodel/Config/Supermodel.ini`, INI, `[ Global ]` (LAST section).
- Binding: `InputStart1 = "KEY_1,JOY1_BUTTON8"` — tokens `JOYn_BUTTONm`,
  `JOYn_YAXIS_NEG`, `JOYn_POV1_UP` (n = joystick number, m = button number).
  Comma list = OR. Booleans int 1/0. Confirm the router doesn't manage it +
  build the evdev→`JOYn_BUTTONm` token map.
- Src: github.com/trzy/Supermodel + bundled README §10. (Agent first thought
  GUI-only; the INI DOES take Input* bindings — verify on-device.)

## Model 2 (m2emu) — NOT FEASIBLE
- Bindings in `~/Emulation/roms/model2/CFG/<game>.input` = BINARY m2emu blob;
  `EMULATOR.INI [Input]` only has `XInput=1`/`RawDevP*` (no per-button keys).
  Delegates to Windows XInput. MAD cannot remap — surface a "uses the standard
  Xbox layout; remap in the emulator's menu" note instead.
- Src: ElSemi/Nebula README (2014).

See [[../FIX-PLAN.md]]-style plan in the session plan file; capture pipeline =
`capture_cmds.py` + `GuiMadCaptureModal`; translator = `lib/madsrv/input_translate.py`.
