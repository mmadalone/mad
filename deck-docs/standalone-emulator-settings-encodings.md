# Standalone-emulator settings: config locations + value encodings (MAD Phase 3)

Source: phase-3 verification workflow (each value cross-checked against the live
config on this Deck + the emulator's source), 2026-06-14. These drive the MAD
`lib/madsrv/<emu>_cmds.py` Settings backends (built on `lib/madsrv/cfgutil.py`).
Dolphin's are in the sibling `deck-docs/dolphin-ini-encodings.md`.

**Universal rule:** edit ONLY keys that already exist (never create — survives
version drift), byte-preserving single-value rewrite scoped to the section, one-time
`.bak`, atomic write, refuse while the emulator runs. The MAD C++ page sends bool as
`"1"/"0"` and enum as the OPTION INDEX; the backend maps that to the stored value.

## Eden (Switch) — `~/.config/eden/qt-config.ini` (Qt INI, LF, `key=value` no spaces)
- Bools are lowercase `true`/`false` — EXCEPT `use_docked_mode` which is integer `1`/`0` (1=Docked,0=Handheld).
- Each real `key=value` is preceded by a `key\default=...` twin line (literal backslash) — leave it untouched (anchored `^key=` regex skips it).
- `[Renderer] resolution_setup` int code==index: 0=0.5x,1=0.75x,2=1x,3=1.5x,4=2x,5=3x…10=8x.
- `[Renderer] use_vsync` 0=Immediate(Off),1=Mailbox,2=FIFO(On),3=FIFO Relaxed.
- `[Renderer] scaling_filter` 0=Nearest,1=Bilinear,2=Bicubic,3=Gaussian,4=ScaleForce,5=FSR.
- `[Renderer] gpu_accuracy` 0=Normal,1=High,2=Extreme.
- `[Audio] output_engine` STRING token: auto/cubeb/sdl2/null/oboe. `[Audio] volume` int 0-200.
- `[UI] fullscreen` lowercase bool. `[System] use_docked_mode` int. proc guard: pgrep -f `[Ee]den`.

## Cemu (Wii U) — `~/.config/Cemu/settings.xml` (pugixml XML; NEVER reparse/reserialize)
- Bools lowercase `true`/`false`. Edit one `<tag>value</tag>` scoped to its PARENT block.
- `<api>` is non-unique: `<Graphic><api>` (0=OpenGL,1=Vulkan) vs `<Audio><api>` (enum int, Cubeb=**3**, Linux-only).
- `<Graphic>`: VSync 0=Off/1=Double/2=Triple/3=Match; Upscale/DownscaleFilter 0=Bilinear,1=Bicubic,2=Hermite,3=Nearest; AsyncCompile bool.
- top-level `<content><fullscreen>` bool. `<Audio><TVVolume>` int 0-100. proc: pgrep -x `Cemu`.
- mlc_path is display-only (no string control) — not exposed.

## RPCS3 (PS3) — `~/.config/rpcs3/config.yml` (YAML; NO PyYAML reserialize — preserves quoting/order/no-trailing-newline)
- Bools lowercase `true`/`false`. Scope to the top-level `Video:` block (Audio also has a `Renderer:`).
- `Renderer` token Null/OpenGL/Vulkan. `Resolution` WxH token. `Resolution Scale` int 25-800.
- `Frame limit` token Off/30/50/60/120/Display/Auto/PS3 Native/Infinite.
- `Shader Mode` token (older builds store `Async Shader Recompiler` — prepend-current keeps it valid).
- `VSync`,`Write Color Buffers` bool. Per-game `config/<TITLEID>/config.yml` overrides globals. proc: pgrep -x `rpcs3`.

## PCSX2 (PS2) — `~/.config/PCSX2/inis/PCSX2.ini` (INI; bools lowercase true/false)
- `[EmuCore/GS] Renderer` SPARSE signed code: Auto=-1, OGL=12, SW=13, VK=14 (write the code, not an index).
- `[EmuCore/GS] upscale_multiplier` bare int 1..8. `MaxAnisotropy` degree 0/2/4/8/16. `deinterlace_mode` 0-based 0..9.
- `[EmuCore/GS] VsyncEnable` bool — lives in **[EmuCore/GS]**, NOT [EmuCore].
- `[EmuCore/Speedhacks] EECycleRate` signed -3..+3 (default 0). `[EmuCore] EnableFastBoot` bool. proc: pgrep -x `pcsx2-qt`.

## Supermodel (Model 3) — `~/.supermodel/Config/Supermodel.ini` (custom INI, `[ Global ]` with spaces)
- ALL bools are integer `1`/`0`. Per-game sections precede `[Global]` — MUST scope to `[Global]`.
- `FullScreen` is DUPLICATED in `[Global]` (last-wins) — edit the LAST occurrence.
- `[Global]`: New3DEngine, QuadRendering, WideScreen, Stretch, FullScreen, Throttle, MultiThreaded (all int bool);
  XResolution/YResolution int px; SoundVolume/MusicVolume int 0-200. proc: pgrep -x `supermodel`.
