# PCSX2 — Steam Deck notes

## Texture replacement: async vs precache (stutter cause + fix)
Source: PCSX2 official HD-textures tutorial (sites.google.com/view/pcsx2-hd-textures-project/tutorial),
GitHub issues PCSX2/pcsx2 #10832, #5578, #7546; ncaanext Steam Deck guide. Read 2026-06-08.
- LoadTextureReplacements: master ON/OFF for replacing game textures from textures/<serial>/replacements.
- LoadTextureReplacementsAsync (default ON): loads each replacement on a worker thread the first time
  that texture appears IN-GAME. Removes the long startup load, but causes a one-time hitch per new
  texture (disk read + decode + VRAM upload mid-frame). Worse from SD card and with big packs.
- PrecacheTextureReplacements: loads the WHOLE pack into RAM at game boot. Eliminates per-texture
  in-game hitching at the cost of a longer load and RAM use ~= pack size on disk. Recommended when you
  have RAM headroom and want smooth frametimes (Deck has 16GB). Async+Precache are usable together;
  precache is what actually kills the on-demand stutter.
- Hash-cache VRAM cap (#5578): very large/ high-res packs at high upscale could overflow the GS hash
  cache -> "Hash cache has used (xxx) MB of VRAM, disabling" and textures stop replacing. PR #7546
  later stopped custom textures from being counted toward hash-cache overflow, so modern builds handle
  big packs better, but high upscale + 2K textures + UMA still pressures VRAM on the Deck.
- Deck/SteamOS gotcha (#10748): texture replacements render in poorer quality under Vulkan/Auto on
  Steam Deck; OpenGL shows them correctly. Renderer=-1 means Auto.

## 007 From Russia with Love (SLUS-21282)
Source: PCSX2 wiki From_Russia_With_Love, GitHub #12524 (lens flare), #3951, #2877. Read 2026-06-08.
- Known perf quirk: heavy LENS FLARE scenes tank GPU/framerate; it is a GS blending cost, not fixable
  by upscale changes. #12524 closed "not planned". Lowering Blend Accuracy (Full->High, or to Basic)
  and lowering upscale reduce the blend pixel cost. Skipdraw can hide flares but risks other effects.
- Community 2x upscale is the comfortable target on Deck-class GPUs; 3x is heavier, esp. with blending.

## EmuDeck shipped defaults (the real Deck baseline) — added 2026-06-08
Source: https://github.com/dragoonDorise/EmuDeck/blob/main/configs/pcsx2qt/.config/PCSX2/inis/PCSX2.ini
- Renderer = 14 (Vulkan, explicit not -1); upscale_multiplier = 1 (NATIVE, not 3x);
  accurate_blending_unit = 1 (Basic); MaxAnisotropy = 0 (off); mipmap = true;
  EECycleRate = 0, EECycleSkip = 0 (no cycle hacks); vuThread = true (MTVU ON);
  HWDownloadMode = 0 (Accurate); EnableFastBoot = true; VsyncEnable = 0; paltex = false.
- EnableThreadPinning NOT written by EmuDeck (engine default = OFF).

## Renderer / upscale / aniso / blending / cycle hacks / thread pinning — added 2026-06-08
- Renderer: Vulkan is the Deck/Linux renderer (enum 14). -1 Automatic resolves to Vulkan on Linux,
  so -1 and 14 behave identically on Deck; 14 just removes auto-pick ambiguity. NOTE conflict with
  the texture-replacement finding above: replacements look worse under Vulkan/Auto, correct under
  OpenGL — relevant only for the SLUS-21282 pack, not for non-replacement games.
  Sources: SteamDeckHQ PCSX2 2.0; EmuDeck wiki; RetroGameCorps.
- Upscale: RetroGameCorps recommends 3x ("supersampling, just about every game plays great").
  SteamDeckHQ: handheld panel ~720p so 2x is the sweet spot, >2x wasted in handheld. So 3x = docked
  1080p, 2x = handheld efficiency. Main perf knob; per-game it. https://retrogamecorps.com/2022/10/16/steam-deck-emulation-starter-guide/
- Aniso: RGC recommends 8x; 16x adds ~no IQ over 8x but more LPDDR5 bandwidth → 8x is best value.
- Blending: official guide "increment until problem goes away; higher substantially increases perf
  requirements." Levels 0 Min/1 Basic/2 Med/3 High/4 Full. Basic global default; raise PER-GAME only.
- EE Cycle Rate/Skip: RISKIEST correctness knobs. Underclock/skip → audio desync, physics oddities,
  cutscene crashes (SotC crash at moderate skip #4662; Time Crisis II #3620; PoP #4110). Leave 0/0
  global; per-game only, smallest reduction, recheck audio+physics. Global use = cargo-cult.
- HWDownloadMode: 0 Accurate is correctness-safe; disabling breaks framebuffer-readback effects.
  Keep Accurate global; disable per-game only for readback-bound titles that tolerate it.
- Thread pinning: pins EE/VU/GS to most performant cores within SAME cluster (avoids cross-cluster).
  Deck Van Gogh = single uniform 4c/8t Zen2 cluster so cross-cluster risk is moot. With MTVU on,
  ~5 hot threads on 4 cores/8 threads can fight SteamOS scheduler/SMT. EXPERIMENTAL: try ON, measure
  frametimes, revert if no gain. Not a guaranteed win on Deck.
- Shader cache: DisableShaderCache must stay FALSE — cache kills first-encounter Vulkan PSO stutter.

## pcsx2x6 — proverb.elf, .acgame, and the System246 boot chain (researched 2026-06-22)

Sources (all fetched): pcsx2x6 site game_config + landing (ps2homebrew-arcade.github.io/pcsx2x6/),
GitHub PS2Homebrew-arcade org (proverb / pcsx2x6 / pcsx2-coh / biosdrain repos),
proverb src/main.c + Makefile + generate_gamepack.py (raw, branch main),
pcsx2x6 GameIndex.yaml (raw, branch devel: bin/resources/GameIndex.yaml).

WHAT proverb.elf IS
- proverb = "alternative bootloader to Sony's PSALM boot.bin for arcade PS2s" (own repo, AFL-3.0, C).
- It is a tiny fixed boot stub. main.c: builds argv[0] = "mc0:" BOOT_PATH, argv[1]="DANGLE",
  reboots IOP w/ embedded ioprp, disables prefix check, loads SIO2MAN + MCMAN (mc0 only),
  fioOpen(argv[0]); if <0 -> "FATAL: cant open" -> return -1; else LoadELFFromFile -> ExecPS2.
  If anything fails it just returns from main and "lets arcade OSDSYS do the error screen for us".
- BOOT_PATH is HARDCODED AT COMPILE TIME via -DBOOT_PATH=\"...\" (Makefile: BOOT_PATH ?= mc0:boot.elf,
  EE_CFLAGS += -DBOOT_PATH). So proverb is generic CODE but each built binary embeds one boot path.

WHY per-game proverb.elf DIFFER (md5)
- proverb/generate_gamepack.py downloads pcsx2x6's GameIndex.yaml and, per gameid, runs
  `make ... BOOT_PATH=<bootprog> BINDIR=bin/<gameid>` -> a separate proverb.elf per game with a
  different hardcoded boot-program name baked in -> different binary -> different md5. (Also writes
  bin/<gameid>/title.txt with GameID/Title/BootProg.) This is the "well organized structure" the
  docs mention. NOT per-disc data; just a different embedded bootprog string.

GameIndex.yaml bootprog field = the Namco loader program name ON THE DONGLE (mc0), e.g.
  NM00001 Ridge Racer V = START, NM00004 Tekken4 = TK4LOAD, NM00012 TimeCrisis3 = TC3LOAD,
  NM00021 Cobra = CBRLOAD. proverb does mc0:<bootprog>.

THE NM00003 TRAP (the Vampire Night problem)
- GameIndex.yaml: NM00003 ACTIVE = "Technic Beat", bootprog: VPNGAME. Directly below, COMMENTED OUT:
  #NM00003 name: Vampire Night bootprog: VPNGAME. So NM00003 was reassigned Vampire Night->Technic
  Beat but kept Vampire Night's bootprog (VPN = VamPire Night). There is NO active Vampire Night entry.
- CONSEQUENCE: Technic Beat's proverb.elf and (the would-be) Vampire Night's proverb.elf are IDENTICAL
  (same BOOT_PATH=VPNGAME). So reusing NM00003's proverb.elf for Vampire Night is, for the boot-path
  lookup alone, harmless/correct — the embedded path is mc0:VPNGAME which IS Vampire Night's loader.

DONGLE FORMAT (verified on the curated dongles in ~/Downloads/_NEW/dongles)
- All 8650752 bytes = 8MiB + 16-byte ECC/page raw VMC dump; superblock "Sony PS2 Memory Card Format
  1.2.0.0". pcsx2x6 mounts these as McdSlot0 = mc0:. This IS the working geometry (TimeCrisis3/Cobra
  dongles are the same size). "[8MB, Formatted]" in the log is the NORMAL mount line, not an error.
- `strings vnight-vpn3verb.bin` SHOWS VPNGAME, VPN3-B, boot.bin present. So the dongle is a real
  Vampire Night dongle and mc0:VPNGAME EXISTS. The dongle is NOT the problem.

ROOT-CAUSE CORRECTION for the rom0:OSDSYS / "Boot program error / boot file not exist" failure
- Emulog shows proverb (CRC 5CE0E5E7) LOADS + EXECUTES, THEN EELOAD is invoked with rom0:OSDSYS /
  BootBrowser. That means proverb SUCCEEDED (it found+exec'd mc0:VPNGAME); VPNGAME (the real Namco
  loader) RAN and then chained to OSDSYS itself. The OSDSYS fallback is therefore DOWNSTREAM of
  proverb, inside the Namco boot chain — NOT a proverb-can't-find-the-ELF failure and NOT a
  gameid/proverb.elf mismatch. The earlier theory (wrong proverb.elf / Technic Beat boot path) is
  WRONG: the boot path coincides (VPNGAME) and proverb ran fine.
- Likely real cause is the LOADER->GAME handoff: VPNGAME expects the game executable/data where it
  isn't (disc media path/device, or argv1 DANGLE-vs-dev-flash, or the disc image type), so the Namco
  loader bails to OSDSYS. Next step is to investigate the .acgame media/elf wiring and what VPNGAME
  looks for on the disc — NOT to swap proverb.elf or the dongle. (Vampire Night isn't in the
  template precisely because it isn't a known-good boot in pcsx2x6.)
