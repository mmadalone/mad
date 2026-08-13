# BIOS folder — wiring, canonical layout, cleanup log

Source: on-box investigation + validation workflow, 2026-07-28. Root: `~/Emulation/bios` (ext4, 1.2G→732M after cleanup).

## How the BIOS folder is actually consumed (verified)
**RetroArch is NOT pointed at `~/Emulation/bios` in bulk.** The flatpak's `system_directory` is its OWN dir:
`~/.var/app/org.libretro.RetroArch/config/retroarch/system`. EmuDeck **symlinks ~51 individual curated files** from bios INTO it (e.g. `dc/*`, `panafz*.bin`, `syscard*.pce`, `mpr-17933.bin`, `stvbios.zip`, PC-FX, Amiga kick*). **Renaming/moving any of those 51 bios paths breaks RetroArch.** Most bios files are NOT read by RetroArch cores at all.

Standalone emulators read bios DIRECTLY, by hard-coded path:
- **DuckStation** (PS1): scans the bios ROOT for `scph*.bin` (by content hash, not just name).
- **PCSX2** (PS2): `bios/ps2/` (config `Bios = ../../Emulation/bios/ps2`).
- **melonDS** (NDS/DSi): exact files `bios9.bin`, `bios7.bin`, `firmware.bin`, `dsi_bios9.bin`, `dsi_bios7.bin`.
- **xemu** (Xbox): `bios/mcpx_1.0.bin` + `bios/Complex_4627v1.03.bin`.
- **Flycast** (Dreamcast): scans `bios/dc/`.
- **ares** (multi): its `settings.bml` names root `MSX.ROM`/`MSX2.ROM` (a keep-set gap a static symlink scan misses — grep configs too).
- **Standalone MAME** (`org.mamedev.MAME`): `~/.mame/mame.ini` `rompath = roms/arcade ; bios ; bios/mame` (so loose bios `.zip` + `bios/mame/*.zip` ARE on its rompath, currently DORMANT — no romsets installed). Its `hashpath`/`samplepath` point at the **flatpak** (`/app/share/mame/...`), NOT `bios/mame/hash` or `bios/mame2003/samples`.
- **libretro mame cores** read RetroArch's OWN `system/mame2003-plus`, `system/mame2010` (real dirs there), NOT `bios/mame2003*`.

Internal symlinks inside bios (targets must be preserved): `kick13/20/31.rom`, `flycast/bios`→flatpak, `ryujinx/keys`→`~/.config/Ryujinx/system`, `shadps4/sys_modules`, `azahar/keys`.

## Canonical layout rule
The EmuDeck layout is deliberately **mostly FLAT** (console BIOS at root; only `ps2/`, `dc/`, per-emu subdirs). **Do NOT reorganize BIOS into tidy per-system folders** — it breaks the emulators that read fixed paths. "Canonical" = keep the exact filenames each emulator expects + dedupe redundant alternates.

## Ground-truth sources (reuse, don't re-derive)
- **Known-good md5 lists**: `~/.config/EmuDeck/backend/functions/checkBIOS.sh` (PS1, PS2, Saturn, Sega CD, Dreamcast, DS).
- **Per-system bucketing**: `~/Emulation/tools/launchers/lib/bios_map.py` (display buckets for the deck-cloud backup UI).
- **Backup**: deck-cloud granular BIOS category (`~/.config/deck-cloud/bios-plan/` holds a manifest, not a full archive).

## Cleanup — 2026-07-28 (validated by a 27-agent workflow, adversarially verified)
Moved (never deleted) to `~/Downloads/_TMP/20260728-bios-cleanup/` with `RECOVERY.txt`. ~472 MB reclaimed.
- **92 duplicate BIOS** — byte-identical alternate-named copies (canonical/needed name kept). E.g. 37 loose MSX regional ROMs (twins of protected `Machines/`), PC-FX/Saturn/PS1/CD-i/SGB/Atari variants.
- **30 non-BIOS junk** — stray saves (`.mcr`/`.sav`/`.dsv`/`.brm`), backups (`.old`/`.orig`), docs, GameCube DVD firmware, mupen strays. (Save-shaped ones preserved in _TMP.)
- **Extras**: `ps3/PS3UPDAT.PUP` (197M, firmware already installed) + MAME support subtrees `mame/hash`,`mame2003/samples`,`mame2003-plus`,`fba`,`fbneo`,`ume`,`mame2016` (230M, off every read path).
- **PS1 canonical fix**: bad dumps cleared from `scph5502.bin`/`scph7000.bin`; good EU (`scph5552`) → `scph5502.bin`, good JP (`Scph7000[2]`) → `scph7000.bin`.
- **CD-i wiring**: copied `cdimono1.zip` (cdi200/220/220b.rom) into RA system dir so the SAME CDI core has a BIOS.
- Verified after: RA 51/51 symlinks OK, all standalone paths OK, canonical hashes OK, ares MSX preserved.

## Bad dumps — QUARANTINED 2026-07-28 (2nd pass)
23 FLAG_UNKNOWN dumps moved to `_TMP/20260728-bios-cleanup/bad-dumps/` (a hash-recheck guard skipped `scph5502.bin` — it now holds the good renamed EU dump, and ares references it). RA 51/51 symlinks still OK after. A few are possibly-legit variants a user might want back (all recoverable from _TMP): 3DO JP font `panafz1-kanji.bin`, Saturn `multinorm`, Sega-CD model-2 `us_scd2_9303`/`eu_mcd2_9306`, MSX regional `MSXR*/MSXFR/MSXSP/MSX2SE`. The rest are truly corrupt (bad PS1 dumps, truncated `CD-I 205.rom`, `dc_boot2.bin`, padded `kick10.rom`).
Final state: 4962 → 3821 files, 1.2G → 720M.

## Still outstanding
- **Missing (melonDS DSi mode)**: `dsi_bios7.bin`, `dsi_bios9.bin`, `dsi_firmware.bin`, `dsi_nand.bin` — DSi-enhanced titles won't boot; plain NDS is fine. (Copyrighted — user must supply.)
