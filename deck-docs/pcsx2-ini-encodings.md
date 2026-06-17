# PCSX2 (PS2) PCSX2.ini stored-value encodings (for a non-corrupting MAD settings editor)

Verified 2026-06-14 against PCSX2 **source** (GitHub `PCSX2/pcsx2` master) and the
**live config** this PCSX2 build wrote on this Steam Deck.

- Live config path: `~/.config/PCSX2/inis/PCSX2.ini`  (AppImage `~/Applications/pcsx2-Qt.AppImage`)
- Internal binary / process name (pgrep -x): **`pcsx2-qt`** (Exec= in `net.pcsx2.PCSX2.desktop`)
- Format: INI, `Key = Value` (spaces around `=`), `[Section]` and slash-namespaced
  sections like `[EmuCore/GS]`, `[EmuCore/Speedhacks]`. LF endings. Blank line(s)
  between sections.
- **Booleans = lowercase `true` / `false`** (e.g. `VsyncEnable = false`, `EnableFastBoot = true`).
  NOT `1/0`, NOT `True/False`. (Contrast: Dolphin uses `True/False`.)

## [EmuCore/GS] Renderer  — GSRendererType, SPARSE SIGNED int code (NOT a 0-based index)
Source: `enum class GSRendererType : s8` in `pcsx2/Config.h`:
```
Auto = -1, DX11 = 3, Null = 11, OGL = 12, SW = 13, VK = 14, DX12 = 15, Metal = 17
```
Stored as the integer code STRING. Live value `Renderer = 14` = **Vulkan (VK)**.
Default `Auto = -1`. Because codes are sparse/negative, this is a STRING-token enum
for our contract: write_mode="option", options_stored = the code strings
("-1","12","13","14","3","15"), index round-trips. NEVER write str(index).
Curated user-facing subset (Linux): Auto(-1), Vulkan(14), OpenGL(12), Software(13).
(DX11/DX12 are Windows-only; Metal is macOS-only; Null is debug — omit from the menu,
but get() must prepend the current code if it isn't in the curated list so it round-trips.)

## [EmuCore/GS] upscale_multiplier  — float member, but THIS build writes a BARE INT
Source: `Pcsx2Config::GSOptions` `float UpscaleMultiplier` (default 1.0), key
`"upscale_multiplier"`, read as float. BUT the live file stores `upscale_multiplier = 3`
(bare integer, no `.0`) — the build strips trailing zeros for whole values.
=> Expose as an INTEGER ENUM of native-scale steps and WRITE THE BARE INT token
("1".."8") exactly as PCSX2 does, so it round-trips with the on-disk `= 3`.
(Do NOT write "3.0" / "3.000000" — would not match the stored token byte-for-byte
and PCSX2 would rewrite it on exit anyway.) 1=Native(1x) ... up to 8x curated.

## [EmuCore] VsyncEnable  — bool
Source: EmuCore `SettingsWrapBitBool(VsyncEnable)`. Lives in `[EmuCore]` (NOT [EmuCore/GS]).
Live: `VsyncEnable = false`. bool lowercase true/false.

## [EmuCore/GS] deinterlace_mode  — GSInterlaceMode, 0-based int == option index
Source: `enum class GSInterlaceMode : u8` (Config.h), key `"deinterlace_mode"`:
```
0 Automatic, 1 Off, 2 WeaveTFF, 3 WeaveBFF, 4 BobTFF, 5 BobBFF,
6 BlendTFF, 7 BlendBFF, 8 AdaptiveTFF, 9 AdaptiveBFF   (10 = Count, not selectable)
```
Live: `deinterlace_mode = 0` (Automatic). Stored int EQUALS the option index =>
this CAN use write_mode="index" (write str(idx)). Default Automatic=0.

## [EmuCore/GS] MaxAnisotropy  — RAW DEGREE, not an index
Source: `SettingsWrapBitfieldEx(MaxAnisotropy, "MaxAnisotropy")`. Stored value is the
anisotropy DEGREE: 0/1 = Off, 2, 4, 8, 16. Live: `MaxAnisotropy = 8` (8x).
=> integer-token enum (write_mode="option", tokens "0","2","4","8","16"); NOT str(index).
(The UI combo is Off / 2x / 4x / 8x / 16x; "Off" stored as 0 historically, modern
default 0. The current file has 8.)

## [EmuCore/Speedhacks] EECycleRate  — signed int, value == slider value, range -3..+3
Source: `MINIMUM_EE_CYCLE_RATE = -3`, `MAXIMUM_EE_CYCLE_RATE = 3`, default 0
(EmulationSettingsWidget.cpp). Stored int = slider value directly. Live: `EECycleRate = 0`.
Qt combobox labels (top→bottom, value -3..+3):
```
-3 "50% (Underclock)"
-2 "60% (Underclock)"
-1 "75% (Underclock)"
 0 "100% (Normal Speed)"
 1 "130% (Overclock)"
 2 "180% (Overclock)"
 3 "300% (Overclock)"
```
=> enum where stored token is the SIGNED INT ("-3".."3") => write_mode="option",
options_stored = ["-3","-2","-1","0","1","2","3"], index round-trips. NOT str(index)
(index 0 would wrongly mean "-3"; the stored value for the default is "0", at index 3).

## [EmuCore] EnableFastBoot  — bool, default true
Source: EmuCore `SettingsWrapBitBool(EnableFastBoot)`, default true. Live:
`EnableFastBoot = true`. bool lowercase.

## Multitap & pad→port/slot mapping  (for MAD's pads→players binder, ≥3 players)
Verified 2026-06-17 against PCSX2 **source** (`pcsx2/SIO/Pad/Pad.cpp` `LoadConfig`,
`pcsx2/SIO/Sio.cpp` `sioConvertPortAndSlotToPad`) + the live PCSX2.ini.
- `[Pad]` section holds the multitap toggles: `MultitapPort1` / `MultitapPort2`
  (lowercase `true`/`false`; live default both `false`). `.ini` keys are 1-based;
  the source enum is `MultitapPort0/1_Enabled` (0-based) — same thing.
- Config has a FLAT pad index 0-7 = sections `[Pad1]`..`[Pad8]`. `LoadConfig` forces
  `NotConnected` for index 2-4 unless `MultitapPort1`, and 5-7 unless `MultitapPort2`.
  So `[Pad1]`/`[Pad2]` (idx 0/1) are ALWAYS active (the two base console ports).
- `sioConvertPortAndSlotToPad(port, slot)`: `slot==0 → port`; else `port==0 → slot+1`;
  else `slot+4`. ⇒ exact (physical port → pad section) mapping:
  - **Port 1** (physical) slots A/B/C/D = pad idx 0/2/3/4 = **`Pad1, Pad3, Pad4, Pad5`**.
  - **Port 2** (physical) slots A/B/C/D = pad idx 1/5/6/7 = **`Pad2, Pad6, Pad7, Pad8`**.
- MAD's `pcsx2_cfg._slot_plan(n)` maps n priority pads PORT-1-FIRST: n≤2 → `[Pad1,Pad2]`
  multitap OFF (standard 2-controller layout); 3-4 → `[Pad1,Pad3,Pad4,Pad5]` +
  `MultitapPort1`; 5-8 → + `[Pad2,Pad6,Pad7,Pad8]` + both. So priority i → in-game
  player i+1. LIMITATION: PS2 multiplayer is game-dependent — this fits the common
  single-multitap (4p) / dual-multitap (8p) layouts, not games with an unusual port
  expectation. (Memory-card `Multitap*_Slot*_Enable` keys are SEPARATE — not touched.)
- Sources: https://github.com/PCSX2/pcsx2/blob/master/pcsx2/SIO/Pad/Pad.cpp ,
  https://github.com/PCSX2/pcsx2/blob/master/pcsx2/SIO/Sio.cpp

## SAFE write approach for this format
REUSE: `lib/inifile.py` `section_body()/set_section()` operate on WHOLE sections
(used by `lib/pcsx2_cfg.py` for the [PadN] router blocks) — too coarse for single
keys (would reserialize the section and could reorder/normalize). For the MAD
settings page, use the Dolphin module's BYTE-PRESERVING single-key approach
(`lib/madsrv/dolphin_cmds.py`): `_section_span()` to bound `[Section]`,
`_read_key()` / `_replace_key()` to rewrite ONLY that one value token via regex,
`_ensure_bak()` (one-time .bak), `_atomic_write()` (temp+replace), `newline=""`
to preserve LF. Only edit keys that ALREADY EXIST (never create). Refuse writes
while `pcsx2-qt` is running. Section names contain `/` — `re.escape` handles it
(the Dolphin regexes already use `re.escape(section)`).

Sources:
- https://github.com/PCSX2/pcsx2/blob/master/pcsx2/Config.h (GSRendererType, GSInterlaceMode enums)
- https://github.com/PCSX2/pcsx2/blob/master/pcsx2/Pcsx2Config.cpp (LoadSave macros: Renderer, upscale_multiplier, deinterlace_mode, MaxAnisotropy, VsyncEnable, EnableFastBoot, EECycleRate)
- https://github.com/PCSX2/pcsx2/blob/master/pcsx2-qt/Settings/EmulationSettingsWidget.cpp (EECycleRate range -3..3, default 0, value==slider)
- https://github.com/PCSX2/pcsx2/blob/master/pcsx2-qt/Settings/EmulationSettingsWidget.ui (EE cycle rate % labels)
