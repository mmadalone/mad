# Sega Model 2 Emulator (ElSemi "m2emu") — EMULATOR.INI

Reference for the MAD **Model 2** page (`lib/madsrv/model2_cmds.py` +
`GuiMadPageModel2`). The emulator is ElSemi's Windows "Model 2 Emulator", run under
Proton/umu by `model2-m2emu.sh`. All settings live in one shared, Windows-style,
**CRLF**, inline-`;`-commented INI:

    ~/Emulation/roms/model2/EMULATOR.INI

**Sources (docs-first):**
- ElSemi's shipped `README.TXT` — `~/Emulation/roms/model2/README.TXT` (2014; the
  authoritative changelog/option notes; confirms FSAA, WideScreenWindow, HoldGears,
  RawInput-2-mice, XInput, MeshTransparency=PS3.0, AutoMip).
- The INI's own inline `;` comments (e.g. "0-10000", "needs PS3.0", "breaks DoA").
- ElSemi / Nebula emulator site: http://nebula.emulatronia.com (legacy; Wayback for history).
- Cached: 2026-06-13.

## Exposed (curated) settings — what the MAD page edits
Grouped exactly as in `model2_cmds.GROUPS`. Type drives the widget (bool→chip,
enum/int/float/resolution→stepper).

### Display [Renderer]
| key | meaning | type / values | default |
|---|---|---|---|
| FullScreenWidth + FullScreenHeight | fullscreen resolution (page offers presets incl. 1280×800 = Deck native) | resolution "WxH" | 1280×720 |
| WideScreenWindow | windowed aspect ratio | enum 0=4:3, 1=16:9, 2=16:10 | 1 |
| FSAA | full-screen anti-aliasing (D3D) | bool 0/1 | 1 |

### Graphics quality [Renderer]
| key | meaning | type | note |
|---|---|---|---|
| Bilinear | bilinear texture filtering | bool | safe |
| Trilinear | mipmap + trilinear filtering | bool | **breaks Dead or Alive** (README) |
| FilterTilemaps | bilinear on 2D tilemap layers | bool | can cause stretch artifacts |
| MeshTransparency | meshed-polygon translucency | bool | **needs a Pixel Shader 3.0 GPU** |
| AutoMip | D3D auto-mipmap generation | bool | advanced |
| FakeGouraud | guess per-vertex (gouraud) colour from flat data | bool | advanced |

### Color (gamma) [Renderer]
| key | meaning | type | default |
|---|---|---|---|
| GammaR / GammaG / GammaB | per-channel gamma correction | float 0.5–2.5 step 0.1 | 1.0 (no correction) |

### Input [Input]
| key | meaning | type | note |
|---|---|---|---|
| XInput | Xbox-style pad support (incl. vibration) | bool | recommended on Deck |
| UseRawInput | RawInput 2-mice support (2-gun games) | bool | **note: README calls it `RawInput`; the shipped INI key is `UseRawInput`** |
| HoldGears | return to neutral when no gear button held | bool | racing games |
| RawDevP1 / RawDevP2 | assign a specific mouse to P1/P2 | int 0–9 (0-based) | only matters with UseRawInput=1 |

### Troubleshooting [Renderer]
| key | meaning | type | note |
|---|---|---|---|
| ForceManaged | force D3D Managed (not Dynamic) textures | bool | "use if the emulator crashes after loading or doesn't show anything" |

## HIDDEN — never read or written by the page (deliberate)
- **Debug / "don't change":** `Wireframe`, `SoftwareVertexProcessing`.
- **In-emulator-menu-managed** (the emu rewrites these; editing them externally is
  pointless/harmful): `FullMode`, `Sound`, `Frameskip`, `AutoFull`, `Filter` (a bitmask),
  `ForceSync`.
- **Launcher-managed:** `DrawCross` — `model2-m2emu.sh` force-toggles it per game
  (0 for gun games bel/gunblade/rchase2 that draw their own crosshair, 1 otherwise).
  A UI toggle here would just be overwritten on the next launch, so it stays hidden.
- **Paths:** `[RomDirs] Dir1` (`roms`, relative to the exe) and `Dir2`
  (`Z:\run\media\deck\1tbDeck\ROMs\model2` — Wine `Z:` = Linux `/`, i.e. the SD-card
  model2 ROMs). Not editable from the page; the backslashes are exactly why we use
  regex line-edits, not configparser.

## Sinden SMOOTHED guns as m2emu RawInput mice — NOT FEASIBLE (researched 2026-06-17)

Question asked: can the Sinden **smoothed** guns ("SindenLightgun Mouse (Smoothed P1/P2)")
be assigned as m2emu's `RawDevP1`/`RawDevP2` mice for the 2-gun games
(`gunblade`/`bel`/`rchase2`)? **No** — blocked by absolute-vs-relative AND by Wine's
single-mouse RawInput. Three independent, documented blockers:

1. **Sinden mouse is ABSOLUTE-only; m2emu RawInput needs RELATIVE.** Verified on-device:
   the raw Sinden mouse evdev nodes (`/dev/input/event21` vid=16c0 pid=0f38, `event257`
   pid=0f39) advertise `EV_ABS ABS_X/ABS_Y` (range 0..32767) and **zero `EV_REL` axes**.
   `sinden-smoother.py make_virtual()` (lines 74-91) clones that profile — the Smoothed
   uinput devices are **also EV_ABS-only** (it writes `EV_ABS ABS_X/Y`, smoother.py lines
   221/231), so feeding the smoothed device changes nothing about the input type.
   ElSemi's M2 RawInput path expects relative-delta mice (README "2 mice support",
   "mouse will be locked to the emulator window"). Confirmed for Model 2 specifically:
   arcadecontrols user **isucamper**: "the devices cannot work because they use 'absolute'
   input, and M2 only works with multiple mice that use 'relative' input" (AimTrak; Sinden's
   mouse interface is the same absolute class). User **BadMouth** got it working only with
   relative mice.
   - https://forum.arcadecontrols.com/index.php?topic=104484.0
2. **Wine/Proton give one merged mouse to user32 RawInput.** Two physical/virtual mice
   cannot be separated into `RawDevP1`!=`RawDevP2` under Proton. SeongGino Steam-Deck
   arcade guide: lightgun games on Wine "will only work in singleplayer with one lightgun
   device" due to "limitations in how Wine handles mouse input" (cites Wine Bugzilla #55547).
   NOTE the proton-ge `proton-rawinput.patch` keeps devices separate and converts
   `MOUSE_MOVE_ABSOLUTE`→relative, BUT that is the **DirectInput** raw path, not the
   user32 `GetRawInputData` path m2emu's `RawDevP1/P2` use — it does not lift the
   single-merged-mouse limit for m2emu.
   - https://gist.github.com/SeongGino/92c5222d0baaf235332e09d2522e76db
3. **DemulShooter (the Windows 2-gun workaround) also fails on Proton.** Sinden wiki M2 +
   RetroBat say native m2emu RawInput is buggy and DemulShooter (with `UseRawInput=0`,
   `DrawCross=0`) is the documented 2-player path **on Windows**; on Linux it shares
   Wine's single-mouse constraint and is not installed here (no `*demulshooter*` under
   `~/Emulation`/`~/.local`/`~/Applications`).
   - https://www.sindenwiki.org/wiki/Model2  ("Mouse IDs change randomly after reboots,
     and unplugging the ID number set as RawDevP1/P2 may need changing"; "DemulShooter is
     required for two players on the Model 2 emulator")

**Even index assignment is unreliable on m2emu's own terms:** `RawDevP1/P2` are bare
0-based **Windows** RawInput indices that "change randomly after reboots" (Sinden wiki);
MAD's `lib/devices.py detect_sinden_mouse_indices()` only knows the **Linux/RetroArch**
udev order, which has no guaranteed relationship to the Proton RawInput hDevice order.

**Realistic ceiling:** SINGLE gun via the existing `LightgunMono`→X11 **system cursor**
path (`sinden-start.sh` lines 8-9 — LightgunMono "drives the X11 system cursor"); Wine
forwards the one system mouse to m2emu. m2emu also needs `d3dcompiler_47` and an **X11**
session (not Wayland/XWayland) per SeongGino. Keep dual-Sinden for Supermodel / RetroArch
/ Dolphin, which read evdev/ManyMouse directly and bypass Wine RawInput.

**A MAD "start guns" button is feasible/cheap but does NOT make m2emu assignment work** —
the Smoothed uinput devices exist only while `sinden-smoother.py` runs, and MAD already
starts them via RPC `sinden.driver` action `start` (`lib/madsrv/sinden_cmds.py`) →
`sinden-start.sh`. Labeling such a button "to assign the guns in m2emu" would be
misleading given blockers 1+2+3.

## Per-game controller bindings: `CFG/<game>.input` (reverse-engineered 2026-06-17)
NOT in EMULATOR.INI — each game's button map is a separate **binary** blob
`~/Emulation/roms/model2/CFG/<game>.input` (68-116 bytes), an array of little-endian
uint32 words, one per game function in a fixed (undocumented) per-game slot order, no
header/labels. Authored ONLY by m2emu's in-game `Game → Configure Controls` Win32 dialog
(README.TXT) — no INI/CLI/registry path → **not MAD-editable headlessly** (this is why
there's no model2 input-map page). Observed word encoding `(device_page << 8) | code`:
- `0x00xx` = **keyboard** (DIK scancode `xx`). In practice only service/system keys:
  `0x3b/0x3c/0x40/0x41/0x42` = F1/F2/F6/F7/F8 (test/service/start/coin), plus a few legacy
  fallbacks (e.g. daytona steering on arrow keys `0xc8/0xd0/0xcb/0xcd`).
- `0x01xx` / `0x02xx` = **joystick device 1 / device 2** — where the actual **gameplay**
  bindings live (verified: gunblade/vcop bind aim+trigger+buttons to `0x01xx` for P1,
  `0x02xx` for P2).
- a `0xff` high flag marks an **axis** binding (e.g. daytona `0xff000102`).
KEY TAKEAWAY: gameplay is **joystick-bound**, not keyboard-bound (only service keys are
keyboard). So the X-Arcade in **Xbox mode** (a JOYSTICK, 045e:02a1) matches the gameplay
bindings as "device 1" (subject to Proton's enumeration order). The old "X-Arcade presents
as a keyboard" claim (was in supermodel-native.sh) was wrong and is fixed.

## Editing safety (model2_cmds.py)
- Read with `newline=""` (preserve CRLF), targeted regex sub of ONLY the one key's value
  token, write atomically (`.model2-tmp` → `os.replace`). Inline comments, section order,
  the `Z:\` path and all other lines stay byte-for-byte identical (unit-tested).
- One-time `EMULATOR.INI.bak` before the first edit (rule #5: never clobber user data).
- Stateless: every `set` re-reads disk, so it can't clobber the launcher's `DrawCross` sed.
- Settings take effect the **next time a Model 2 game is launched** (m2emu reads the INI at startup).
