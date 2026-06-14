# Supermodel (Sega Model 3) — Supermodel.ini config reference

Sources (read 2026-06-14):
- Bundled official manual: flatpak `com.supermodel3.Supermodel` → `files/bin/Docs/README.txt`
  (Version 0.3a-WIP, "USER MANUAL FOR SUPERMODEL", §10 The Configuration File, §12 Index of
  Configuration File Settings). This bundled README does NOT document the newer graphics keys
  New3DEngine / QuadRendering / WideScreen / Stretch (only CLI flags -wide-screen/-stretch/-wide-bg).
- Source of truth for typing: mirror/model3emu (GitHub) Src files —
  Src/OSD/SDL/Main.cpp (DefaultConfig + CLI parse), Src/Util/GenericValue.h (ValueAs<bool>/serialize),
  Src/Util/NewConfig.h, Src/Util/ConfigBuilders.cpp (INI parser).
- supermodel3.com/AdvancedUsage.html (online manual; was ECONNREFUSED on 2026-06-14, use README.txt).

## File location (this deck)
- Active emulator = flatpak `com.supermodel3.Supermodel` (ES-DE model3 → `supermodel.sh` →
  `flatpak run com.supermodel3.Supermodel`). Inside the sandbox HOME=/home/deck and
  `filesystems=home`/`host`, so it reads the REAL file:
  **/home/deck/.supermodel/Config/Supermodel.ini**
  (the flatpak `persistent=.supermodel` map at ~/.var/app/.../.supermodel is EMPTY/unused).
- Process name for pgrep -x: **supermodel** (flatpak binary is literally `supermodel`).
  Also-installed-but-inactive: supermodel.exe (Proton), native binary. Refuse writes while running.

## Syntax (README §10)
- `Name = Argument`, one per line, CASE SENSITIVE. Two arg types only: **integers** (bare, may be
  negative) and **strings** (double-quoted). Booleans are the integer 0 (off) / 1 (on) style.
- `[ SectionName ]` headers (note the SPACES inside brackets in the default file, e.g. `[ Global ]`,
  `[ scud ]`). Settings before any header → Global. Comments start with `;` to end of line.
- Precedence: Global < game-specific section (named by MAME romset, lowercase) < command line.
- **Input mappings (Input*) are read ONLY from [Global].** Graphics/audio can be Global or per-game.

## Bool parsing/serialization (GenericValue.h) — DEFINITIVE
- ValueAs<bool> accepts text "true"/"false"/"on"/"off"/"yes"/"no" (case-insensitive) AND falls
  back to numeric via stringstream, so "1"/"0" also parse correctly.
- Supermodel's OWN serializer writes bool as NUMERIC "1"/"0" (no boolalpha).
  => Writer should write **1 / 0** for all the Integer/bool graphics+misc keys. Matches both the
  existing file and Supermodel's own output. (Exception: a FEW keys are documented as
  "Boolean value (true or false)" e.g. OutputsWithLF — NOT in our wanted set.)

## Wanted [Global] keys — type per source
All Integer / 0|1 bool unless noted. (Main.cpp DefaultConfig literals shown for reference.)
- New3DEngine    bool 0/1   default true   (new 3D engine)
- QuadRendering  bool 0/1   default false  (quad rendering; CLI -quad-rendering)
- WideScreen     bool 0/1   default false  (CLI -wide-screen; mutually-exclusive-ish w/ Stretch)
- WideBackground bool 0/1   default false  (CLI -wide-bg)
- Stretch        bool 0/1   default false  (CLI -stretch)
- VSync          bool 0/1   default true
- FullScreen     bool 0/1   default false  (README §12; CLI -fullscreen)
- MultiThreaded  bool 0/1   default true   (README §12; CLI -no-threads disables)
- GPUMultiThreaded bool 0/1 default true
- Throttle       bool 0/1   default true   (README §12; CLI -no-throttle)
- XResolution / YResolution  Integer pixels, default 496x384 (CLI -res=x,y)
- MusicVolume / SoundVolume  Integer percent 0..200, default 100 (CLI -music-volume/-sound-volume)

## DUPLICATE-KEY / multi-section hazard on THIS deck's file (critical for the writer)
The user's [Global] is huge (≈ lines 214–470, ends at `[Supermodel3 UI]`) and was hand-edited:
- **FullScreen appears TWICE inside [Global]** (`FullScreen=0` then later `FullScreen=1`).
  Supermodel = last-wins within a section → effective value is the LAST one (1). A first-match
  whole-file regex edit would change the wrong (ineffective) line.
- WideScreen / MusicVolume / SoundVolume also appear in MANY per-game sections
  (14 / 29 / 29 occurrences total in file). A whole-file first-match regex hits a per-game section,
  NOT [Global].
=> The model2_cmds.py whole-file `_set_key` (count=1, first match) is UNSAFE here. The Model 3
   writer must (a) scope to the `[ Global ]` section span only, and (b) edit the LAST matching
   line of the key within that span (so duplicate FullScreen resolves to the effective one).
   Same byte-preserving regex-on-value technique otherwise (preserve `=` spacing, inline `;`
   comment, line ending). File on this deck is LF-only (no CRLF) — but open newline="" anyway.

## Mixed key spacing in the file
Some keys have a space before `=` (`New3DEngine =1`, `WideScreen =1`), others none
(`FullScreen=1`, `XResolution=1280`). The value-token regex must tolerate optional ws around `=`
and preserve whatever is there. None of the wanted graphics/audio values are quoted (all bare ints).
