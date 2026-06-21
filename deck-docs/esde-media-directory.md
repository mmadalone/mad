# ES-DE `MediaDirectory` setting — how ES-DE itself resolves it

_Source: ES-DE `es-app/src/FileData.cpp` `getMediaDirectory()` + INSTALL.md
(GitLab `es-de/emulationstation-de`), confirmed 2026-06-22._

ES-DE stores the downloaded-media location in `es_settings.xml`:

```xml
<string name="MediaDirectory" value="" />
```

`getMediaDirectory()` resolves it as follows:

1. **Empty** ⇒ default to `<ES-DE app-data dir>/downloaded_media/`
   (app-data dir = `~/ES-DE` unless `$ESDE_APPDATA_DIR` overrides).
2. **`~`** ⇒ expanded via `expandHomePath()` (home-dir expansion).
3. **`%ESPATH%`** ⇒ replaced with `getExePath()` — the **directory of the ES-DE
   binary**. Documented in INSTALL.md as "the path to the ES-DE binary… useful for
   portable installations."

There is **NO `%ESDEDIR%` token**, and `%ROMPATH%` is for ROM/system settings, not
media. (Earlier MAD code wrongly expanded a fictional `%ESDEDIR%` and missed
`%ESPATH%`; fixed 2026-06-22.)

## How MAD uses this (`lib/esde_settings.media_root()`)
- Reads `MediaDirectory`; expands `~` / `%HOME%` and best-effort `%ESPATH%`
  (derived from the located AppImage/AppDir as `.../usr/bin`, only if that dir
  exists). `$MAD_MEDIA_ROOT` env and an `install.conf` `MEDIA_ROOT` key override.
- **Safety:** if any `%token%` is left unresolved (e.g. `%ESPATH%` on a build where
  the exe dir can't be located), it falls back to `<APPDATA>/downloaded_media`
  rather than returning a literal-token path that downstream writers would
  `mkdir`/copy into. `getExePath()`'s exact return on the Steam Deck AppImage is
  version-dependent, so MAD only substitutes `%ESPATH%` when the derived `usr/bin`
  actually exists; otherwise it uses the safe default.
- On the maintainer's Deck `MediaDirectory` is a plain absolute path
  (`/run/media/deck/1tbDeck/downloaded_media`), returned verbatim — no token logic.
