# Eden (Nintendo Switch, Yuzu fork) config - MAD notes

Source: Eden mirror github.com/eden-emulator/mirror (branch master), fetched 2026-07-04.
(The official git.eden-emu.dev returns HTTP 403 to WebFetch/API - use the GitHub mirror.)
Cross-checked against the live /home/deck/.config/eden/qt-config.ini on this Deck.
Config file: ~/.config/eden/qt-config.ini (Qt SimpleIni). Per-game: ~/.config/eden/custom/<TITLEID>.ini.
Load dir (mods/cheats): ~/.local/share/eden/load/<TitleID-HEX>/. Input profiles: ~/.config/eden/input/*.ini.

## The `\default` twin (CRITICAL, verified in src/frontend_common/config.cpp)

`Config::ReadSettingGeneric` reads a `key\default` bool that DEFAULTS TO TRUE when absent. If it is
true, Eden calls `LoadString("")` = reset the setting to its compiled default and IGNORE the
`key=value` line entirely. The value is honored ONLY when `key\default=false`. This runs for the
GLOBAL config too. So any writer that changes a value MUST also set `key\default=false`, else the
change is silently reverted on Eden's next load. Eden's own `WriteSettingGeneric` always writes the
twin (false only when the value differs from the compiled default).

Consequence: MAD's `eden_settings._yuzu_write` sets BOTH `key=value` AND `key\default=false` (mirrors
citron_settings). Per-game writes use the triple `key\use_global=false` + `key\default=false` + value
(yuzu_pergame). The old flat eden.get/set used plain cfgutil.ini_replace (no twin flip) = a
silent-discard bug for every non-default write; removed in the tile expansion.

## Enum index tables (src/common/settings_enums.h; index == stored integer)

These DIVERGE from Citron and from stock yuzu - build Eden descriptors from THESE, not Citron's.

- RendererBackend `backend`: 0 OpenGL(GLSL), 1 Vulkan, 2 Null, 3 OpenGL(GLASM), 4 OpenGL(SPIR-V)
- ResolutionSetup `resolution_setup` (13): 0 0.25x, 1 0.5x, 2 0.75x, 3 1x(720p native), 4 1.25x,
  5 1.5x(1080p), 6 2x(1440p), 7 3x, 8 4x, 9 5x, 10 6x, 11 7x, 12 8x
- ScalingFilter `scaling_filter` (15): 0 NearestNeighbor, 1 Bilinear, 2 Bicubic, 3 Gaussian,
  4 Lanczos, 5 ScaleForce, 6 Fsr(AMD FSR), 7 Area, 8 ZeroTangent, 9 BSpline, 10 Mitchell,
  11 Spline1, 12 Mmpx, 13 Sgsr, 14 SgsrEdge
- AntiAliasing `anti_aliasing`: 0 None, 1 Fxaa, 2 Smaa
- AspectRatio `aspect_ratio`: 0 16:9, 1 4:3, 2 21:9, 3 16:10, 4 Stretch
- VSyncMode `use_vsync`: 0 Immediate(Off), 1 Mailbox, 2 Fifo(On), 3 FifoRelaxed
- FullscreenMode `fullscreen_mode`: 0 Borderless, 1 Exclusive
- GpuAccuracy `gpu_accuracy`: 0 Low, 1 Medium, 2 High   (renamed vs yuzu Normal/High/Extreme)
- AnisotropyMode `max_anisotropy` (9): 0 Automatic, 1 Default, 2 X2, 3 X4, 4 X8, 5 X16, 6 X32,
  7 X64, 8 None
- AstcRecompression `astc_recompression`: 0 Uncompressed, 1 Bc1, 2 Bc3
- AstcDecodeMode `accelerate_astc`: 0 Cpu, 1 Gpu, 2 CpuAsynchronous
- NvdecEmulation `nvdec_emulation`: 0 Off, 1 Cpu, 2 Gpu
- VramUsageMode `vram_usage_mode`: 0 Conservative, 1 Aggressive
- DmaAccuracy `dma_accuracy`: 0 Default, 1 Unsafe, 2 Safe
- FramePacingMode `frame_pacing_mode`: 0 Auto, 1 30, 2 60, 3 90, 4 120
- ExtendedDynamicState `dyna_state`: 0 Disabled, 1 EDS1, 2 EDS2, 3 EDS3
- SpirvOptimizeMode `optimize_spirv_output`: 0 Never, 1 OnLoad, 2 Always
- GpuUnswizzleSize `gpu_unswizzle_texture_size`: 0 VerySmall, 1 Small, 2 Normal, 3 Large, 4 VeryLarge
- GpuUnswizzle `gpu_unswizzle_stream_size` / GpuUnswizzleChunk `gpu_unswizzle_chunk_size`:
  0 VeryLow, 1 Low, 2 Normal, 3 Medium, 4 High
- CpuAccuracy `cpu_accuracy` (5): 0 Auto, 1 Accurate, 2 Unsafe, 3 Paranoid, 4 Debugging
- CpuBackend `cpu_backend`: 0 Dynarmic, 1 Nce   (NCE is ARM-only; Deck is Dynarmic-locked -> not offered)
- MemoryLayout `memory_layout_mode`: 0 4GB, 1 6GB, 2 8GB, 3 10GB, 4 12GB
- ConsoleMode `use_docked_mode`: stored as INTEGER 0 Handheld / 1 Docked (NOT true/false)
- Region `region_index`: 0 Japan, 1 Usa, 2 Europe, 3 Australia, 4 China, 5 Korea, 6 Taiwan
- AudioMode `sound_index` (in [System]): 0 Mono, 1 Stereo, 2 Surround
- Language `language_index` (20): 0 Japanese, 1 EnglishAmerican, 2 French, 3 German, 4 Italian,
  5 Spanish, 6 Chinese, 7 Korean, 8 Dutch, 9 Portuguese, 10 Russian, 11 Taiwanese, 12 EnglishBritish,
  13 FrenchCanadian, 14 SpanishLatin, 15 ChineseSimplified, 16 ChineseTraditional,
  17 PortugueseBrazilian, 18 Polish, 19 Thai
- AudioEngine `output_engine`: hand-written enum {0 Auto, 1 Cubeb, 2 Sdl3, 3 Null, 4 Oboe}.
  Live file stores an INTEGER (`output_engine=0`), so MAD treats it as write_mode "index" (NOT the
  stale string list "auto/cubeb/sdl2/.." the old eden_cmds.py used; note SDL is v3, not v2).

Bools are lowercase `true`/`false`, EXCEPT use_docked_mode (integer 1/0). shader_backend (live=2) has
NO enum in settings_enums.h (video_core concept) - not offered, like Citron.

## Tab -> INI section + representative keys (Category != INI section)

- General: [Core] use_multi_core, use_speed_limit, speed_limit, sync_core_speed, memory_layout_mode;
  [UI] exit/confirm toggles
- System: [System] language_index, region_index, custom_rtc_enabled, rng_seed_enabled, device_name,
  use_docked_mode, sound_index, disable_nca_verification
- CPU: [Cpu] cpu_backend, cpu_accuracy, cpu_debug_mode, cpuopt_* (~20), use_fast_cpu_time
- Graphics (Category Renderer): [Renderer] backend, resolution_setup, scaling_filter, anti_aliasing,
  aspect_ratio, fullscreen_mode, use_disk_shader_cache, use_asynchronous_gpu_emulation, nvdec_emulation,
  fsr_sharpening_slider
- Advanced Graphics (Category RendererAdvanced): [Renderer] gpu_accuracy, use_vsync, max_anisotropy,
  astc_recompression, accelerate_astc, vram_usage_mode, use_reactive_flushing, use_fast_gpu_time,
  optimize_spirv_output, dma_accuracy, frame_pacing_mode
- GPU extensions (Categories RendererExtensions + RendererHacks): [Renderer] provoking_vertex,
  descriptor_indexing, sample_shading, dyna_state, vertex_input_dynamic_state, sync_memory_operations,
  skip_cpu_inner_invalidation, disable_shader_loop_safety_checks, enable_raii, disable_buffer_reorder,
  enable_buffer_history; hacks use_asynchronous_shaders, rescale_hack, fix_bloom_effects, emulate_bgr565,
  gpu_unswizzle_enabled/_texture_size/_stream_size/_chunk_size. (The tab iterates the two Categories,
  so its key set = whatever carries those tags - MAD only offers keys present in the live [Renderer].)
- Audio: [Audio] output_engine, volume (0-200), audio_muted, muteWhenInBackground; [System] sound_index
- Linux: [Linux] enable_gamemode (Feral GameMode); [UI] gui_force_x11

LANDMINE: `enable_gamemode` appears in BOTH [Linux] and [UI] in the live file. A section-aware write
must target [Linux] (the functional one). cfgutil is section-aware, so descriptors set section="Linux".

## Hotkeys store (nested, verified on disk)

[UI] `Shortcuts\<Group>\<Action>\KeySeq` (keyboard) + `\Controller_KeySeq` (Switch tokens) + `\default`
twins; group/action names URL-encoded (Main%20Window). Same shape as Citron -> eden_hotkeys_cmds is a
faithful clone of citron_hotkeys_cmds pointed at Eden's config.
