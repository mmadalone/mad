# pcsx2x6 (PCSX2 fork) USB / JVS PCSX2.ini encodings (lightgun controller-config UI)

Ground truth for the `[USB1]`/`[USB2]`/`[JVS]` controller config in the **pcsx2x6**
fork (a PCSX2 fork, github.com/mmadalone/pcsx2x6 deck-patches). USB device ini format
mirrors mainline PCSX2; upstream `PCSX2/pcsx2` source is valid corroboration.

Verified 2026-06-25 against THREE sources:
1. **Live on-device config** (this Deck): pcsx2x6 runs `-portable` (marker
   `~/Applications/pcsx2x6/portable.ini`), so the ACTIVE config MAD reads/writes is the
   PORTABLE `~/Applications/pcsx2x6/PCSX2x6/inis/PCSX2.ini` (what every pcsx2x6 backend
   targets). `~/.config/PCSX2x6/inis/PCSX2.ini` is a stale non-portable leftover, not read
   in `-portable` mode. Also `launchers/tests/fixtures/pcsx2x6/PCSX2.ini`.
2. **Fork source** (mirror `PS2Homebrew-arcade/pcsx2x6` @ master = same codebase):
   `pcsx2/USB/usb-lightgun/guncon2.cpp`, `pcsx2/USB/usb-hid/usb-hid.cpp`,
   `pcsx2/USB/USB.cpp`.
3. **Upstream PCSX2** (`PCSX2/pcsx2` master) for the inherited-vs-fork-specific split.

- Config path (portable): `~/Applications/pcsx2x6/PCSX2x6/inis/PCSX2.ini`. Inner proc: `pcsx2-qt`.
- Format: same as mainline PCSX2 (see `pcsx2-ini-encodings.md`): INI, `Key = Value`
  (spaces around `=`), LF endings, booleans = lowercase `true`/`false`.

## Section + key-name construction (source-confirmed)
From `pcsx2/USB/USB.cpp`:
- Section name: `GetConfigSection(port)` -> `fmt::format("USB{}", port + 1)` =>
  **`[USB1]`** (port 0) and **`[USB2]`** (port 1). 1-based.
- Every binding/setting key is **prefixed with the device-type token + `_`**:
  `fmt::format("{}_{}", device, bind_name)`. So device `guncon2` binding `Trigger`
  => ini key `guncon2_Trigger`; setting `cursor_path` => `guncon2_cursor_path`.
  (HID mouse would be `hidmouse_Pointer` etc.)
- `Type = <token>` is the bare device token, NOT prefixed.

## 1. USB port Type tokens  ([USB1]/[USB2] `Type = ...`)
Source-confirmed tokens (the device registry / proxy names). Default when nothing
connected is `None` (`USB::SetDefaultConfiguration` writes `Type = None`;
`GetConfigDevice` default-value is `"None"`).

| Connected device      | `Type =` token |
|-----------------------|----------------|
| nothing / unplugged   | `None`         |
| HID Mouse             | `hidmouse`     |
| GunCon2 light gun     | `guncon2`      |

(There are MANY other USB tokens in PCSX2 (pad, msd, singstar, etc.) but only these
three matter for the lightgun rig.) Token is case-sensitive as written above.

## 2. HID Mouse bindings  ([USB1]/[USB2] when `Type = hidmouse`)
Source: `usb-hid.cpp` HIDMouseDevice `Bindings()`. Bare names below; ini key = prefixed
with `hidmouse_`. Binding VALUE format is the standard PCSX2 InputManager source string.

| UI label      | bare name      | ini key              | value format example         |
|---------------|----------------|----------------------|------------------------------|
| Pointer (aim) | `Pointer`      | `hidmouse_Pointer`   | `Pointer-0/...` (raw mouse N)|
| Left Button   | `LeftButton`   | `hidmouse_LeftButton`| `Pointer-0/LeftButton`       |
| Right Button  | `RightButton`  | `hidmouse_RightButton`| `Pointer-0/RightButton`     |
| Middle Button | `MiddleButton` | `hidmouse_MiddleButton`| `Pointer-0/MiddleButton`   |

(Value can also be `Keyboard/<Key>`, `SDL-<n>/<bind>` etc, any InputManager source.
The `Pointer-N/...` form = raw pointer device N; LeftButton/RightButton/MiddleButton are
the pointer-device button sub-bindings. NOT verified the exact default mouse bindings,
but the on-disk guncon2 Trigger uses `Pointer-0/LeftButton`, confirming the format.)

## 3. GunCon2 / light-gun bindings  ([USB1]/[USB2] when `Type = guncon2`)
Source: `usb-lightgun/guncon2.cpp` binding info. Bare names; ini key = `guncon2_` + name.
**FORK binding set is a SUBSET of upstream** (see section 6). The keys the fork/our rig
actually uses (confirmed on-disk):

| UI label / role        | bare name        | ini key                 | value format example   |
|------------------------|------------------|-------------------------|------------------------|
| Trigger                | `Trigger`        | `guncon2_Trigger`       | `Pointer-0/LeftButton` |
| Foot Pedal (= A)       | `A`              | `guncon2_A`             | `Keyboard/Z`           |
| Start                  | `Start`          | `guncon2_Start`         | `Keyboard/Return`      |
| Coins (= Select)       | `Select`         | `guncon2_Select`        | `Keyboard/Escape`      |
| Relative aim Up        | `RelativeUp`     | `guncon2_RelativeUp`    | `Keyboard/Up`          |
| Relative aim Down      | `RelativeDown`   | `guncon2_RelativeDown`  | `Keyboard/Down`        |
| Relative aim Left      | `RelativeLeft`   | `guncon2_RelativeLeft`  | `Keyboard/Left`        |
| Relative aim Right     | `RelativeRight`  | `guncon2_RelativeRight` | `Keyboard/Right`       |

Also present in fork source + an older .bak (D-pad set, distinct from Relative*):
`guncon2_Up` / `guncon2_Down` / `guncon2_Left` / `guncon2_Right` (bare `Up/Down/Left/Right`).
These are the digital D-pad; the `Relative*` set is the relative-aim axis. The CURRENT
live config uses `Relative*`, the older `.bak` used `Up/Down/Left/Right`.

UPSTREAM PCSX2 guncon2.cpp ALSO defines `B`, `C`, `ShootOffscreen`, `Recalibrate`
(=> `guncon2_B`, `guncon2_C`, `guncon2_ShootOffscreen`, `guncon2_Recalibrate`).
NOT verified present in the fork's pared-down list; fork source review showed only
Up/Down/Left/Right/Trigger/A/Start/Select/Relative*. **FLAG: if the UI needs B/C, verify
against the fork's exact guncon2.cpp binding array before exposing them.**

GenericInputBinding defaults are unbound; the rig hand-binds everything (see live config).

## 4. GunCon2 crosshair / cursor keys  ([USB1]/[USB2])
Source: `guncon2.cpp` settings (`cursor_path`/`cursor_scale`/`cursor_color`),
ini-prefixed with `guncon2_`. **These three are UPSTREAM PCSX2, not fork-added.**

| ini key                | value type / format            | default     | on-disk example                                   |
|------------------------|--------------------------------|-------------|---------------------------------------------------|
| `guncon2_cursor_path`  | string, absolute PNG file path | empty       | `/home/deck/Applications/pcsx2x6/PCSX2x6/crosshairs/Green.png` |
| `guncon2_cursor_scale` | float                          | `1.0`       | `0.08`                                             |
| `guncon2_cursor_color` | hex color string, CSS form, optional `#` prefix | `FFFFFF` | `#ffffff` / `FFFFFF` |

NOTE: live PCSX2.ini currently OMITS `guncon2_cursor_scale` (uses default) and
`guncon2_cursor_color`; the `.bak` + fixture carry `guncon2_cursor_scale = 0.08`.
Per-gun crosshair = the PORTABLE/active ini per USB port (USB1 = gun1, USB2 = gun2).

## 5. [JVS] section  (FORK-SPECIFIC, does NOT exist in upstream PCSX2)
The entire `[JVS]` section is added by the pcsx2x6 fork for the Namco 2x6 arcade I/O.
Keys confirmed from live config + the .bak (which exposes more JVS keys when TestMode on).

| key                    | value type / format          | on-disk example | notes |
|------------------------|------------------------------|-----------------|-------|
| `TestMode`             | bool (lowercase true/false)  | `false`         | Enters the in-game arcade operator/Test menu = IN-GAME gun calibration (Gun Initialize / Gun Adjust). Flip true, calibrate, flip back. Saved to game SRAM. |
| `SindenBorderEnabled`  | bool                         | `false`         | Sinden white-border overlay on/off. |
| `SindenBorderMode`     | int (mode index)             | `0`             | Border placement mode. |
| `SindenBorderThickness`| int (px, ~1..50)             | `10`            | Border thickness in pixels. |
| `VideoVoltage`         | bool                         | `true`          | JVS video/timing (not lightgun-relevant). |
| `MonitorSyncFrequency` | bool                         | `true`          | "" |
| `VideoSyncSplit`       | bool                         | `true`          | "" |
| `SuppressDaemon`       | bool                         | `true`          | "" |
| `P1_Service`           | InputManager source string   | `Keyboard/Escape` | JVS Service button (Vampire Night calibration needs it; NOT auto-bound, see memory pcs2x6-namco-246). |

JVS keys that appear ONLY when `TestMode = true` is written (seen in .bak, value-format
not all verified): `P2TriggerBit`, `P2SensorBit`, `DumpRam` (bool), `SysByteOr`,
`ScreenposTrig`. Treat as advanced/debug; not needed for the controller-config UI.
**FLAG: SindenBorderMode integer->label mapping not verified from source; expose as raw int
or verify the enum in the fork's JVS source before labelling modes.**

## 6. Fork-specific vs inherited-from-upstream
| item | origin |
|------|--------|
| `Type` tokens `None` / `hidmouse` / `guncon2` | upstream PCSX2 |
| HID mouse `Pointer`/`LeftButton`/`RightButton`/`MiddleButton` | upstream PCSX2 |
| guncon2 bindings (Up/Down/Left/Right/Trigger/A/B/C/Start/Select/Relative*/ShootOffscreen/Recalibrate) | upstream PCSX2 (fork ships a SUBSET) |
| `guncon2_cursor_path` / `guncon2_cursor_scale` / `guncon2_cursor_color` | **upstream PCSX2** (not fork-added) |
| `[JVS]` section + `TestMode` + `SindenBorder*` + Video*/Sync*/Service | **FORK-SPECIFIC** (deck-patches; absent upstream) |

MAD already encodes part of this: `lib/madsrv/pcsx2x6_lightgun_cmds.py` writes
`guncon2_cursor_scale` ([USB1]/[USB2]) + `SindenBorderEnabled/Mode/Thickness` ([JVS]);
gates on `Type = guncon2` (`standalones_cmds._pcsx2x6_has_guncon2`).

## Sources
- Live on-device (portable): `~/Applications/pcsx2x6/PCSX2x6/inis/PCSX2.ini`,
  `launchers/tests/fixtures/pcsx2x6/PCSX2.ini` (read 2026-06-25).
- Fork source (mirror, same codebase): https://github.com/PS2Homebrew-arcade/pcsx2x6
  -> `pcsx2/USB/usb-lightgun/guncon2.cpp`, `pcsx2/USB/usb-hid/usb-hid.cpp`,
  `pcsx2/USB/USB.cpp` (GetConfigSection/GetConfigDevice/SetDefaultConfiguration + the
  `fmt::format("{}_{}", device, bind_name)` key builder).
- Fork repo proper: https://github.com/mmadalone/pcsx2x6 (tree/deck-patches).
- Upstream corroboration + inherited-vs-fork split:
  https://github.com/PCSX2/pcsx2 -> `pcsx2/USB/usb-lightgun/guncon2.cpp`.
- Related: `deck-docs/pcsx2-ini-encodings.md`, memory `pcs2x6-namco-246-on-deck`.
