# ES-DE emulator find rules (es_find_rules.xml)

Source: ES-DE USERGUIDE — https://gitlab.com/es-de/emulationstation-de/-/raw/master/USERGUIDE.md
Cross-checked against live rules in `~/Applications/ES-DE-MAD.AppDir/usr/share/es-de/resources/systems/linux/es_find_rules.xml`.
Date cached: 2026-07-11

## How it works
- `es_systems.xml` references an emulator as `%EMULATOR_NAME%`; the actual "what to look for and where"
  is resolved by `es_find_rules.xml`. `%CORE_x%` (libretro) is separate.
- Each `<emulator name="X">` holds an ordered list of `<rule type="...">` blocks, each with `<entry>` lines.
- **First match on disk wins.** Rules parsed **top-to-bottom in document order** (userguide: "the staticpath
  rules are always parsed in the order they are defined, and as the AppImage entries are listed above the
  Flatpak entries, these will be searched first").
- Effective priority as bundled: `systempath` (PATH binary) → AppImage in ~/Applications → Flatpak export.

## Rule types
- `systempath` — bare binary name searched on `$PATH` (also finds flatpak-exported app-ids, e.g. `org.libretro.RetroArch`).
- `staticpath` — literal path / glob. Three flavours seen:
  - AppImage glob: `~/Applications/ares*.AppImage`
  - native folder binary: `~/Applications/redream/redream`
  - flatpak export: `/var/lib/flatpak/exports/bin/<app-id>` (optional `|flatpak run --command=... <app-id>`).
- Custom vs bundled merge = **WHOLE-EMULATOR OVERRIDE, FIRST FILE WINS** (NOT a per-entry merge — the userguide's
  "complement/merge" wording is misleading; verified in OUR source `es-app/src/SystemData.cpp`):
  - `loadFindRules()` (SystemData.cpp:43-82) builds `paths = [custom, bundled]` — the custom file
    (`getAppDataDirectory()+"/custom_systems/es_find_rules.xml"` = `~/ES-DE/custom_systems/…`) is added FIRST (:49),
    bundled linux file appended SECOND (:82), then looped in that order.
  - If an emulator name is already in `mEmulators`, the later (bundled) block is SKIPPED ENTIRELY (`continue`, :122-128).
  - ⇒ a custom `<emulator>` block WHOLLY REPLACES the bundled one; bundled AppImage/Flatpak/PATH entries are
    DISCARDED, not appended. A custom block with one bad path = that emulator has NO fallback.
  - ES-DE reads ONLY `~/ES-DE/custom_systems/es_find_rules.xml`. EmuDeck's
    `~/.config/EmuDeck/backend/configs/emulationstation/custom_systems/es_find_rules.xml` is a TEMPLATE dir that
    ES-DE never reads directly.
- Order emulator paths are tried (FileData.cpp): `systemPaths` first (:2519), then `staticPaths` (:2560) — i.e.
  PATH binary before AppImage/folder/flatpak. Within each, document order, first match wins.

## What ES-DE expects per (Linux) emulator — native binary vs AppImage vs Flatpak
So "does ES-DE want a native app or an AppImage?" is PER-EMULATOR, not global:
- Accept ALL forms (PATH / AppImage / Flatpak): RETROARCH, DUCKSTATION, FLYCAST, PCSX2, SUPERMODEL, ARES.
- **AppImage-only**: JGENESIS (`~/Applications/jgenesis-cli*.AppImage`).
- AppImage OR native folder OR PATH: MESEN (`~/Applications/Mesen*.AppImage` | `~/Applications/Mesen/Mesen` | `mesen2`).
- **native-binary-only** (NO AppImage): REDREAM (`~/Applications/redream/redream`), KRONOS (`~/Applications/kronos/kronos`),
  GEARGRAFX (`~/Applications/geargrafx/geargrafx`).
- native folder / PATH / Flatpak, NO AppImage: YMIR (`~/Applications/ymir/ymir-sdl3` | flatpak `io.github.strikerx3.ymir`).
- PATH or Flatpak only (NO AppImage, NO ~/Applications binary): MEDNAFEN (`mednafen` | flatpak Mednaffe
  `com.github.AmatCoder.mednaffe` via `flatpak run --command=mednafen`).

EmuDeck convention on this Deck: standalone emus installed as AppImages in `~/Applications/` (DuckStation, Cemu,
Citron, etc.). Native-only emus (redream/kronos/geargrafx/ymir) need the extracted folder-binary layout instead.

## This Deck's actual state (verified 2026-07-11)
- ACTIVE file `~/ES-DE/custom_systems/es_find_rules.xml` overrides ONLY: CITRON, EDEN, PCSX2X6 (our custom emus).
  Everything else (RetroArch, DuckStation, Flycast, Supermodel, and the flagged standalones) uses BUNDLED rules.
- EmuDeck's BACKEND TEMPLATE `~/.config/EmuDeck/backend/configs/…/custom_systems/es_find_rules.xml` DOES contain
  overrides pointing at `/run/media/mmcblk0p1/Emulation/tools/launchers/*.sh` (RETROARCH/DUCKSTATION/FLYCAST/
  SUPERMODEL). This Deck's SD mount is `/run/media/deck/1tbDeck` so those paths are dead — BUT this template is
  NOT the file ES-DE reads, so it has zero effect. (NB: were those entries ever copied into the active
  `~/ES-DE/custom_systems/` file, they would REPLACE — not fall through to — the bundled rules and break those
  emulators, per the whole-emulator-override semantics above.)
