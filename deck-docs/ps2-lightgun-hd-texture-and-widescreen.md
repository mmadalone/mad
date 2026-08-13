# PS2 lightgun games: HD texture packs + 16:9 patches (his stack)

Research date: 2026-07-01. Two multi-agent web-research+adversarial-verify sweeps (9 games each,
18 agents per sweep). Disc serials VERIFIED on-device via chdman extractcd -> SYSTEM.CNF BOOT2.
Scope = the PS2 GunCon2 lightgun games on his stack (retail `ps2` system + Namco 246/256 `pcsx2x6`).

## Verified disc serials (chdman, 2026-07-01) - ALL match the pack/patch serials
The metadata serials were WRONG for several; these are the real boot serials + CRCs:
- Dino Stalker (USA)                 = SLUS-20485  CRC 3FBF0EA6
- Resident Evil: Dead Aim (USA)      = SLUS-20669  CRC FBB5290C
- RE Survivor 2 Code Veronica (PAL)  = SLES-50650  CRC CD3ED649   (NOT SLES-50721)
- Time Crisis: Crisis Zone (USA)     = SLUS-20927  CRC E0D4421A   (SLUS-21123 = DBZ Budokai 3)
- Time Crisis 3 (USA)                = SLUS-20645  CRC 7290669C   (NOT SLUS-20351)
- Vampire Night (USA)                = SLUS-20221  CRC 7FBCDA34   (SLUS-20268 = SW Racer Revenge)
Namco pcsx2x6 (arcade, PS2 hardware): NM00003 Vampire Night, NM00012 TC3, NM00032 TC4 (Super256).

## HD texture packs (PCSX2 texture replacement = folder of PNGs by hash, load via Graphics > Texture Replacement > Load Textures)
Install to ~/.config/PCSX2/textures/<SERIAL>/replacements/  (pcsx2x6 -> ~/.config/PCSX2x6/textures/).
- Dino Stalker (USA): YES. archive.org collection `pcsx2-hd-texture-packs` -> "Dino Stalker (USA)
  [SLUS-20485] HD Remaster.rar" (1.2 GB). Bulk AI upscale. Also GBAtemp thread 679381 (mvp899).
- RE: Dead Aim (USA): YES. archive.org same collection -> "Resident Evil Dead Aim (USA) [SLUS-20669]
  HD Remaster.rar" (2.1 GB). Original = GBAtemp 671142 "Dead Aim Remastered Project" by ewgeha
  (Yandex). Named-author, better quality; fonts/menu/map stay low-res.
- RE Survivor 2 (PAL SLES-50650): YES. archive.org -> "...[SLES-50650] HD Remaster.rar" (~943 MB).
  Original = GBAtemp 659128 by SomberTwilight (MediaFire), bundles extra costumes + 2 cheats.
- Time Crisis 3 (USA SLUS-20645): YES. archive.org -> "Time Crisis 3 (USA) [SLUS-20645] HD
  Remaster.rar" (~408 MB, 3378 files, bulk AI upscale). Also a hand-made pack by Bl4ckH4nd
  (~$5 Patreon, ~383 MB) - higher effort but paywalled.
- Vampire Night (USA SLUS-20221): YES, verified live 2026-07-01. GBAtemp 643864 (thecoolpup) ->
  pixeldrain SLUS-20221.zip (431 MB) + Dropbox backup.
- Time Crisis: Crisis Zone (USA): pack EXISTED (GBAtemp 643871, thecoolpup, Dec 2023) but the
  download link is DEAD; 4 reupload requests 2024-2025 unanswered. NOT obtainable. Do NOT substitute
  the archive.org TC2/TC3 packs (different games/CRCs, won't load).
- Arcade VN (NM00003) + TC3 (NM00012): only the RETAIL packs exist; won't load on the arcade build
  (different serial + texture hashes). No arcade pack.
- Time Crisis 4 (NM00032): NO pack anywhere (arcade-only + PS3 port; no PS2 sibling to borrow from).
archive.org collection root: https://archive.org/details/pcsx2-hd-texture-packs
Reality check: the "HD Remaster" collection packs are batch ESRGAN upscales of the game's own
textures (sharper, not transformative). On a lightgun game, 3x-6x internal resolution is the bigger
win and does NOT affect aim.

## 16:9 widescreen patches (PCSX2 .pnach by CRC; enable via "Widescreen Patches" in game Properties)
Source = official PCSX2/pcsx2_patches repo (the built-in DB). ALL retail games have a region-matched
patch. BUT for a lightgun rig 16:9 is mostly a TRAP: FOV hack widens the image while hit-detection
stays 4:3 -> gun aims off (worst at edges). AIM verdicts:
- Vampire Night (SLUS-20221_7FBCDA34): patch UNIQUELY includes crosshair/aim fixes -> likely KEEPS
  aim. THE one worth using in 16:9. Recalibrate in-game once. (Also bundles Remove Blackbars +
  No-GunCon-Flash options.)
- Dino Stalker (SLUS-20485_3FBF0EA6): real, but PCSX2 GunCon2 report #7619 says "does not work
  properly with widescreen" -> likely breaks aim.
- RE: Dead Aim (SLUS-20669_FBB5290C): real ("hor fov" hack), aim untested, standard risk.
- RE Survivor 2 (SLES-50650_CD3ED649, ElHecht): real, but game rated BAD for GunCon2 even at 4:3;
  16:9 makes horizontal overshoot worse.
- Crisis Zone (SLUS-20927_E0D4421A): real; game already has a 4:3 centering bug. Patch file ALSO
  bundles a "No GunCon Flash" block - keep it OFF with a real gun.
- Time Crisis 3 (USA): ready-made pnach is PAL-only (FD32030F, DieSkaarj). USA needs a hand-built
  file: `patch=1,EE,0033976c,word,3f400000` saved as 7290669C.pnach. Community-tagged "needs aim fix".
- Arcade TC3 (NM00012): a real arcade byte-patch exists (arcade-projects #35678, byte 0x80->0x40) but
  author confirms "the aim is off". Not usable for the rig.
- Arcade VN (NM00003): no verified arcade patch (retail ones are wrong build). TC4 (NM00032): none.

## Bottom line / recommendation
Texture packs don't touch aim -> install freely. Keep every game at native 4:3 for accurate
Sinden/GunCon2 aim; get sharpness from 3x-6x internal resolution. Only Vampire Night is a good 16:9
candidate (aim-aware patch). Crisis Zone texture pack is the only real gap (dead link).

## APPLIED 2026-07-01: Vampire Night textures + 16:9 on pcsx2x6-retail (the recipe, reusable)
His retail GunCon2 games run on the pcsx2x6 fork, NOT stock PCSX2. Launch (from es_systems.xml):
  pcsx2x6.AppImage -datapath /home/deck/Applications/pcsx2x6-retail -batch -fullscreen -- %ROM%
The fork uses `<datapath>/PCSX2x6/` as the data root, so the ACTIVE data dir is
`~/Applications/pcsx2x6-retail/PCSX2x6/` (portable; folders bios/textures/patches/cheats/gamesettings).
- pnach location = `patches/` NOT `cheats/` (verified in fork src pcsx2/Patch.cpp: FindPatchFilesOnDisk
  uses `cheats ? Cheats : Patches`; ReloadPatches passes cheats=false; cheats/ is only for EnableCheats
  cheat codes). Filenames accepted: `SLUS-20221_7FBCDA34.pnach` OR `7FBCDA34.pnach` (both patterns globbed).
- Widescreen is enabled PER-GAME via the modern `[Patches] Enable = <label>` mechanism, mirroring the
  working reference `~/.config/PCSX2/gamesettings/SCES-51428_29B5FDB9.ini`. NOT the global
  EnableWideScreenPatches (keep 4:3 default so other lightgun games' aim stays correct).
- CRC = PCSX2 elfCRC = XOR of all 32-bit LE words of the boot ELF. Verified VPN disc = SLUS_202.21 ->
  7FBCDA34 (script: scratchpad/elfcrc.py, extract ELF from chdman bin). TC3 retail on same install =
  7290669C (from a savestate name) confirming the fork computes canonical CRCs.
What was installed (all keyed to SLUS-20221 / CRC 7FBCDA34):
  patches/SLUS-20221_7FBCDA34.pnach  = official ElHecht aim-corrected 16:9 (verbatim; has Widescreen 16:9
    + Remove Blackbars + No GunCon Flash blocks). Source: raw.githubusercontent.com/PCSX2/pcsx2_patches
    /main/patches/SLUS-20221_7FBCDA34.pnach
  gamesettings/SLUS-20221_7FBCDA34.ini = [Patches] Enable = Widescreen 16:9 (+ No GunCon Flash, user's
    choice, cosmetic) ; [EmuCore/GS] AspectRatio=16:9, FMVAspectRatioSwitch=16:9, LoadTextureReplacements=true
  textures/SLUS-20221/replacements/  = 4045 .dds files, 1.4G (pack sha256 829165b4..0293, verified)
FALLBACK if 16:9 does not engage on-device: add `[EmuCore]` + `EnableWideScreenPatches = true` to the ini.
OWED on-device (headless can't test): launch VPN, confirm wider 16:9 + sharper textures, RE-RUN in-game
gun calibration (aspect changed) and check Sinden aim.
pixeldrain download gotcha: pixeldrain.com is PROBABILISTICALLY reset (SNI DPI, ~1/3 TLS connects) AND
DNS returns unroutable IPv6 -> use `curl -4 --retry 999 --retry-all-errors ...` (or --resolve to the v4 IP).
His pixeldrain Pro API key: ~/.claude/tokens/pixeldrain.md (auth via `curl -u :$KEY`); not needed for public
files but harmless. The Dropbox reupload (dorkasaurusrex, rlkey in gbatemp 643864) is a DIFFERENT build
(532MB, hash won't match) - prefer pixeldrain for the exact hashed file.

## APPLIED 2026-07-01 (part 2): the other 4 retail games on pcsx2x6-retail
All disc CRCs verified via elfcrc: Dino Stalker SLUS-20485/3FBF0EA6, Dead Aim SLUS-20669/FBB5290C,
RE Survivor2 SLES-50650/CD3ED649, TC3 SLUS-20645/7290669C, Crisis Zone SLUS-20927/E0D4421A.
TEXTURES installed (from archive.org pcsx2-hd-texture-packs .rar; rar layout = `<longname>/<SERIAL>/
replacements/`, so lift the SERIAL dir into textures/): SLUS-20485 (4992 files), SLUS-20669 (1161),
SLES-50650 (2992 + a COSTUMES/ extra-outfits dir, not auto-loaded), SLUS-20645 (3378). Total incl VPN = 13G.
Crisis Zone = NO texture pack (dead link). Each textured game got a texture-ONLY per-game ini
(`[EmuCore/GS] LoadTextureReplacements=true`, no aspect change -> stays 4:3, aim intact).
16:9 = applied to NONE of these (user: don't apply where it breaks; all break aim; only VPN's patch is
aim-corrected). BUT all 6 official/hand-built 16:9 pnachs are STAGED DISABLED in patches/ for experimenting
(fetched from PCSX2/pcsx2_patches; TC3 USA hand-built from PS2-HOME code 2033976c 3f400000; all verified 0
unlabelled auto-apply patches). To experiment: add `[Patches] Enable = Widescreen 16:9` + `[EmuCore/GS]
AspectRatio = 16:9` to that game's gamesettings ini (expect aim off). Dead Aim pnach also has No-Interlacing;
Survivor2/CrisisZone have No GunCon Flash - independently toggleable. OWED on-device: visual check only.

## PCSX2 data-path map (2026-07-01, resolves the "too many folders" confusion)
THREE emulators, folder decided at launch:
- STOCK PCSX2 (regular PS2 games): bin ~/Applications/pcsx2-Qt.AppImage, non-portable -> config
  ~/.config/PCSX2/inis/. [Folders] redirect data OUT (EmuDeck split): Textures =
  ~/Emulation/storage/pcsx2/textures ; MemoryCards ~/Emulation/saves/pcsx2 ; Bios ~/Emulation/bios/ps2.
  NOTE: ~/.config/PCSX2/textures is NOT read (Folders points to storage).
- pcsx2x6 ARCADE (Namco 246/256): bin ~/Applications/pcsx2x6/pcsx2x6.AppImage launched `-portable`
  -> data ~/Applications/pcsx2x6/PCSX2x6/ (self-contained).
- pcsx2x6 RETAIL (GunCon2 discs): same bin, `-datapath ~/Applications/pcsx2x6-retail` -> data
  ~/Applications/pcsx2x6-retail/PCSX2x6/ (the lightgun textures/patches above live here).
RETIRED 2026-07-01 (moved to ~/Downloads/_TMP-pcsx2-cleanup-20260701-112812/, RECOVERY.txt there):
  ~/.config/PCSX2x6 (was the BARE-launch config), ~/.config/PCSX2.new (year-old test),
  ~/Applications/pcsx2x6/inis (pre-portable). The two app-menu .desktop entries ("pcsx2x6 Arcade" =
  ~/.local/share/applications/pcsx2x6.desktop, "pcsx2x6 Retail" = pcsx2x6-2.desktop) were fixed to
  launch with `-datapath /home/deck/Applications/pcsx2x6[-retail]` (ABSOLUTE path: a .desktop Exec is
  not shell-parsed, so `~` does NOT expand). Now app-menu + ES-DE share one config per mode, and
  nothing launches pcsx2x6 bare, so ~/.config/PCSX2x6 stays unused.

## The Warriors (SLUS-21215) stock-PCSX2 texture merge (2026-07-01)
Fixed 24 stock-PCSX2 HD packs stranded in ~/.config/PCSX2/textures (unread) -> moved to
~/Emulation/storage/pcsx2/textures. The Warriors had two copies: storage=DDS 4310 (hi-res up to 4K,
= the eternights GitHub Xbox-port set at higher res; the 145MB GitHub pack is a strict downgrade,
0 new) and .config=PNG 4819 (+537 more coverage, ~Simon's Enhanced HD). MERGED the 537 PNG-only into
the DDS pack (PCSX2 loads mixed DDS+PNG) -> 4847 = most complete possible. Reversible: manifest +
log + revert.sh in ~/Emulation/storage/pcsx2/_texture-merge-logs/ ; source .config PNG pack intact.
Packs load by SERIAL folder (CRC-independent); loader picks by final ext (png+dds both registered).
