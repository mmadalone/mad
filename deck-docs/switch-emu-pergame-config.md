# Switch emulator per-game config inheritance (Ryujinx / Eden / Citron) + the MAD media+info browser

Source-verified against each emulator's own code on 2026-07-03. Cache per CLAUDE.md rule #2 (check
here before re-deriving). Underpins MAD's per-game settings + the media+info browser.

## Ryujinx (Ryubing / GreemDev fork -- the installed one)

- Per-game config lives at `~/.config/Ryujinx/games/<TITLEID lowercased>/Config.json`. Per-game
  config is a FORK feature (the original, discontinued gdkchan Ryujinx never shipped it).
- The per-game file is a COMPLETE clone of global that WHOLLY REPLACES it. There is NO per-key
  inherit. Source: `ConfigurationState.Migration.cs Load(cff, path, titleId)` -- per-game-CAPABLE
  settings assign directly (`X.Value = cff.X`), so an ABSENT key deserializes to `default(T)`
  (0 / false / enum-0 / null) and OVERWRITES; it does NOT fall back to global. Global-only settings
  (the rows the GUI tags `(Global)`: theme, update-check, game dirs, hotkeys, system time) ignore
  the per-game file. So a PARTIAL per-game file is UNSAFE.
- If the file's `version` is behind, Ryujinx REWRITES it as a complete `ToFileFormat()` snapshot
  bumped to `CurrentVersion`. The live global on this Deck is `version:70` (do NOT hardcode -- carry
  it from the live file). The GUI writes a FULL snapshot (every key) to the per-game file, not a diff.
- Config JSON is FLAT top-level key/value.
- Source read via the Ryubing mirror `lavaforge.org/preserve-emulation/Ryubing` (master) +
  base cross-check `github.com/nintendoswitchemulators/ryujinx`. Official `git.ryujinx.app` is behind
  Anubis; `github.com/GreemDev/Ryujinx` 404s (moved).

MAD approach (`lib/madsrv/ryujinx_cmds.py`): MAD edits the game's own `games/<tid>/Config.json`
directly, in place -- the same file Ryujinx itself reads/writes. There is NO sidecar pin-map and NO
launch-time regeneration; an earlier sidecar-pin-map design was retired because it could clobber a
value the user had set in Ryujinx that MAD did not track. For the full current model (how overrides
are detected, how "Inherit global" works, what happens to the file when the last override is
cleared, and the per-game-input limitation) see ryubing-config.md's "Per-game MAD model (DIRECT
read/write)" section.
Two constraints from that model still apply and are still enforced in code: a readable GLOBAL
`Config.json` is REQUIRED to write a per-game override -- MAD raises ENOENT rather than write a
partial per-game file, because Ryujinx resets any absent key to its compiled default. And the
global (no-titleid) bool set must PARSE the C++'s string `"0"/"1"` -- `bool("0")` is True, so a
naive `bool(raw)` can never turn a global bool Off.

## Eden + Citron (Yuzu forks -- byte-format-identical qt-config)

- Per-game file: `~/.config/{eden|citron}/custom/<TITLEID uppercased>.ini`.
- Inheritance is per-key markers. A key INHERITS global when `key\use_global` is true/absent; it is
  OVERRIDDEN by the TRIPLE `key\use_global=false` + `key\default=false` + `key=<value>`.
- The `\default` twin gotcha: a `key=value` line is IGNORED unless `key\default=false` is also
  present (absent/`\default=true` -> the compiled default is loaded, value discarded). So writing an
  override REQUIRES all three lines.
- An absent key inherits, so a MINIMAL create-on-demand ini is SAFE and is not clobbered on boot
  (the emulator only writes on an explicit per-game save). No need to open the game's Properties
  first.
- Enum INDICES differ between Eden and Citron (both descend from different Yuzu snapshots): `backend`,
  `resolution_setup` (Citron shifted +1), `scaling_filter` (FSR=7 vs 5), `gpu_accuracy`,
  `aspect_ratio`, `anti_aliasing`, `max_anisotropy`, audio engine. So the descriptor GROUPS must NOT
  be shared -- only the format engine is.
- Source: `frontend_common/config.cpp` ReadSettingGeneric/WriteSettingGeneric, read via
  `github.com/eden-emulator/mirror` (master) and `github.com/citron-neo/emulator` (main).

MAD approach: shared format engine `lib/madsrv/yuzu_pergame.py` (create-on-demand, inherit-aware
render, the `\default` twin, clear-to-inherit). Citron + Eden each supply their OWN GROUPS + path +
running check. `cfgutil.ini_set_or_insert` writes `key = value` (SPACES) -- so any override-detection
must be spaces-tolerant (`yuzu_pergame.has_override` uses `\use_global\s*=\s*false`); a plain
`"use_global=false" in text` check MISSES MAD-created files.

## The MAD per-game media+info browser (the standard for ALL per-game pages)

Fork C++ `GuiMadPagePergameBrowser` (drop-in twin of `GuiMadPageGamePicker`, same
`(ns, target, menuSections)` contract + on-select dispatch) renders a two-pane page: LEFT a
virtualized game list with a `*` override badge; RIGHT the game's media (art -> preview video,
ES-DE's own `FileData::getImagePath()/getVideoPath()`) over an info panel (the overrides summary).
`GuiMadPageRetroArchGame` derives from it (RA-specific cores/core-picker + `<system>:<stem>` identity
+ the fixed Settings/Input/Controllers sub-menu).

Backend contract -- each `<ns>.games` returns:
- `games`: `[{titleid, stem, name, override, summary}]`. `stem` = the ROM filename stem (ES-DE
  `getStem` parity: last extension dropped, tags kept, case-preserved) for media; `override` (bool)
  = the `*` badge; `summary` = the info-panel line ("" -> "No per-game overrides yet.").
  CAVEAT: for a FOLDER-as-file system (e.g. Lindbergh `<name>.lindbergh`), ES-DE `getStem` does NOT
  strip the extension of a DIRECTORY, so emit the full folder name (`p.name`, "rambo.lindbergh")
  as `stem`, not the bare `p.stem`.
- `system`: the ES-DE system whose media the browser resolves (e.g. `switch`, `ps2`, `lindbergh`).

Media resolution: the browser indexes the system's FileData by `getStem()` (exact, used when the
row HAS a stem) AND by the scraped gamelist `getName()` as a fallback (used ONLY for a stem-less row,
so a stem-carrying game never mis-resolves to a same-named title). The name key is `normKey()` --
lowercased with ALL whitespace stripped -- NOT a plain `lower()`. Why: the scraper can add spacing
around punctuation that the emulator's title metadata omits, e.g. ES-DE scraped
`Pokemon : Let's Go, Pikachu!` (space before the colon) vs Ryujinx's title `Pokemon: Let's Go,
Pikachu!`; a plain lowercased compare misses by that one space, but stripping whitespace makes them
match. `normKey` is byte-safe for UTF-8 (tolower/isspace on bytes >127 are no-ops in the C locale,
so accented chars like the precomposed NFC `e-acute` pass through unchanged on both sides). A
stem-less ROM whose name still doesn't match any gamelist `getName()` shows info without media (tag
the ROM `[TITLEID]` -- the standard convention -- to give it a real stem).

Setting `MadPergameBrowserScope` (Main Menu > UI Settings, between Startup view and Systems sorting):
`all` = the browser also drives the input/controllers/pads pickers; `settings` = only the settings
picker (which always uses it).

## Canonical Switch-emu section layout (menu parity)

Every Switch standalone emulator (Eden, Ryujinx, Citron) presents the SAME 5 top-level rows in its
`Standalones -> Switch -> <emu>` config menu, so the tree never reads as a cluttered flat list and
all three stay in parity as their granular settings trees get built:

```
System    (group)  -> General, CPU, System, Dock detection
Video     (group)  -> Graphics, Graphics (Adv)
Input     (group)  -> Controllers, Input mapping, Hotkeys
Audio     (leaf, opens the Audio settings page directly)
Per-game  (the game-first media+info browser menu; see the section above)
```

Map each emulator's OWN config tabs into these buckets (verify per-emu source -- tab/enum sets
diverge, see the Ryujinx/Eden+Citron sections above):
- Yuzu forks (Eden/Citron) tabs General/System/CPU/Graphics/Graphics-Adv/Audio/Controls/Hotkeys drop
  in 1:1.
- Ryujinx tabs (User Interface/System/CPU/Graphics/Audio/Input/Hotkeys/Network/Logging): System-ish
  tabs -> **System**, Graphics -> **Video**, Input + Hotkeys -> **Input**, Audio -> the leaf.
- "Dock detection" is MAD's own launch-time docked/handheld toggle, not an emulator tab -> **System**.

Implementation: `lib/madsrv/standalones_cmds.py` builds the tree with `kind:"group"` rows carrying a
nested `sections` list (a local `group()` helper next to `row()`); the fork C++
`GuiMadPageStandaloneSections.cpp` recurses on a group row (opens a sub-chooser). This is GENERIC --
the same nested-group pattern `_pcsx2_sections` uses -- so grouping is pure Python, NO fork rebuild.
Citron's builder is `_citron_sections`; the structure (and a no-page-lost guard) is pinned by
`tests/test_citron_sections.py`. When relocating leaf rows into groups, keep each row's
`label`/`sublabel`/`kind`/`arg` VERBATIM so every page opens exactly as before.

### The per-game sub-menu is grouped the same way (needs a fork rebuild)

After picking a game, the per-game sub-menu groups identically (Citron, `_citron_pergame_row`):

```
System   (group)  -> System, CPU, Linux (GameMode)
Video    (group)  -> Graphics, Adv. Graphics
Audio    (opens directly)
Input    (opens directly -> Input Profiles)
Add-Ons  (opens directly)
Cheats   (opens directly)
```

Categories with 2+ pages are sub-choosers; single pages open directly. Pinned by the `Pergame`
class in `tests/test_citron_sections.py`.

UNLIKE the top-level grouping (pure Python), the per-game grouping needs a ONE-LINE-ish fork change
+ rebuild. Reason: the media+info browser's `settingsmenu` target injects the picked titleid into
the sub-menu leaves in `GuiMadPagePergameBrowser::openGame` (`leaf.ctxVal = id`). That loop only
touched TOP-LEVEL leaves, so a leaf nested inside a `kind:"group"` row got NO titleid and would open
the GLOBAL page. Fix: recurse one level into each leaf's `subsections` and inject there too (per-game
grouping is exactly 2 levels deep, so a single nested loop suffices -- no `<functional>`). Safe for
other emus: the inner loop is a no-op when `subsections` is empty (a flat menu), RetroArch OVERRIDES
`openGame`, and Citron is the only backend user of `settings_pergame_menu`. `parseSections` already
recurses, and the section chooser already opens a sub-chooser for a group row, so no other C++ change
is needed. (Recorded 2026-07-03; top-level + per-game both shipped.)

## Switch tile install detection (STRICT binary)

`standalones_cmds._emu_installed(emu)` gates which Switch emulators appear in the dynamic Switch
group tile (2+ present -> sub-grid; exactly 1 -> collapses to open it directly; 0 -> the tile
disappears). It is STRICT: an emulator shows ONLY when its actual launchable binary exists -- the
same thing ES-DE resolves for `%EMULATOR_<X>%` -- NOT when a leftover config dir exists. (The old
behaviour also counted `~/.config/<emu>/...`, so a deleted-but-previously-launched emu stayed
visible; that was the bug.)

- **Citron / Eden**: reuse the AppImage glob patterns from `es_find_rules._RULES["CITRON"|"EDEN"]`
  (via `dict(...).get(name, ())`), so detection stays in lockstep with the real find rules
  (`~/Applications/*itron*.AppImage`, `~/.local/share/applications`, `~/.local/bin`, `~/bin`, ...).
- **Ryujinx**: has no custom rule (it uses ES-DE's BUNDLED RYUJINX find rule), so
  `_RYUJINX_BINARY_GLOBS` mirrors that rule -- `~/Applications/*yujinx*.AppImage` (+ the other
  AppImage dirs), the EmuDeck extracted build `~/Applications/publish/Ryujinx`, the flatpak exports
  (`io.github.ryubing.Ryujinx` / `org.ryujinx.Ryujinx`), and a `Ryujinx`/`ryujinx` binary on `$PATH`
  (`shutil.which`). Note this Deck's Ryujinx is an AppImage (`ryujinx-canary-1.3.328-x64.AppImage`);
  keep only ONE `*yujinx*.AppImage` in `~/Applications` or the find-rule glob resolves ambiguously.
- Unknown members return True (never hide a future member). Pinned by
  `tests/test_citron_sections.py::StrictDetection`. Pure Python; ships on a MAD restart, no rebuild.
  (Recorded 2026-07-03.)
