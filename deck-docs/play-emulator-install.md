# Play! emulator (jpd002/Play-) — install on Steam Deck / SteamOS 3.7

Sources fetched 2026-06-22:
- Flathub app page: https://flathub.org/en/apps/org.purei.Play
- Flathub manifest: https://github.com/flathub/org.purei.Play/blob/master/org.purei.Play.yaml
- Official downloads: https://purei.org/downloads.php
- GitHub repo/README: https://github.com/jpd002/Play-
- Batocera wiki (uses Play! for namco2x6): https://wiki.batocera.org/systems:namco2x6
- README via Grokipedia mirror note: last upstream commit ~2026-03-23 (active dev)

## RECOMMENDED INSTALL: Flatpak from Flathub
- App id: `org.purei.Play`
- Command: `flatpak install flathub org.purei.Play`
- Launch binary/command (inside flatpak): `Play`
  - Run from CLI: `flatpak run org.purei.Play`
- Current stable version on Flathub: **0.74** (released ~Feb 2026, "4 months ago" as of 2026-06-22)
- Download size: 9 MiB. Arch: x86_64, aarch64.

### Runtime / glibc caveat — NONE for flatpak
- Runtime: `org.kde.Platform` **6.10** (sdk `org.kde.Sdk`).
- The flatpak bundles its OWN runtime (KDE 6.10 / its own glibc), so the SteamOS host
  glibc 2.41 is IRRELEVANT. This sidesteps the immutable-rootfs + glibc concerns that
  bite raw AppImages/source builds. This is the cleanest path on SteamOS 3.7.

### finish-args (permissions baked into the flatpak)
- `--share=ipc`, `--share=network`
- `--socket=x11`  (X11 — fine in KDE desktop mode / gamescope X)
- `--socket=pulseaudio`
- `--filesystem=home:ro`, `--filesystem=/mnt:ro`, `--filesystem=/media:ro`, `--filesystem=/run/media:ro`
  - NOTE: home is mounted **read-only**. ROMs on the SD card under /run/media are read-only too.
    Play!'s own config/savestate dir lives under the flatpak sandbox
    `~/.var/app/org.purei.Play/` (writable). If ROMs need to be elsewhere, grant extra
    fs perms with `flatpak override --user org.purei.Play --filesystem=...`.
- `--device=all`  → controllers AND mice/lightguns (Sinden presents as a mouse / two
  virtual mice via the smoother) are visible to the sandbox. Good for the gun plan.

## ALTERNATIVE: GitHub Actions nightly artifacts (newer than 0.74)
README: "You can test the latest improvements and bug fixes in the Actions page by
clicking in the build category for your system (Build X), opening the most recent
successful item (workflow run) and downloading the file below the 'Artifacts' field."
- Path: https://github.com/jpd002/Play-/actions → pick the Linux build job → latest green
  run → Artifacts. Arcade (namco2x6) support is actively developed, so a nightly may be
  needed if 0.74 lacks a specific game fix. Nightly Linux artifact is typically an AppImage
  → would then hit the raw-glibc question on SteamOS (verify by running it; flatpak avoids this).
- There is NO GitHub "Releases/latest" tag flow to rely on — downloads come from Actions
  artifacts or Flathub. (gh CLI is NOT installed on this Deck.)

## DOES EMUDECK INSTALL IT? — NO
EmuDeck uses PCSX2 for PS2; it does not install Play!. Play! must be installed manually
(flatpak above). So for the Namco 2x6 arcade pivot, Play! is a manual add, then wire into
ES-DE as a custom system launching `flatpak run org.purei.Play <arcadedef-id or path>`.

## ARCADE (Namco System 246/256) rom layout — Play! native format
Confirmed by Batocera wiki + the on-device romset already at
/home/deck/Downloads/_NEW/Namco2x6Games/:
- `arcadedefs/` dir of `*.arcadedef` JSON (game metadata: id/name/dongle/cdvd/boot/patches).
- Per game: a MAME-named `.zip` (the dongle/security chips, e.g. vnight.zip) +
  a same-named subdir containing the disc CHD (e.g. vnight/vpn1cd0.chd,
  timecrs3/tst1dvd0.chd). e.g. Batocera example: tekken4.zip + tekken4/tef1dvd0.chd.
- **No BIOS required** for namco2x6 in Play! per Batocera ("No Namco System 246/256
  emulator in Batocera needs a BIOS file to run"). (We separately have the r27v1602f.7d
  System 246 BIOS on hand if a future build wants it, but Play! does not require it.)
- README confirms a "Namco System 2x6 Arcade Support" section + light-gun support
  ("Gun Trigger: CIRCLE", "Pedal: TRIANGLE", cursor-based aiming). Good for Vampire Night /
  Time Crisis 3 + the eventual Sinden plan.
## EXACT flatpak paths (RESOLVED 2026-06-22 from source — Play--Framework src/PathUtils.cpp)
- arcadedefs are read from `GetAppResourcesPath()/arcadedefs`. On Linux PathUtils returns
  `/app/share` when it exists (the Flatpak case) → bundled defs live at the read-only
  `/app/share/arcadedefs` INSIDE the flatpak. These are CURRENT/CORRECT (have driver:sys246
  + inputMode:lightgun + screenPosXform). User does NOT touch arcadedefs — the romset's own
  arcadedefs/ folder is STALE (no driver field) and is IGNORED. (Source: ArcadeUtils.cpp
  RegisterArcadeMachines/BootArcadeMachine; a missing "driver" throws "Arcade driver
  unspecified." — confirmed by reading the .cpp.)
- The ROM payload (<id>.zip + <id>/<disc>.chd) goes in arcaderoms under "Play Data Files".
  GetPersonalDataPath() returns $XDG_CONFIG_HOME first; flatpak sets
  XDG_CONFIG_HOME=~/.var/app/org.purei.Play/config (flatpak docs). So the EXACT writable
  leaf is:
    ~/.var/app/org.purei.Play/config/Play Data Files/arcaderoms/
  (arcaderoms is also a configurable pref `ps2.arcaderoms.directory`, settable in
  Settings → Browse Arcade ROMs Dir — use that to point elsewhere, but you must
  `flatpak override --user --filesystem=<dir> org.purei.Play` for non-sandbox dirs since
  home is mounted :ro.)
- CLI: `flatpak run org.purei.Play --arcade vnight --fullscreen` (main.cpp appends
  ".arcadedef"). Bundled defs auto-register at startup → game also appears in the in-app
  list (no manual arcadedef install).

## Performance note
Batocera/README both warn namco2x6 in Play! is "work in progress" and needs a
high-performance system. Steam Deck APU may struggle on some titles; use OpenGL graphics
API (Batocera recommendation). Verify on-device.
