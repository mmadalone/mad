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

## Eden (Switch, Yuzu fork) — clean, Phase 1
- File: `~/.config/eden/qt-config.ini`, Qt INI, `[Controls]`.
- Binding: `player_0_button_a="engine:sdl,port:0,guid:<32hex>,button:1"`. Button
  index = SDL index (0=A/South,1=B/East,2=X/West,3=Y/North,4/5 shoulders,…).
  Axes: `axis:N`, with `axis_x/axis_y`, `invert_*`, `deadzone`. GUID = no-CRC SDL
  (vid:pid). Existing writer: `lib/eden_cfg.assign`. Qt paired lines
  (`key\default=…` + `key=…`) — match only `key=`.
- Src: github.com/yuzu-emu/yuzu (input parser). Verified live.

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
