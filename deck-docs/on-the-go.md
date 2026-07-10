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

Internal resolution (per launch path, own atomic marker rail per touched config; markers under
`~/Emulation/storage/controller-router/{res,ra-res,dolphin-res}/`; swept at launch-start AND
game-end; revert only if the file still holds the value we applied, so an in-emulator change is
kept):
  - PS2/PS3 (`lib/switch_bind.py` res): PCSX2 `[EmuCore/GS] upscale_multiplier` (native/2x);
    RPCS3 `Video: Resolution Scale`. `_RES_SPEC`.
  - Switch (Eden/Citron/Ryujinx): the emulator's own `use_docked_mode`/`docked_mode` (720p vs
    1080p base), driven by `switch_bind._switch_dock_state` -> `deck_state` (feature on) with a
    legacy controller-presence fallback (feature off). Governed by the per-emu "Dock detection"
    toggle, NOT `[systems.switch.handheld]`.
  - RetroArch heavy cores (`lib/ra_res.py`): per-content `.opt` (else folder `<Core>.opt`) via
    `retroarch_cfg.read_opt/write_opt`. Cores: Beetle PSX HW, Flycast (`reicast_internal_resolution`),
    Kronos (Saturn HW; Beetle Saturn has no upscale), Mupen64Plus-Next (43+169 screensize;
    ParaLLEl-N64 skipped). native only (no 2x).
  - GC/Wii (`lib/dolphin_res.py`): `InternalResolution` in the per-game `GameSettings/<GameID>.ini`
    `[Video_Settings]` if it overrides, else the global `GFX.ini [Settings]`. native only.
  - Wii U (Cemu): NO scalar (resolution = graphic-pack presets). NOT auto-swapped - the watt cap
    applies, resolution stays manual via Cemu's graphic packs (the Cemu MAD tile exposes them).

Per-game precedence: pcsx2 `gamesettings/<SERIAL>_<CRC>.ini`, rpcs3 `custom_configs/config_<SERIAL>.yml`,
Dolphin `GameSettings/<GameID>.ini` override the global; the rail edits whichever the game reads.

Handheld input: standalones already bind the Deck pad when no external pad is present
(`handheld_class`). RetroArch flips `input_joypad_driver` udev->sdl2 when handheld
(`controller-router._ra_handheld_driver`, gated to real RA launches via `launched_core() is not
None`), restored to udev at game-end; sdl2 sees the lizard-mode Deck pad that udev cannot.

## Wiring

  - Hooks (active copy in `~/ES-DE/scripts/`): game-start `03-mad-power.sh` + `06-dolphin-res.sh`;
    game-end `07-mad-power-restore.sh` + `08-dolphin-res-restore.sh`.
  - RA rail: inline in `controller-router.py` `_setup`/`_cleanup`.
  - PS2/PS3 rail: `switch_bind.bind()`.
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
