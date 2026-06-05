# ES-DE Controller / Gun Assignment Router

Routes controllers to RetroArch ports based on a per-system policy. Built
2026-05-29.

## What it does

Before every game launches from ES-DE:

1. Classifies the game (system name + Pew-Pew-Pew custom collection lookup).
2. Enumerates connected input devices via `/dev/input/event*`.
3. Resolves a per-system priority list to actual present devices.
4. Writes RetroArch's name-based device reservation into the per-game
   override `.cfg` (under `~/.var/app/.../config/<Core>/<rom_basename>.cfg`).
5. For lightgun (Pew-Pew-Pew) games: also pins `input_player[1,2]_mouse_index`
   to the Sinden smoothed virtual devices (or raw Sinden if smoother is off).
6. Shows a fullscreen blocking warning when:
   - A lightgun game is launched without any Sinden connected, or
   - A console game (NES/SNES/etc) is launched with only the X-Arcade.
   On **Cancel**, the wrapper exits non-zero and ES-DE aborts the launch.

After the game exits, `game-end/00-controller-router.sh` strips the router's
sentinel block from each per-game `.cfg`, leaving any bezel-project lines
untouched.

## Required one-time setup

### Disable Steam Input on the ES-DE shortcut

Per-controller routing only works when Steam Input isn't merging all pads
into one virtual Xbox 360 pad.

1. In Steam (Big Picture or Game Mode) → **Library → ES-DE** (the non-Steam
   shortcut you launch ES-DE from).
2. Gear icon → **Properties → Controller**.
3. Set **"Override for ES-DE"** to **"Disable Steam Input"**.
4. Restart ES-DE.

Trade-off accepted: the Steam Deck's built-in gyro, trackpads, and back
paddles produce no input under ES-DE. Face buttons, sticks, and triggers
work via raw evdev.

Verify after restart by launching a NES game with an 8BitDo plugged in — the
router should write a `<rom>.cfg` with `input_player1_reserved_device =
"8BitDo …"` lines under each NES core dir.

## Files

```
~/Emulation/tools/launchers/
├── controller-router.py            # main orchestrator
├── controller-router-wrap.sh       # invoked from es_systems.xml
├── controller-policy.toml          # per-system priority lists (EDIT THIS)
├── lib/
│   ├── devices.py                  # evdev enumeration
│   ├── classify.py                 # GameContext + Pew-Pew lookup
│   ├── retroarch_cfg.py            # per-game .cfg sentinel-block writer
│   └── warning_dialog.py           # fullscreen Proceed/Cancel tkinter
└── sinden-update-retroarch-mouseindex.py    # thin caller of lib/devices.py

~/ES-DE/scripts/game-end/
└── 00-controller-router.sh         # cleanup hook (strips sentinel)

~/ES-DE/custom_systems/es_systems.xml
                                    # every <command> wrapped with the router
~/Emulation/storage/controller-router/router.log
                                    # runtime log
```

## How to add or change a routing policy

Edit `controller-policy.toml`. Each `[systems.<name>]` table has:

- `category` — informational; "arcade", "console", "lightgun", "handheld", or
  "tools".
- `warn_when_only_xarcade` (optional bool) — show the "best with gamepad"
  blocking dialog when only the X-Arcade is detected. Used on console
  systems.
- `require_sinden` (optional bool) — show the "connect a gun" blocking
  dialog if no Sinden is detected. Used on `__pew_pew_pew__` only.
- `inherits` (optional string) — point at another system's `ports`. Shallow
  (one-hop) only.
- `ports` — list of per-port priority lists. Port 1 is the first list,
  port 2 the second, etc. Each priority list is a list of device-name
  substrings; the router walks it and picks the first present device. If
  no port priority matches, that port gets no reservation (RetroArch auto-
  assigns).

## How to add or remove systems from routing

Routing fires only for systems with a `<system>` entry in
`~/ES-DE/custom_systems/es_systems.xml` whose `<command>` lines are wrapped
with `controller-router-wrap.sh`. Currently wrapped systems:

| arcade-like | console | other |
|---|---|---|
| naomi, fba, pcenginecd, saturn, segacd, dreamcast | nes, snes, genesis, megadrive, n64 | gameandwatch, mugen, sinden |

To add another system: copy a similar `<system>` block and edit the
`<name>`, `<fullname>`, `<extension>`, and the wrapped `<command>` lines.
Match the format:

```xml
<command label="…">
  /home/deck/Emulation/tools/launchers/controller-router-wrap.sh
  <SYSTEMNAME> %ROM% "%BASENAME%" "<FULLNAME>"
  --
  <ORIGINAL_EMULATOR_COMMAND_AND_ARGS>
</command>
```

The router will look up `<SYSTEMNAME>` in `controller-policy.toml`. If no
policy is defined, the router exits 0 without writing anything (no harm).

## How to disable routing temporarily

Two options:

- **Per-session**: revert ES-DE to the unwrapped commands. Restore from
  the backup:
  ```
  cp ~/ES-DE/custom_systems/es_systems.xml.bak-pre-router-wrap \
     ~/ES-DE/custom_systems/es_systems.xml
  ```
  Restart ES-DE.
- **Per-game**: open ES-DE's metadata editor for that game, change the
  alternative emulator to one of ES-DE's bundled (unwrapped) defaults.

## Logs and debugging

- Router log: `~/Emulation/storage/controller-router/router.log` — every
  setup / cleanup invocation logs the resolved policy, devices present,
  resolved ports, and files written.
- ES-DE's own log: `~/ES-DE/logs/es_log.txt` — look for the wrapper command
  line ES-DE is executing.
- RetroArch's log: launch RetroArch with `--verbose` to see SDL2 joypad
  enumeration and which controller it assigned to each port.

## Known limitations

- Non-RetroArch standalone emulators (Dolphin, Flycast Standalone, Supermodel,
  Ymir, Mednafen Standalone) are wrapped by the router for the warning logic
  but the router does NOT write controller-routing config for them — they
  have their own input-config files that the existing user-maintained
  per-emulator setup handles.
- Mouse-index pinning for Sinden uses numeric indices (RetroArch doesn't yet
  support name-based mouse reservation). There's a small hot-plug race
  window (milliseconds) between the router exiting and RetroArch's own
  enumeration. The router runs the detection late in its setup phase to
  shrink that window.
- The custom `<system>` entries in `es_systems.xml` mirror ES-DE's defaults
  at the time of writing (May 2026). If ES-DE bumps its default commands in
  a future release, the wrapped overrides will go stale — sync manually.
