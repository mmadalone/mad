# On-the-go (handheld) auto-profiles

Cache of how the on-the-go feature works, so it does not have to be re-derived from the code.
Internal MAD feature (no external tool). Recorded 2026-07-09.

## What it does

When the Deck is played HANDHELD, participating heavy systems automatically get:
  1. a TDP watt cap (the main heat/battery lever), and
  2. a lower internal render resolution,
both restored exactly when docked / on exit. Plus the built-in Deck pad becomes Player 1 on
every system (RetroArch needed a fix; standalones already did this). Docked = NO change.

Honest scope notes:
  - The watt cap does almost all the real heat/battery work. Internal resolution only helps
    GPU-bound titles; it is near-useless on CPU-bound ones (BOTW, RPCS3, heavy PS2).
  - There is no real "fps cap" lever: the session gamescope owns the 40 Hz panel and an ES-DE
    child cannot set it per-game. The feature does not try.

## Detection

Physical display via `lib/deck_state.py` (`is_docked`/`is_handheld`): an external HDMI/DP
connector that is `connected` AND `enabled` = docked; the internal panel = handheld. Pure
sysfs (`/sys/class/drm/card*-{HDMI,DP}*/status` + `enabled`), works in every launch context.
Overrides (first wins): env `MAD_FORCE_CONTEXT=handheld|docked` (test hook) > policy
`[handheld].force` > `[handheld].detect="manual"` (=> docked) > the DRM check.

## Config (controller-policy.toml / .local.toml)

    [handheld]
    enabled = true            # master switch
    detect  = "display"       # "display" | "manual"
    force   = ""              # "" | "handheld" | "docked"
    default_watt_cap = 12     # W (stock is 15)
    [systems.<sys>.handheld]
    enabled  = true
    watt_cap = 12             # or omit to inherit default_watt_cap
    res      = "native"       # "native" | "2x" | "inherit"

Edited from the MAD "On-the-go" sidebar page (`lib/madsrv/onthego_cmds.py`, C++ page reuses
`GuiMadPageStandaloneSections` + `GuiMadPageEmuSettings`, no new page class).

## The two levers

TDP watt cap (`lib/deck_power.py`): writes `power1_cap` (+ `power2_cap`) on the amdgpu hwmon
(resolve by name-glob) via Valve's whitelisted `/usr/bin/steamos-polkit-helpers/steamos-priv-write`
(no password / sudoers / setuid; base-image, survives updates; tries a direct write first since
the node is `root:deck` group-writable after the first priv-write of a boot). Only ever LOWERS,
self-floors at 4 W. NOTE: `ryzenadj` is dead on this OLED APU (model 145) - sysfs is the only path.

Internal resolution -- ONE backend-aware rail (`lib/handheld_res.py`, WS-B 2026-07-11). Own atomic
marker dir `~/Emulation/storage/controller-router/handheld-res/`; swept at launch-start AND game-end
(hooks 09/11); revert only if the file still holds the value we applied, so an in-emulator change is
kept. `sweep_all` also heals orphans in the legacy `{res,ra-res,dolphin-res}/` dirs for one release.
At each launch it DETECTS the emulator the game actually runs with -- the RA core via
`retroarch_cfg.launched_core` (honours the per-game `<altemulator>`), else the standalone via
`es_systems.standalone_backend_id(resolved_command(...))` -- and writes THAT emulator's resolution.
The per-system picker stores an abstract FACTOR (native/2x/3x/4x/6x/8x/inherit); each backend snaps
it DOWN to its nearest real value; only ever LOWERS (never raises above the docked/resting value).
  - Covered backends (`REGISTRY`): Beetle PSX HW (`beetle_psx_hw_internal_resolution`, native
    `1x(native)`), Flycast (`reicast_internal_resolution` WxH), Kronos (`kronos_resolution_mode`,
    native `original`) + YabaSanshiro (`yabasanshiro_resolution_mode`, rungs original/2x/4x) for
    Saturn, SwanStation (`duckstation_GPU.ResolutionScale`), Mupen64Plus-Next (43+169 screensize),
    standalone Dolphin (`InternalResolution`, per-game `GameSettings/<id>.ini [Video_Settings]` else
    global `GFX.ini [Settings]`), PCSX2 (`[EmuCore/GS] upscale_multiplier`), RPCS3 (`Video: Resolution
    Scale`). An uncovered emulator (Redream, Ymir, ParaLLEl-N64, standalone Flycast/Kronos, ...) is a
    clean no-op + log. NOTE: the old code wrote invalid native tokens (`1x` to Beetle, `1X` to
    Kronos) -- fixed here.
  - Switch (Eden/Citron/Ryujinx): the emulator's own `use_docked_mode`/`docked_mode` (720p vs
    1080p base), driven by `switch_bind._switch_dock_state` -> `deck_state`. Governed by the per-emu
    "Dock detection" toggle, NOT `[systems.switch.handheld]`; NOT part of handheld_res.
  - Wii U (Cemu): resolution = graphic-pack PRESET, per-game via `lib/cemu_res.py` (own rail + hooks
    08/10); handheld_res does not touch wiiu. The watt cap still applies.

Per-game precedence: pcsx2 `gamesettings/<SERIAL>_<CRC>.ini`, rpcs3 `custom_configs/config_<SERIAL>.yml`,
Dolphin `GameSettings/<GameID>.ini`, RA per-content `<stem>.opt` override the global; the rail edits
whichever the game reads.

Handheld input: standalones already bind the Deck pad when no external pad is present
(`handheld_class`). RetroArch flips `input_joypad_driver` udev->sdl2 when handheld
(`controller-router._ra_handheld_driver`, gated to real RA launches via `launched_core() is not
None`), restored to udev at game-end; sdl2 sees the lizard-mode Deck pad that udev cannot.

## Wiring

  - Hooks (active copy in `~/ES-DE/scripts/`): game-start `03-mad-power.sh` + `09-handheld-res.sh`;
    game-end `07-mad-power-restore.sh` + `11-handheld-res-restore.sh`. The old per-emulator res rails
    (inline RA in `controller-router`, PS2/PS3 in `switch_bind.bind()`, Dolphin hooks 06/08) are
    RETIRED, superseded by the unified 09/11 (`install.sh` backs up + removes the deployed 06/08).
  - Session-start orphan sweep: `~/.config/systemd/user/mad-power-sweep.service` (WantedBy=
    graphical-session.target; runs `deck_power.py sweep`). Fires on boot + every Game<->Desktop
    switch. Does NOT catch a same-session ES-DE-crash-then-Steam-launch (no systemd signal there);
    that self-heals on the next reboot or ES-DE launch. `power1_cap` re-creates at default on
    reboot, so a stuck cap can never survive a reboot.

## Conflict caveat (important)

Steam's per-game TDP slider (QAM) and Decky PowerTools/SimpleDeckyTDP also write `power1_cap`.
If Steam owns the ES-DE shortcut's TDP they fight and Steam wins on resume/QAM change. Keep the
Steam per-game TDP slider OFF for the ES-DE shortcut so MAD owns the cap.

## Revert paths

Power: delete/restore `~/Emulation/storage/controller-router/.mad-power-restore` (or reboot).
Res: the marker dirs above (swept automatically). MAD page: revert the fork AppImage from
`~/Applications/ES-DE-MAD.AppImage.pre-onthego-2026-07-09`.

## Tests

`tests/test_{deck_power,switch_res,ra_res,dolphin_res,onthego_cmds}.py` (byte-stable round-trips,
driven by `MAD_FORCE_CONTEXT` + module-constant redirection; no hardware). Run:
`cd ~/Emulation/tools/launchers && python3 -m unittest discover -s tests` + `mad-backend.py --selfcheck`.
