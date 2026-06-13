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

## Editing safety (model2_cmds.py)
- Read with `newline=""` (preserve CRLF), targeted regex sub of ONLY the one key's value
  token, write atomically (`.model2-tmp` → `os.replace`). Inline comments, section order,
  the `Z:\` path and all other lines stay byte-for-byte identical (unit-tested).
- One-time `EMULATOR.INI.bak` before the first edit (rule #5: never clobber user data).
- Stateless: every `set` re-reads disk, so it can't clobber the launcher's `DrawCross` sed.
- Settings take effect the **next time a Model 2 game is launched** (m2emu reads the INI at startup).
