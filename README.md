<p align="center">
  <img src="art/banner.png" alt="MAD" width="600">
</p>

<h1 align="center">MAD — Multi-Pad Arcade Dashboard</h1>

<p align="center">
  A gamepad-native control panel and controller-routing stack for
  <a href="https://es-de.org">ES-DE</a> on the <b>Steam Deck</b>,
  built around a lightly source-patched ES-DE fork.
</p>

---

> ⚠️ **Personal project / work-in-progress.** This is the setup that runs one specific Steam Deck (EmuDeck + ES-DE, an X-Arcade Tankstick, two Sinden lightguns, a Mayflash DolphinBar, and an assortment of Bluetooth pads). It's published mainly as a **backup and reference** — paths, hardware fingerprints and policies are tailored to that rig, so it is **not** a turnkey installer for other Decks. Cherry-pick freely.

## What is MAD?

On a Steam Deck running ES-DE in Game Mode you have *many* controllers (an arcade stick, lightguns, several pads) and *many* emulators, each with its own idea of how to assign players. MAD makes that coherent and controllable **without a keyboard or Desktop Mode**:

- 🎛️ **A control panel** (`router-config-gui.py`, opened from ES-DE's **Main Menu → Utilities → "MAD CONTROL PANEL"**) — a fullscreen, fully gamepad-navigable, ES-DE-themed UI for everything below: live device preview, per-system controller priority, player pinning, lightgun, splash screens, backups.
- 🔀 **A controller router** (`controller-router.py`) — runs at ES-DE game-start/-end hooks and writes each emulator's controller config so the right pad lands on the right player, every launch. Policy-driven (`controller-policy.toml`), never hard-coded behaviour.
- 🧩 **A patched ES-DE fork** (the `deck-patches` branch of this repo) — a handful of small source patches that the control panel relies on (see below).

## Highlights

- **Per-system & per-collection priority** — e.g. arcade systems default to the X-Arcade on P1/P2, console systems to gamepads; collections (e.g. lightgun games) override per-ROM.
- **Device pinning** — pin a *specific* physical pad to a player (`uniq:` Bluetooth-MAC / `port:` USB-port / `vidpid:` model key), globally or per-system, via the **Players** page (press-to-identify).
- **X-Arcade Tankstick** support, including **telling it apart from a real Xbox 360 pad** (both enumerate as `045e:02a1`) by its USB port — identified once from the **Preview** page.
- **Sinden lightguns** (2-player), **Mayflash DolphinBar** Wii-Remote detection, and **multi-pad** rigs handled as first-class cases.
- **Per-emulator backends** — RetroArch (per-game `reserved_device` overrides), plus standalone Cemu, Dolphin, PCSX2, xemu, RPCS3, Eden, Supermodel, Hypseus/Daphne, OpenBOR and MUGEN.
- **Live Preview** — see every connected controller (with battery %), what each system *would* route, and the DolphinBar Wii-Remote count, updating as you plug/unplug.
- **Resilience** — `deck-post-update.sh` detects what a SteamOS update wiped and restores it (Samba, Sinden deps, udev rules, the patched AppImage wrapper, Python GUI deps); backups via `deck-backup.sh`.
- **Steam-overlay input** — with Steam Input *off* (required for raw-evdev routing), ES-DE would double-input under the Steam overlay; MAD pairs the **PauseGames** Decky plugin with a fork patch that drops the buffered presses on resume.

## The ES-DE fork (`deck-patches`)

Small, rebase-able source patches on top of upstream ES-DE (`base/v3.4.1`), built into the AppImage the control panel uses:

| Patch | Why |
|---|---|
| Full-screen startup splash | Edge-to-edge custom splash on the Deck |
| `arg5` to game-start scripts | Pass the *launched-from* collection so launch screens & routing are correct |
| Honour `es_systems_sorting.xml` for custom collections | Stable custom-collection ordering |
| **"MAD CONTROL PANEL"** + **"Restart Steam (fix audio)"** menu rows | Launch MAD / recover audio from inside ES-DE |
| Drop queued input after a long pause | No replay of Steam-overlay presses on resume |

`upstream` = the official GitLab ES-DE; `origin` = this repo. Per ES-DE release the patches are rebased onto the new tag and the AppImage is rebuilt.

## Repository layout

```
main          ← this branch: the MAD tools (control panel, router, libs, scripts)
deck-patches  ← the patched ES-DE fork source (build the AppImage from here)
```

```
router-config-gui.py     the MAD control panel (Tkinter, fullscreen, gamepad-nav)
controller-router.py     the game-start/-end router
controller-policy.toml   routing policy (systems, collections, backends, pins, hardware)
lib/                     devices, per-emulator config writers, ES-DE/SDL helpers, GUI theme
*.sh                     emulator launchers, build/backup/restore, ES-DE hooks
art/  data/              icons, banner, UI sounds
```
> `controller-policy.local.toml` (your live overrides, written by the GUI) is **git-ignored**; copy `controller-policy.example.toml` to start your own.

## Building the ES-DE AppImage

The fork builds in an `esde-ubuntu` [distrobox](https://distrobox.it/) (ES-DE needs an Ubuntu toolchain; SteamOS's root is immutable):

```bash
cd ~/esde-build/ES-DE
git checkout deck-patches
distrobox enter esde-ubuntu -- bash ~/esde-build/ubuntu-build.sh   # → ES-DE_x64_SteamDeck.AppImage
```
Install it as `~/Applications/ES-DE-MAD.AppImage`; a tiny wrapper at `~/Applications/ES-DE.AppImage` regenerates the splash and execs it (with the stock AppImage kept as `.real`).

## Hardware this targets

Steam Deck (SteamOS 3.x / Game Mode / gamescope) · EmuDeck + ES-DE · X-Arcade Tankstick (Xbox mode) · 2× Sinden lightgun · Mayflash DolphinBar · DualSense / DualShock 4 / 8BitDo / Wii U Pro pads.

## Credits & licence

Built on [**ES-DE**](https://es-de.org) (EmulationStation Desktop Edition) by Leon Styhre — the `deck-patches` branch is a fork of its source; all credit for ES-DE itself goes upstream. MAD's own tooling and the fork patches are in this repo; see `LICENSE`.

<p align="center"><i>Made for one very over-configured Steam Deck. 🕹️</i></p>
