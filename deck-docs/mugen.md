# MUGEN / Ikemen GO on the Deck (MAD tile)

Cache of how MUGEN runs on this Deck and how the MAD tile drives it, so it does not
have to be re-derived from the code. Recorded 2026-07-19.

Sources:
  - Upstream engine: Ikemen GO (github.com/ikemen-engine/Ikemen-GO).
  - Our implementation: the launchers repo (mmadalone/mad) files named below;
    shipped in commits 91955ce, bdaea9d, 06c27ca, 0ef9446.
  - Engine specifics below were VERIFIED on-device 2026-07-19 (the bundled SDL, the
    live config format, the game layout), not assumed. Where an early plan guessed
    (JSON config, "detect motif fresh"), the guess was WRONG and is corrected here.

Related: deck-docs/mugen-ikemen-symlinks.md (the Rule #4 symlink exception),
deck-docs/on-the-go.md, memory `mugen-ikemen-tile` + simple-memory (mugen Phases 1-6,
8BitDo pads).

## Engine reality (the corrections)

- Every MUGEN game runs on **Ikemen GO** (a Go re-implementation of the MUGEN engine),
  NOT the original Elecbyte MUGEN. 22 games: 21 launch via Ikemen GO, 1 is a native
  Linux Ikemen build (TMNT, tmntxjlTurboRB).
- **The engine reads `save/config.ini`, a sectioned comment-heavy INI** (gopkg.in/ini.v1),
  NOT JSON. Each game has its own `save/config.ini`.
- **`save/config.json` is DEAD.** The 21 stray config.json files in the game folders are
  inert 2024 leftovers from an older side-by-side ikemen-go-v0.99.0 install. The current
  engine ignores them. Never write game config to config.json.
- **Ikemen GO bundles its OWN SDL 2.0.18** at
  `~/Emulation/tools/ikemen-go/lib/libSDL2-2.0.so.0.18.2`, NOT the system SDL (2.32.x).
  This matters for input (see the HIDAPI landmine below).
- Renderer: `[Video] RenderMode` is `OpenGL 3.3` or `Vulkan 1.3`. mugen.sh sets Vulkan
  once (guarded by a `.deck-vulkan-set` marker), then the config tree owns it.

## Game layout

Each game is a self-contained Ikemen GO install under `~/ROMs/mugen/<folder>/`, launched
by a `~/ROMs/mugen/<name>.mugen` script. The `.mugen` basename is the game IDENTITY (it
matches the ES-DE ROM + media), and is often NOT the config folder name
(AvengersVsX-Men.mugen launches folder `AvX`), so the folder is resolved by PARSING the
launcher's exec line, never guessed from the filename.

The `.mugen` exec line is `mugen.sh <mode> <target>` with three modes:
  - `ikemen <folder>`      run via the shared Ikemen GO build (the default; 21 games)
  - `native <folder/bin>`  run a self-contained native Linux Ikemen binary (1 game, TMNT)
  - `win <path.exe>`       run a Windows build via Proton (legacy path, currently unused)

## mugen.sh (the launcher)

`mugen.sh` (repo root) stays the PARENT process (does NOT `exec` the engine in ikemen
mode) so it can own the input merger and the on-the-go restore. Per mode:

- Bootstrap: symlink Ikemen GO's `external/`, `data/*`, `font/*` into the game folder if
  absent (the Rule #4 exception, see mugen-ikemen-symlinks.md).
- Motif: resolved READ-ONLY from `config.json`.Motif (authoritative) then a
  `data/mugen.cfg` / `system.def` fallback, passed to the engine with `-r <motif>`. Never
  written into config.ini. NOTE: a fresh detection-first order would regress MvC2 (it needs
  `data/Mvc2/system.def`, not the top-level one), which is why config.json.Motif wins.
- Input: spawns the MAD Pad merger and whitelists only its twins (see below).
- On-the-go: applies the handheld resolution downshift before launch and restores it after
  (ikemen AND native branches; the native branch was wired in commit 06c27ca).
- Launches the engine in the BACKGROUND and waits, so a crash/quit path can restore state
  and unwind the merger.

## MAD tile (Utilities > MAD CONTROL PANEL > Standalones > M.U.G.E.N)

Python-only (no ES-DE fork rebuild). Game-first: pick a game once, then edit its pages.

- **Config tree** (`lib/madsrv/mugen_cmds.py`): Per-game > Settings edits that game's
  `save/config.ini` (Video / Audio / Gameplay groups) through the shared byte-preserving
  cfgutil engine (comments + alignment kept, one-time .bak, refused while the game runs).
  NEVER exposes Motif or the `[Joystick_*]` / `[Keys_*]` input blocks. A game that has not
  been launched yet is flagged "launch once to create its config".
- **Input**: the shared `gamepad`/backends.describe page exposes all MAD Pad knobs
  (pad families + seat priority, stick gate box/radial, stick deadzone, handheld fallback,
  and the X-Arcade warn toggle, folded in).
- **On-the-go**: per-system watt cap + a MUGEN-specific handheld resolution (see below).

## Input pipeline (the MAD Pad merger)

MUGEN reuses the OpenBOR virtual-pad merger, `mad-openbor-pads.py --backend mugen`. It
grabs the real player pads (EVIOCGRAB), de-rotates the X-Arcade stick, digitizes gamepad
sticks to a d-pad, and emits ordered canonical uinput twins named "MAD Pad P{n}"
(vid 0x4d41, pid 0x0002..0x0005). mugen.sh whitelists ONLY those twins, so Ikemen seats
them P1..P4 in OUR order (from `[backends.mugen].pad_classes`, X-Arcade halves first).
Ikemen caps at 4 players. Native SDL honours the twin whitelist for every device.

- **Recognition**: Ikemen opens pads via SDL_GameControllerOpen, so a twin is only usable
  if it has a gamecontrollerdb mapping. mugen.sh exports SDL_GAMECONTROLLERCONFIG from
  `data/mugen-twins.gamecontrollerdb`, a dpad-only mapping whose GUIDs are computed against
  Ikemen's SDL 2.0.18 (vid/pid only, no name hash). Dropping the stick axes from that
  mapping is what makes a gamepad stick read as the D-PAD in-game.
- **Canonical joystick config** (`lib/mugen_cfg.py`): on a merger launch, rewrites every
  game's `[Joystick_Pn]` to the standard binding (de-rotating hand-tuned X-Arcade configs),
  byte-preserving, so all seats share one map. Stale configs self-heal on the next launch.
- **Stick gate**: `[backends.mugen].stick_gate = "radial"` + `stick_deadzone = 35`. The
  radial gate treats the stick as a vector (one engage radius + 8-way angle snap) so
  diagonals engage at the same push as cardinals (fighters need clean quarter-circles). The
  per-axis "box" gate (OpenBOR's default) makes diagonals need ~41% more deflection and
  feels buggy. MUGEN opts into radial; OpenBOR now does too.

### LANDMINE: SDL 2.0.18 HIDAPI bypass (root cause of the first input failure)

Ikemen's bundled SDL 2.0.18 has a HIDAPI joystick driver that reads pads over hidraw,
BYPASSING both the SDL device whitelist AND the merger's evdev grab. Result: Ikemen opened
the LIVE DualSense and bound its analog stick, ignoring the twin. FIX: mugen.sh exports
`SDL_JOYSTICK_HIDAPI=0` (forces the evdev path, so the whitelist hides the raw pads and the
grab bites). openbor.sh does the same. LESSON: verify input against the target's OWN
libraries; a system-SDL test hid this.

## On-the-go (handheld)

- **Watt cap**: rides the shared framework (general default in `[handheld]`, per-system
  override in `[systems.mugen.handheld]`, applied by the game-start power hook). Per-game
  watt cap was intentionally SKIPPED (marginal for light 2D fighters).
- **Resolution** (`lib/mugen_res.py`): MUGEN-specific, NOT the handheld_res multiplier rail.
  In HANDHELD it downshifts each game's own `[Video] GameWidth/GameHeight` by a scale
  percent (aspect preserved, even values), snapshots the resting size to a sidecar
  (`save/.mad-hhres-restore`), and restores on exit. A leftover sidecar from a crash is
  swept on the next apply, and restore() will NOT clobber a resting value the user changed
  after a downshift (the sidecar records both resting and downshift; it only reverts an
  untouched downshift). Stored: general in `[systems.mugen.handheld].res`, per-game in
  `[backends.mugen.pergame.<folder>].hhres`, keyed by the config FOLDER name.
  - The per-game picker shows the REAL pixels for that game (Full 1280x720 / High 1024x576 /
    Medium 832x468 / Low 640x360). The all-games page stays a percent scale, because it
    spans games of different resting sizes so no single pixel number is honest across them.

## 8BitDo pads (OpenBOR, shared merger)

The merger is SHARED by OpenBOR and MUGEN, so 8BitDo support (commit 0ef9446) lives in the
same translation tables. FC30 / FC30 II (2dc8:2810) and NES30 Pro (2dc8:3820) now translate
via a per-class `ABS_ROLE_OVERRIDE` in `lib/openbor_maps.py`. MUGEN leaves 8BitDo listed but
OFF (Miquel: not for MUGEN). Full axis layouts: simple-memory `openbor-8bitdo-pads`.

## Quick landmine list

- config.ini is live; config.json is dead. Motif is `-r` only, never in the INI.
- Ikemen bundles SDL 2.0.18; export SDL_JOYSTICK_HIDAPI=0 or the twins are ignored.
- The twin gamecontrollerdb GUIDs are SDL-2.0.18-specific (regenerate if the engine's SDL
  changes).
- The box stick gate stays the merger's code default (OpenBOR's 42 pad tests assert it);
  radial is per-backend opt-in.
- Handheld resolution and the config tree BOTH touch GameWidth/GameHeight: the config tree
  owns the resting value, on-the-go is snapshot/downshift/restore. Never last-writer-wins.
- Folder name != .mugen basename: always parse the launcher exec line for the folder.
