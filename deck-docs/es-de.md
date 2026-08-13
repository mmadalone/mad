# ES-DE (EmulationStation Desktop Edition) — doc findings

Source: official USERGUIDE — https://gitlab.com/es-de/emulationstation-de/-/raw/master/USERGUIDE.md
Read: 2026-06-04.

## ROM scanning gotchas (verified via es_log.txt, 2026-06-13)
- **`noload.txt` in a system's ROM-dir root makes ES-DE skip that ENTIRE system.** Log line:
  `Info: Not populating system "<sys>" as a noload.txt file is present`. EmuDeck drops these in
  subfolders (e.g. naomi/Flycast, saturn/*) to hide them, and intentionally on naomi2 to disable
  it. A stray one at a system root (e.g. left from when the folder was empty) silently hides newly
  added ROMs even with correct extensions. Fix = remove/move the noload.txt, restart ES-DE.
- **A system only appears if ≥1 file matches its `<extension>` list.** Otherwise:
  `Skipping system "<sys>" as no files matched any of the defined file extensions` → system hidden.
- **This install's real ROM dir is `~/ROMs` → symlink → `/run/media/deck/1tbDeck/ROMs` (SD card).**
  `ROMDirectory` in es_settings.xml is BLANK → ES-DE default `~/ROMs`. The `~/Emulation/roms` tree
  is a STALE EmuDeck leftover (old `/run/media/mmcblk0p1/...` metadata) that ES-DE does NOT scan —
  except the m2emu install lives there (see model2 note below). Always place ES-DE ROMs in `~/ROMs/<sys>/`.

## Custom `<command>` emulator resolution — NO leading space before a literal-path emulator (source-verified 2026-06-13)
Verified against ES-DE source `es-app/src/FileData.cpp::findEmulator()` (lines ~2334, ~2615-2660):
- **Method 1** — if the command contains a `%EMULATOR_X%` variable, ES-DE resolves it via
  es_find_rules.xml. This scan is position-independent, so a leading space does NOT matter.
- **Method 2** — if there is NO `%EMULATOR_X%`, ES-DE extracts the emulator from the command thus:
  `if (command.front()=='"')` → read up to the closing quote; `else` → `command.substr(0, command.find(' '))`
  i.e. the **first whitespace-delimited token**. It then checks that file exists.
- **THE GOTCHA:** ES-DE does NOT trim the command (`SystemData.cpp:1069` uses `entry.text().get()`
  with default pugixml flags = no `parse_trim_pcdata`; `command` is never trimmed before findEmulator).
  So a **leading space** after `>` — e.g. `<command label="x"> /path/wrap.sh …` — makes `command.front()`
  a space, `find(' ')` return 0, and `substr(0,0)` = **""** → `Couldn't launch game, emulator not found`
  → on-screen "emulator isn't properly installed", even though the path exists and runs fine in a shell.
- **RULE:** custom commands whose emulator is a literal path (incl. via `controller-router-wrap.sh`)
  must have **NO leading space** after `>` (start directly with the path, or with a `"`quoted path`"`
  AS THE FIRST CHARACTER). `%EMULATOR_X%`-based commands are immune. NOTE: our naomi Supermodel
  commands AND many other custom entries were written with a leading space — only the `%EMULATOR_`
  ones work; literal-path ones (naomi Supermodel) are silently broken by this.

## model2 (Sega Model 2 Emulator / ElSemi m2emu) — wiring (set up 2026-06-13)
- m2emu EXE + working Proton prefix live at `~/Emulation/roms/model2/` (EmuDeck install), but ES-DE's
  model2 ROMs are at `~/ROMs/model2/` (SD). Bridged via `EMULATOR.INI [RomDirs] Dir2=Z:\run\media\deck\1tbDeck\ROMs\model2`
  (Proton maps `Z:` → `/`). ES-DE launches it via custom launcher `~/Emulation/tools/launchers/model2-m2emu.sh`
  (umu/ULWGL-Proton-8.0-5-3, GAMEID=ulwgl-model2), wrapped through controller-router-wrap.sh; defined in
  custom_systems/es_systems.xml (m2emu = default, MAME core = fallback). ES-DE's bundled m2emu find-rule
  (`~/Applications/m2emulator/`) is the WRONG path and unused.

## Launch-command variables
- `%ROM%` — full path to the ROM passed to the emulator.
- `%BASENAME%` — ROM filename without path or extension (e.g. `lair` for `lair.zip` or the
  `lair.daphne` folder).
- `%GAMEDIR%` — the directory the ROM lives in. **For a folder-ROM (directories-interpreted-
  as-files), `%GAMEDIR%` = the folder itself** (so `%GAMEDIR%/%BASENAME%.txt` resolves *inside*
  the folder). For a plain file ROM it's the parent dir.
- `%STARTDIR%=<dir>` — sets the emulator's working directory (e.g. `%STARTDIR%=%GAMEDIR%`).
- `%INJECT%=<file>` — **inserts the raw text CONTENTS of `<file>` into the launch command at
  that position.** It APPENDS command-line flags; it does NOT replace the command. The file is
  typically `%BASENAME%.commands` and holds only options, e.g. `-fastboot -fullscreen` (Daphne)
  or `--user_language=5` (xenia). ⚠️ A `.commands` file containing a *full* command (rcade-style
  `singe vldp …`) is INCOMPATIBLE — it produces a malformed double-command.

## Directories interpreted as files
- Rename a directory to a supported file extension → ES-DE shows it as a single game entry
  (not a navigable folder). The metadata-editor Delete button is disabled for these (safety).
- To launch a specific file inside, put a file with **the same name as the directory** inside
  it; that inner file is what's passed as `%ROM%`. e.g. `Jet Grind Radio.cue/Jet Grind Radio.cue`.

## Daphne games (Hypseus)  [USERGUIDE "Daphne games"]
Folder layout (example Dragon's Lair):
```
lair.daphne/            <- directory named <game>.daphne
  lair.daphne           <- EMPTY marker file (REQUIRED: directories-interpreted-as-files)
  lair.txt              <- framefile
  lair.m2v  lair.ogg    <- video / audio  (+ optional .bf)
  lair.commands         <- OPTIONAL: extra CLI flags, e.g. "-fastboot -fullscreen"
```
- **The arcade ROM `.zip` does NOT go in the folder.** It lives in Hypseus's rompath
  (`<hypseus home>/roms/`). Hypseus searches `roms/<game>/` or `roms/<game>.zip`. (Hypseus
  author, github DirtBagXon/hypseus-singe discussion #132.)
- The folder name must be a valid Daphne game ID (list: daphne-emu.com CmdLine page). ROM
  revisions like `lair_a`, `dle11`, `lair2_319` ARE valid built-in IDs (confirmed: they load
  their own rom chips), but they reuse the base game's video — they are not separate self-
  contained titles in the canonical one-folder-per-game model.

## Singe games (Hypseus)  [USERGUIDE "Singe games"]
- Folder `<game>.singe/`. Edit the inner `<game>.singe` LUA file: set `MYDIR` to the **absolute**
  folder path (no `~`; trailing slash required), e.g. `MYDIR = "/home/deck/ROMs/.../mononoke.singe/"`.
- Assign the **"Hypseus [Singe]"** alternative emulator in the gamelist `<altemulator>` — the
  default (Daphne) emulator will NOT launch a Singe game.

## THIS install's specifics (empirical, confirmed by test 2026-06-04 — not generic docs)
- `~/Applications/hypseus-singe/roms` is a **symlink → `/run/media/deck/1tbDeck/ROMs/daphne`**,
  so the flat `~/ROMs/daphne/<game>.zip` files ARE Hypseus's rompath. Moving a base `.zip` away
  breaks rom-loading ("Could not load ROM images"). So: loose `.zip` roms stay flat; `.daphne`
  folders supply framefile+video. This matches the official design (rom not in the folder).
- Daphne system is a custom es_systems entry routing the two Hypseus commands through
  `~/Emulation/tools/launchers/hypseus-pin.sh` (X-Arcade SDL pin); DirkSimple + MAME kept.
  See memory `daphne-setup`.

## Singe — relative-path games need CWD set (2026-06-04, Time Traveler)
- Some Singe games (e.g. the freeware "Time Traveler for SINGE" by RDG2010) use HARDCODED
  RELATIVE asset paths in their .singe LUA (`dofile("singe/timetraveler/dvd-globals.singe")`,
  `fontLoad("singe/timetraveler/...")`) instead of the docs' `MYDIR` absolute-path style. These
  resolve from the **working directory**, so the launch command must set CWD to the game folder
  via **`%STARTDIR%=%GAMEDIR%`** (documented ES-DE var; added to our `Hypseus [Singe] (X-Arcade)`
  command). The framefile's `.` first line resolves relative to the FRAMEFILE's own location
  (confirmed via log: "Video/Audio directory is: <folder>/./"), so video is CWD-independent.
- Canonical ES-DE layout for such a folder (`Time_Holo.singe/`): inner `Time_Holo.singe` = the
  entry LUA script (also the directories-interpreted-as-files marker), `Time_Holo.txt` = framefile,
  `Time_Holo.commands` = **flag-only** extras (`-x 798 -y 532 -fullscreen_window -noserversend`),
  assets under `singe/timetraveler/`, videos `vts_*.m2v` at the folder root. Assign the
  `Hypseus [Singe]` emulator via the gamelist `<altemulator>`. A rcade-style `.commands` holding a
  FULL `singe vldp ...` command does NOT work (ES-DE `%INJECT%` only appends flags).

## Theming (for reusing ES-DE colors/fonts in our own tkinter UI)
Sources: official THEMES.md + THEMES-DEV.md
https://gitlab.com/es-de/emulationstation-de/-/raw/master/THEMES.md ,
https://gitlab.com/es-de/emulationstation-de/-/raw/master/THEMES-DEV.md
Read: 2026-06-04. Plus empirical read of local `pixel-es-de` theme.

### Active-theme discovery (parse from es_settings.xml)
- `~/ES-DE/settings/es_settings.xml` holds the active selection as `<string>` entries:
  - `Theme` = active theme **folder name** (NOT "ThemeSet"; that was the old name). Here `pixel-es-de`.
  - `ThemeVariant` = selected variant name, or `none` = use theme's first/default variant. Here `textlistWithVideos`.
  - `ThemeColorScheme` = selected colorScheme name, or `none` = theme default. Here `none`.
  - `ThemeFontSize` = selected fontSize bucket, or `none`. `ThemeAspectRatio` = `automatic`/`16:9`/...
  - `UserThemeDirectory` = optional extra themes search path ("" = unset).
- Theme folders are searched in `~/ES-DE/themes/<name>/` first, then the app-resources
  `<install>/themes/<name>/`; the HOME copy wins on name clash. So resolve active theme dir as
  `~/ES-DE/themes/$Theme` (fall back to UserThemeDirectory, then app resources).

### theme.xml / capabilities.xml structure
- `capabilities.xml` (theme root) declares: `<themeName>`, one or more `<aspectRatio>`, and
  `<variant name="..">` (with `<label>`, `<selectable>`, optional `<override>`/`<trigger>`/`<useVariant>`),
  plus optional `<colorScheme name="..">` (with `<label>`) and `<fontSize>` buckets. ES-DE reads
  this to populate the UI Settings menus.
- `theme.xml` (theme root) holds `<view name="system|gamelist">` blocks and `<variant name="..">`
  wrappers. Colors/fonts are PER-ELEMENT properties, all hex RGB (6) or RGBA (8):
  - text/datetime/image/video/badges/carousel: `<color>`; carousel also `<textColor>`,`<itemColor>`,`<selectedItemColor>`.
  - textlist/grid: `<primaryColor>`,`<secondaryColor>`,`<selectedColor>`,`<selectorColor>`,`<textColor>`.
  - helpsystem: `<textColor>`,`<iconColor>`,`<textColorDimmed>`,`<iconColorDimmed>`.
  - fonts: `<fontPath>` (PATH, a .ttf, theme-relative `./...`) + `<fontSize>` (FLOAT, fraction of screen height).
- **Variables**: `<variables>` block defines names; referenced as `${name}` anywhere, incl. partial values
  (e.g. `${themeColor}40` appends alpha). Variable NAMES are theme-author-chosen, not fixed by ES-DE
  (only the element PROPERTY names above are fixed). colorScheme blocks are "just a set of `<variables>`".
- **Includes**: `<include>./path.xml</include>` merges another theme file; same element `name`+type
  combine/override. Per-system folders use this (see below).
- `<label>` may carry `language="en_US"` etc. for localization.

### pixel-es-de specifics (this install)
- Base `theme.xml` (root) uses HARDCODED hex (e.g. textlist `primaryColor=ffffff`,
  `secondaryColor=871F78`, `selectedColor=ffffff`; help `textColor=ffffff`; bg `<image><color>808080`).
  It defines NO root `<variables>` and NO colorScheme — colors come per-system.
- Each per-collection folder `~/ES-DE/themes/pixel-es-de/<system>/theme.xml` declares its OWN
  `<variables><themeColor>RRGGBB</themeColor><selectColor>RRGGBB</selectColor></variables>` then
  `<include>./../theme.xml</include>` and overrides only the bg `<color>` and textlist
  `selectedColor`/`selectorColor` (`${themeColor}40` = 25% alpha). So **color varies per system**:
  nes ff0000, snes cc1919, genesis 3443ff, arcade 004688/select ffce08, psx a33a3a, daphne fda504, system(default) 00a0e8.
  `themeColor` is a saturated brand color (good as ACCENT, bad behind body text); `selectColor` is the highlight.
- Font: single shared `art/font.ttf` = **"SF Pixelate"** (fc-scan family) — a small pixel font; reads
  tiny at normal pt sizes, needs ~larger px and integer scaling to stay crisp.
- `_inc/` here only holds `sounds/` (navigation .wav). No color/font includes live there in this theme.

### Key facts for a SEPARATE process reusing the theme
- ES-DE parses ONLY `capabilities.xml`, `theme.xml`, and files reached via `<include>`. Any OTHER
  file in a theme folder is IGNORED (confirmed: pixel-es-de keeps `theme.xml.retropie-original`,
  `README.md`, `readme.txt`, `splash*.png` in-tree untouched). => safe to drop sidecar files
  (e.g. a `router-config/` subdir or `*.routercfg.json`) inside a theme without affecting ES-DE.
- tkinter cannot load a .ttf by path directly. To use `art/font.ttf` either (a) register it for the
  process via `fc-cache`/an XDG fonts dir then reference family "SF Pixelate", or (b) use Tk 8.6+'s
  ability to load via Pillow/`tkextrafont`. The Deck has `/usr/bin/fc-cache`,`fc-scan`,`fc-list`.
  Pragmatic path: copy/symlink the .ttf into `~/.local/share/fonts/`, run `fc-cache -f`, then
  `tkinter.font.Font(family="SF Pixelate", size=N)`. Detect family name with `fc-scan --format '%{family}'`.
- Caveats: (1) per-system `themeColor` is a SATURATED background/accent — never put body text on it;
  derive a readable bg by darkening it or use a fixed dark panel + themeColor only for accents/borders.
  (2) Pixel fonts render small — bump pt size / use integer multiples; offer a fallback system font.
  (3) Colors are per-system not global — pick ONE representative (e.g. the `system/` default 00a0e8,
  or let the tool target a specific system) rather than assuming a single theme palette.
  (4) RGBA 8-digit hex appears (`...40`) — strip/convert alpha before handing to tkinter (#RRGGBB only).

### MAD implementation (2026-06-04, `lib/gui_theme.py`)
- **Resolution chain:** sidecar `~/ES-DE/themes/<theme>/router-config/theme.toml` (authoritative) →
  auto-extract from `theme.xml` (resolves `<variables>`/`${var}` + honors the active `ThemeVariant`/
  `ThemeColorScheme`) → built-in dark fallback. `Theme.source` = sidecar|auto|fallback.
- **Sidecar schema** (TOML, all keys optional; **quote hex** — TOML `#`=comment): `bg panel row border
  fg fgDim accent accent2 selectBg selectFg selectorColor warn` + `font` (family) / `fontFile`
  (theme-relative .ttf) / `pixelFont` (bool) / `fontSizePt`. Friendly names map to internal palette
  (fg→text, fgDim→text_dim, panel→surface). Annotated reference sample shipped at
  `~/ES-DE/themes/pixel-es-de/router-config/theme.toml`.
- **Auto-extract** keeps a dark base and TINTS chrome ~10% toward the theme `themeColor` (theme bg is
  unusable behind text); adopts `selectedColor/selectColor/themeColor`→accent, `selectorColor`→focus ring,
  light `primaryColor`→text. pixel-es-de auto-resolves to magenta accent `#bc14ff` on dark.
- Run `python3 lib/gui_theme.py` to dump the resolved palette/source headlessly.

## Scrapers & Steam-game media
Source: ES-DE USERGUIDE; screenscraper.fr systeme pages; ES-DE GitLab issue #1766. Read 2026-06-08.
- ES-DE supports only **two scrapers: ScreenScraper.fr and TheGamesDB.net**. No native SteamGridDB
  support — it's an OPEN feature request (issue #1766), not implemented.
- **ScreenScraper has NO dedicated Steam platform.** Steam/PC titles fall under **"PC Windows"**
  (systemeid **138**, ~43.5k games) or **"PC Dos"** (135). PC Windows DOES carry full media set
  (cover, screenshot, video, wheel/logo, marquee, fanart) — so popular Steam titles scrape OK.
- **Why Steam scraping is unreliable:** ScreenScraper matches by file **checksum** (impossible for a
  Steam `.steam`/`steam://rungameid/<appid>` shortcut — no ROM file) or by **exact filename/name**.
  Steam = name-only match → quality hinges on naming; indie/obscure/new titles often missing.
- **Better Steam art source = SteamGridDB** (grids/heroes/logos/icons), but ES-DE can't scrape it
  natively. Workaround: pull art externally and drop into `~/ES-DE/downloaded_media/steam/<mediatype>/`,
  or use a 3rd-party SteamGridDB scraper tool to generate the files.
- **Official transparent LOGO for a Steam game** (→ ES-DE marquee): best source is the **local Steam
  client cache** `~/.local/share/Steam/appcache/librarycache/<appid>/<hash>/logo.png` (also
  `library_hero.jpg` → fanart, `library_capsule.jpg`, `library_header.jpg` there). This often EXISTS
  even when the public store API hides it — `IStoreBrowseService/GetItems` can return
  `library_logo: None` while the local client has the logo. Verified 2026-06-08 on Huntdown: Overtime
  (appid 2473350): GetItems gave library_hero but library_logo=None; SGDB had no entry for that appid
  at all (only base "Huntdown" id 16980); local librarycache had the correct transparent OVERTIME logo.
- Resolve modern Steam asset URLs (hashed paths) via `IStoreBrowseService/GetItems/v1?input_json=...`
  with `data_request.include_assets=true` → `assets.{library_hero,library_capsule,library_logo,...}`,
  prefix `https://shared.cloudflare.steamstatic.com/store_item_assets/` + `asset_url_format`.
- **SGDB direct API:** needs `Authorization: Bearer <key>` AND a real `User-Agent` (Cloudflare 403s
  the default urllib UA → looks like auth failure but isn't). Endpoints `/api/v2/{logos|heroes|grids}/
  steam/<appid>` (404 if appid not indexed) or `/{kind}/game/<sgdbid>` after `/search/autocomplete/<name>`.
  Key is **deliberately NOT persisted** (secret) — ask the user each time. Durable tool:
  `~/Emulation/tools/launchers/steam-fetch-media.py` (+ `lib/sgdb.py`); local librarycache is its source #1.
- **Media-extension gotcha:** the file extension MUST match the real image format (a PNG saved as
  `.jpg` renders broken/sideways in ES-DE). Logos are PNG (transparent), heroes/covers usually JPG.

## Recovering Steam store media for stripped/delisted store pages
Source: Wayback Machine CDX + live Steam CDN probing (River City Ransom: Underground, appid 422810). Read 2026-06-11.
- A Steam store page can be STRIPPED of screenshots/movies while staying online: `appdetails`
  returns `success:true` but `filters=screenshots,movies` comes back EMPTY, and the live store
  page HTML contains zero `ss_` URLs (RCR:U is such a case).
- The asset FILES usually still exist on the live CDN — only the page listing was removed. Recovery:
  1. Wayback CDX for old snapshots: `web.archive.org/cdx/search/cdx?url=store.steampowered.com/app/<appid>*&fl=timestamp,original,statuscode&filter=statuscode:200`
  2. Grep the snapshot HTML for `apps/<appid>/ss_<sha1>.1920x1080.jpg` (screenshots) and the
     trailer's separate MOVIE id: `steam/apps/<movieid>/movie{480,_max}.{mp4,webm}`.
  3. Fetch from live `steamcdn-a.akamaihd.net` (or `shared.akamai.steamstatic.com/store_item_assets`)
     — verified all still HTTP 200 in 2026 for a page stripped since ~2018. Trim trailer with
     `ffmpeg -c copy -t 30 -movflags +faststart` per the steam collection's video convention.
- Caveat: an `ss_<hash>.1920x1080.jpg` URL may serve a lower-res master (RCR:U's were 1280x720) — fine.

## Art/resource organization (CANONICAL conventions)
Sources (read 2026-06-13, against our local ES-DE **3.4.1** source = `~/esde-build/ES-DE`, the
exact running version; these files ARE the official docs/code):
- THEMES.md — https://gitlab.com/es-de/emulationstation-de/-/raw/master/THEMES.md
- THEMES-DEV.md — https://gitlab.com/es-de/emulationstation-de/-/raw/master/THEMES-DEV.md
- USERGUIDE.md — https://gitlab.com/es-de/emulationstation-de/-/raw/master/USERGUIDE.md
- INSTALL.md "Overriding resource files" — https://gitlab.com/es-de/emulationstation-de/-/raw/master/INSTALL.md
- code: `es-core/src/resources/ResourceManager.cpp` (`getResourcePath`), `es-core/src/utils/FileSystemUtil.cpp` (`getAppDataDirectory`), `es-app/CMakeLists.txt` (install rules)

### 1. THEME art (the `:/`-NOT scheme — theme-relative `./`)
- A theme = "a collection of assets like images, videos and fonts as well as XML config files".
  Art lives INSIDE the theme dir tree and is referenced **theme-relative with a `./` prefix**
  (`<path>./core/frame.png</path>`, `<fontPath>./core/font.ttf</fontPath>`). The `./` is resolved
  relative to the XML FILE doing the reference; `<include>` paths work the same way. (THEMES.md
  §"How it works", line ~1283: *"paths … are set as relative to the theme file by adding './' …
  This prefix works for all path properties."*)
- Canonical theme dir structure (THEMES.md lines 47–93): theme root holds `theme.xml`
  (default/fallback view config) + `capabilities.xml` (MANDATORY, even if empty — declares
  `themeName`, `variant`s, `colorScheme`s, `fontSize`s, `aspectRatio`s, languages). Shared assets
  go in a `core/` subdir (`core/fonts/`, `core/images/`, `core/sounds/` in slate-es-de). Per-system
  art goes either in **`<system>/` subdirs** (`nes/`, `snes/` each with `theme.xml` + `images/`) OR
  in a variable-driven **`systems/{backgrounds,logos,metadata}/<system>.*`** layout — both official.
- `<system>/theme.xml` is auto-loaded per system (dir name = es_systems.xml `<theme>` tag, else
  `<name>`); root `theme.xml` is the fallback when no per-system file exists. `${system.theme}`
  expands to that dir name for building include paths.
- `_inc/` is NOT an ES-DE-defined dir — it's a convention SOME community themes use (e.g. RobZombie
  pixel-es-de `_inc/systems/...`) for include fragments; ES-DE only cares that the `<include>`/`<path>`
  resolves. Any file in a theme that is NOT reached via capabilities.xml→theme.xml→`<include>` is
  simply IGNORED (confirmed earlier; safe to drop sidecars like `router-config/`).
- Two search roots, HOME wins on name clash: `~/ES-DE/themes/<name>/` then
  `<install>/themes/<name>/` (Linux install = `/usr/share/es-de/themes/`). Only linear/modern/slate
  ship bundled (es-app/CMakeLists.txt:391–396).

### 2. APPLICATION / built-in resources (the `:/` colon-slash scheme)
- ES-DE's OWN graphics/fonts/sounds/etc. are referenced in code/themes as a **virtual path starting
  `:/`** e.g. `:/graphics/splash.svg`, `:/fonts/Akrobat-Bold.ttf`. `ResourceManager::getResourcePath`
  resolves `:/<x>` by checking, IN ORDER:
  1. `~/ES-DE/resources/<x>`  ← **user override (app-data home) — checked FIRST**
  2. (platform pkg/data path; on a NON-AppImage unix build, the program-data dir)
  3. `<exe-dir>/resources/<x>`  ← the BUNDLED/installed copy
  Missing => fatal "Program resource missing" + emergency shutdown (unless `terminateOnFailure=false`).
- Source-tree location of the bundled files: **`resources/`** at the ES-DE repo root, with subdirs
  `graphics/` (svg+png: badges, buttons, arrows, splash.svg, white.png, window_icon, plus
  `graphics/{controllers,help,overlay,systemstatus}/`), `fonts/` (Akrobat*, DejaVuSans, FontAwesome,
  NotoEmoji, …), `sounds/`, `shaders/glsl/`, `controllers/`, `MAME/`, `systems/<os>/`, `sorting/`,
  `locale/<lang>/`, `certificates/`.
- Compiled into the AppImage via CMake `install(DIRECTORY .../resources DESTINATION …)` →
  `<install>/share/es-de/resources` (es-app/CMakeLists.txt:397–398); in the AppDir, `usr/bin/resources`
  is a symlink → `../share/es-de/resources`. So the canonical APP-level (non-theme) art home is the
  repo `resources/` tree, surfaced at runtime as `:/…`.
- **Per-file override is OFFICIAL** (INSTALL.md "Overriding resource files", line ~1125): drop a file
  with the SAME relative path under `~/ES-DE/resources/` and it wins over the bundled copy. Their own
  example: `ES-DE/resources/graphics/splash.svg`. (This IS how our fork's full-screen splash works —
  `~/ES-DE/resources/graphics/splash.svg` already exists here.) Don't override es_systems.xml /
  es_find_rules.xml / es_import_rules.xml this way (use custom_systems instead).

### 3. MEDIA / gamelist art (downloaded_media)
- Canonical: **`~/ES-DE/downloaded_media/<system>/<mediatype>/<game>.<ext>`** (USERGUIDE §"Manually
  copying game media", line ~3545). Media filename = ROM basename (folders mirror the ROM subdir tree;
  for "dirs-as-files" games the inner-file name). Path is overridable via _Other settings → Game media
  directory_ (blank = default). Here `~/ES-DE/downloaded_media` is a symlink → SD card.
- The 11 canonical `<mediatype>` subdirs (USERGUIDE line ~3581): `3dboxes, backcovers, covers, custom,
  fanart, manuals, marquees, miximages, physicalmedia, screenshots, titlescreens, videos`.
  (`miximages` are GENERATED by ES-DE, not scraped; `manuals` are PDFs.) Images: .jpg/.png/.webp;
  videos: .mp4/.mkv/.avi/.wmv/.mov/.webm.
- Custom-system art uses the same per-system folders, keyed by the custom es_systems `<name>`/`<theme>`.

### 4. Art for an APPLICATION EXTENSION (e.g. our MAD panel — NO native ES-DE concept)
- There is **NO ES-DE-canonical home for third-party-feature art** — ES-DE has no plugin/extension
  model. The closest official conventions, and how they map to MAD:
  - (a) Built-in `:/` resources: only legitimate for art the ES-DE BINARY itself renders. A fork could
    add e.g. `resources/graphics/mad/…` and reference `:/graphics/mad/…`, BUT that bloats the AppImage
    and is wiped on every EmulateStation/EmuDeck app update — wrong for a separate tkinter process.
  - (b) Theme-relative: ES-DE ignores non-referenced theme files, so a `router-config/` sidecar inside
    the active theme is SAFE and makes MAD art themeable per-theme — good for OPTIONAL per-theme
    overrides, but a theme is not guaranteed to ship MAD art so it can't be the sole home.
  - (c) User data dir under `~/ES-DE/`: ES-DE owns this namespace; dropping MAD-specific subdirs here
    risks collisions with future ES-DE dirs and the _Orphaned data cleanup_ tool. Not recommended as
    primary.
  - (d) The honest answer: **there is no canonical ES-DE answer** — MAD is an external app, so its
    BASELINE art belongs with the MAD code (outside the ES-DE tree), with the theme sidecar as an
    OPTIONAL override layer.
- This is exactly what MAD already does (`router-config-gui.py::_mad_art_dirs`, resolution order):
  1. `~/ES-DE/themes/<active-theme>/router-config/`  (optional per-theme override; ES-DE-safe sidecar)
  2. `~/Emulation/tools/launchers/art/`  (MAD's OWN bundled baseline — the durable home)
  3. `~/esde-build/art/`  (project source/staging dir)
  => Recommendation: KEEP MAD's baseline art in `~/Emulation/tools/launchers/art/` (versioned with the
  tool, survives ES-DE rebuilds/updates), keep the theme `router-config/` dir as the optional themeable
  layer. Do NOT compile MAD art into ES-DE `:/` resources and do NOT invent new top-level `~/ES-DE/`
  dirs for it.

### 5. The `~/ES-DE/` user-data layout (getAppDataDirectory)
- `~/ES-DE` = the app-data dir (FileSystemUtil.cpp `getAppDataDirectory`: `$ESDE_APPDATA_DIR` env
  override → else `~/ES-DE`; macOS = `~/Documents/ES-DE`; Android/portable differ). Created on first
  start. **ES-DE upgrades never touch anything inside it** (USERGUIDE line ~125). Canonical subdirs:
  - `themes/`         — user-installed themes (+ theme downloader's `themes-list/`); HOME copy beats bundled.
  - `settings/`       — `es_settings.xml`, `es_input.xml`, etc.
  - `gamelists/<system>/gamelist.xml` — game metadata (ES-DE keeps these here, NOT in the ROM tree).
  - `collections/`    — auto + custom collection `.cfg` files.
  - `custom_systems/` — user `es_systems.xml` / `es_find_rules.xml` overrides (the SUPPORTED way to
                        customize systems — NOT editing bundled resources).
  - `downloaded_media/` — game media (see §3); often relocated/symlinked.
  - `resources/`      — OPTIONAL per-file overrides of bundled `:/` resources (see §2); not created by
                        default. Here it holds `graphics/splash.svg` (fork splash).
  - `logs/`           — `es_log.txt`.
  - `controllers/`, `screensavers/`, `scrapers/`, `scripts/`, `splashscreens/`, `usermanuals/` — other
    runtime data dirs (present in this install; `scripts/` = custom event scripts).
