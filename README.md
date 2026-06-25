<p align="center">
  <img src="art/banner.png" alt="MAD" width="600">
</p>

<h1 align="center">MAD: Multi-Pad Arcade Dashboard</h1>

<p align="center">
  A gamepad-native control panel and controller-routing stack for
  <a href="https://es-de.org">ES-DE</a> on the <b>Steam Deck</b>,
  built around a lightly source-patched ES-DE fork.
</p>

<p align="center">
  <a href="https://github.com/mmadalone/mad/releases/latest"><img src="https://img.shields.io/github/v/release/mmadalone/mad?label=latest%20release&color=2ea043" alt="Latest release"></a>
  <a href="CHANGELOG.md"><img src="https://img.shields.io/badge/changelog-md-1f6feb" alt="Changelog"></a>
</p>

---

> ⚠️ **Personal project / work-in-progress.** Built for one specific Steam Deck (EmuDeck + ES-DE, an X-Arcade Tankstick, two Sinden lightguns, a Mayflash DolphinBar, and an assortment of Bluetooth pads). There's now a one-shot installer (below) that sets up the ES-DE + MAD **core** on any Deck, but the *controller routing* is inherently per-rig, so you configure your own pads in the GUI. Cherry-pick freely.

> 🎉 **`v0.4.0`** lets you **reorder and show/hide every entry** in the MAD sidebar (live **Apply**, no panel reopen), adds a **Wii-Remote navigation toggle** (ES‑DE → Main Menu → Input Device Settings), makes the **maintenance scripts portable** (they read your ROM/gamelist/media paths from ES‑DE instead of a hardcoded layout) with new **Hardware setup** and **Maintenance scripts** guide sections, and hardens **suspend** (correct `deep`/S3 on Steam Decks, robust across SteamOS updates) and post-update recovery. See the [release notes](https://github.com/mmadalone/mad/releases/tag/v0.4.0) and the [CHANGELOG](CHANGELOG.md).
>
> Earlier: **`v0.3.0`** added the interactive installer (a `whiptail` component picker + `install.conf` re-applied after SteamOS updates, capability-adaptive sidebar, shipped activation hooks, on-demand bezel downloads); **`v0.2.0`** was the first "share-readiness" release that installs cleanly on *anyone's* Steam Deck.

## Quick install

On a Steam Deck with **ES-DE** set up (EmuDeck is the easy way to get there, but it's optional; see *Standalone install* below), from a Desktop-Mode terminal:

```bash
curl -fsSL https://raw.githubusercontent.com/mmadalone/mad/main/install.sh | bash
```

It fetches the patched ES-DE AppImage, drops the MAD tools in place, installs + selects the MAD theme (`pixel-es-de`, on by default, deselectable in the picker), installs the ES-DE router + activation hooks + Python deps, sets the correct suspend mode for your Deck model, and seeds a default controller policy; idempotent + recoverable (it never deletes; a pre-existing non-MAD dir is moved to a `_TMP` backup and hooks are `.bak`'d). Two things it can't safely script (it prints them at the end): **add ES-DE to Steam and set Steam Input OFF**, and configure your pads in the **MAD CONTROL PANEL**. On a fresh install it opens a small **component picker** (theme, Sinden lightgun, Samba) and records your choices in `install.conf`, which `deck-post-update.sh` then reuses after a SteamOS update so it only re-applies what you opted into. `--express` takes the defaults (no prompt); `--reconfigure` re-opens the picker on an existing install. Pass `--dry-run` first to see every action. The manual, step-by-step path is in *Install / setup* below.

> **Switches:** `--dry-run` (preview every action, change nothing) · `--standalone` (force no-EmuDeck mode) · `--express` (accept the defaults, skip the picker) · `--reconfigure` (re-open the picker on an existing install). `install.sh --help` lists them.
>
> ⚠️ **After it finishes, reboot or log out / back in before launching ES-DE.** The installer adds you to the `input` group, and until you re-login MAD reads **zero** controllers (it looks like "no pads detected", but it isn't a bug). Run it in an **interactive Desktop-Mode terminal** so the component picker and the EmuDeck-vs-standalone prompt actually appear; a piped or non-interactive run (`ssh -T`, a script) silently takes the defaults.

## Standalone install (without EmuDeck)

EmuDeck is **recommended** (it's the easy way to install the emulators and BIOS) but it's no longer **required**. If `install.sh` doesn't find EmuDeck/ES-DE it offers a **standalone** mode (or force it with `./install.sh --standalone`):

```bash
curl -fsSL https://raw.githubusercontent.com/mmadalone/mad/main/install.sh | bash -s -- --standalone
```

In standalone mode MAD seeds the `~/ES-DE` config itself and generates a **minimal** `custom_systems/es_systems.xml`: it wires `switch`/`ps2`/`ps3`/`xbox` through MAD's launch-time controller binders and adds MAD's own special systems (`sinden`, `daphne`, `openbor`, `model2`, `mugen`, `naomi`, `gameandwatch`). Every other system (`snes`, `psx`, `dreamcast`, …) inherits ES-DE's bundled definition, and ES-DE's `es_find_rules.xml` resolves each emulator to wherever you installed it, so ~195 systems just work, no inventory file needed.

MAD is a **control panel + a patched ES-DE**, not an emulator installer. Standalone means **you provide**:

1. **The emulators**: anywhere ES-DE looks: `flatpak install` (e.g. `net.pcsx2.PCSX2`, `net.rpcs3.RPCS3`), or drop AppImages in `~/Applications`. ES-DE shows *"emulator not found"* until then.
2. **ROMs** under `~/ROMs/<system>` (ES-DE's native rom dir, e.g. `~/ROMs/snes`, `~/ROMs/ps2`). **Not** `~/Emulation/roms`.
3. **BIOS / firmware / keys** per emulator, as each emulator requires.

MAD's own data still lives under `~/Emulation` by default; override the root with `MAD_DATA_ROOT=/your/path` if you keep your emulation tree elsewhere.

## What is MAD?

On a Steam Deck running ES-DE in Game Mode you have *many* controllers (an arcade stick, lightguns, several pads) and *many* emulators, each with its own idea of how to assign players. MAD makes that coherent and controllable **without a keyboard or Desktop Mode**:

- 🎛️ **A control panel** (opened from ES-DE's **Main Menu → Utilities → "MAD CONTROL PANEL"**), rendered NATIVELY inside ES-DE's window (`GuiMadPanel` in the fork + the `mad-backend.py` NDJSON daemon in this branch): a fullscreen, fully gamepad-navigable UI for everything below: live device preview, per-system controller priority, player pinning, backends, quit combos, lightgun (incl. live camera preview), Daphne binds, three live controller testers, splash screens, backups. Wii Remotes on a mode-4 DolphinBar navigate it (and all of ES-DE) via `wii-nav-bridge.py` (toggle under **Main Menu → Input Device Settings**).
- 🔀 **A controller router** (`controller-router.py`): runs at ES-DE game-start/-end hooks and writes each emulator's controller config so the right pad lands on the right player, every launch. Policy-driven (`controller-policy.toml`), never hard-coded behaviour.
- 🎨 **Fully themeable**: the active ES-DE theme can recolor the whole panel and swap its icons: drop `router-config/mad-theme.xml` (global palette) and per-page `<pagename>-theme.xml` files into the theme (pages including preview, systems, priority, players, quit-combo, standalones, retroarch, bezelproject, lightgun, x-arcade, gamepads, splash, backup, sidebar). Colors: `frame/primary/secondary/title/selector/red/green/separators/panelDimmed/buttonFlat/helpText` (hex RGB[A], `${var}` substitution); icons: `<icon name="sidebar">./icons/x.png</icon>`. **No files = the stock look**; see `art/mad-theme-examples/pixel-es-de/` for a complete reference set. Themes can also ship UI fonts (any `*.ttf`/`*.otf` in the theme) selectable under **UI Settings → THEME FONTS**.
- 🧩 **A patched ES-DE fork** (the `deck-patches` branch of this repo): a handful of small source patches that the control panel relies on (see below).

## Highlights

- **Per-system & per-collection priority**: e.g. arcade systems default to the X-Arcade on P1/P2, console systems to gamepads; collections (e.g. lightgun games) override per-ROM.
- **Device pinning**: pin a *specific* physical pad to a player (`uniq:` Bluetooth-MAC / `port:` USB-port / `vidpid:` model key), globally or per-system, via the **Players** page (press-to-identify).
- **X-Arcade Tankstick** support, including **telling it apart from a real Xbox 360 pad when the X-Arcade Tankstick is on Xbox mode** (both enumerate as `045e:02a1`) by its USB port, identified once from the **Preview** page.
- **Sinden lightguns** (2-player), **Mayflash DolphinBar** Wii-Remote detection, and **multi-pad** rigs handled as first-class cases.
- **Optional Home Assistant LED**: the Sinden driver can fire HA webhooks to switch a TV LED strip on/off with lightgun games. Set the base URL + webhook IDs in `sinden.conf` (gitignored; seeded from `sinden.example.conf`). There's no token; an HA webhook is triggered by its secret URL, so use the random IDs HA generates and keep `local_only` on.
- **Per-emulator backends**: RetroArch (per-game `reserved_device` overrides), plus standalone Cemu, Dolphin, PCSX2, xemu, RPCS3, Eden, Supermodel, Hypseus/Daphne and OpenBOR.
- **Namco System 246/256 tile**: a Standalones tile for **[pcsx2x6](https://github.com/mmadalone/pcsx2x6/tree/deck-patches)**, our Steam-Deck fork of the [PS2Homebrew-arcade System 246/256 emulator](https://github.com/PS2Homebrew-arcade/pcsx2x6) (Tekken, Soul Calibur, Time Crisis, Vampire Night, and the rest of the 246/256 library). Some of these games use a pad/stick and some use a Sinden lightgun; our fork adds Game-Mode Sinden lightgun aim (single-player) for the gun games. The tile has the full standalone section set: a Settings page (graphics, the JVS **Testmode** gun-calibration toggle) over its *portable* `PCSX2.ini`; an **Input-mapping** page (the DualShock2 ports for pad games, plus two **USB ports** where you pick None / HID Mouse / **Light Gun** and bind the gun's trigger, pedal, start and coins); a **Lightgun** page shown when a USB port is Light Gun (per-gun crosshair image and size, the Sinden white-border overlay, and a one-press **Start Sinden guns** button); and a **Controllers** (pads to players) page that assigns a gamepad such as a DualSense to a player (used directly in the pad games, or alongside the gun in the lightgun games; System 246/256 is 1-2 players). **You install the fork yourself**: a prebuilt AppImage is on its [releases page](https://github.com/mmadalone/pcsx2x6/releases), or build the [`deck-patches`](https://github.com/mmadalone/pcsx2x6/tree/deck-patches) branch. You also add the `pcsx2x6` system and supply the arcade **COH-H** BIOS plus each game's security dongle and disc image; those are copyrighted, so **dump them from hardware you own** (none are distributed here, and MAD doesn't fetch them). MAD adds the on-screen config, controller binding, and Sinden launch glue once you have `pcsx2x6` games.
- **Live Preview**: see every connected controller (with battery %), what each system *would* route, and the DolphinBar Wii-Remote count, updating as you plug/unplug.
- **Resilience**: `deck-post-update.sh` re-applies what a SteamOS update wiped: the always-on bits (patched-AppImage wrapper, Python deps, router hooks, model-aware suspend mode) plus whatever you opted into at install (Sinden deps + udev, Samba, both opt-in, Samba off by default), each gated by `install.conf` so an opted-out component isn't restored *or* nagged about. If the patched AppImage itself is gone it's pulled from the CI release by `deck-fetch-esde.sh` before falling back to a local rebuild. Backups via `deck-backup.sh`.
- **CI-built AppImage**: every push to `deck-patches` has **GitHub Actions** build the patched ES-DE AppImage (Ubuntu 22.04, matching the Deck's glibc) and publish it to a rolling release, so recovery is a download, not a 30-min rebuild.
- **Steam-overlay input**: with Steam Input *off* (required for raw-evdev routing) ES-DE would double-input under the Steam overlay. The fork now **pauses input + preview videos natively** when it loses gamescope keyboard focus, no plugin required. The **PauseGames** Decky plugin is optional, only for the few game-context overlay spots (home/notes/guide/resume).

## The ES-DE fork (`deck-patches`)

Small, rebase-able source patches on top of upstream ES-DE (`base/v3.4.1`), built into the AppImage the control panel uses:

| Patch | Why |
|---|---|
| Full-screen startup splash | Edge-to-edge custom splash on the Deck |
| `arg5` to game-start scripts | Pass the *launched-from* collection so launch screens & routing are correct |
| Honour `es_systems_sorting.xml` for custom collections | Stable custom-collection ordering |
| **Utilities + Quit menu rows**: MAD CONTROL PANEL, USER MANUALS, RESTART STEAM (FIX AUDIO), SWITCH TO DESKTOP | Launch MAD / read PDF manuals / recover audio / exit to desktop, all from inside ES-DE |
| Drop queued input after a long pause | No replay of buffered presses after returning from a launched game |
| **Native PauseGames** | Block input + pause preview videos while the Steam overlay holds gamescope keyboard focus, plus a Guide-button chord guard; replaces the Decky plugin for general overlay use |
| **Capability-adaptive sidebar** + a **Sidebar** page | The panel auto-hides rows you can't use yet (Lightgun/X-Arcade/Bezel); the Sidebar page forces any of them Auto / Always-show / Always-hide |

`upstream` = the official GitLab ES-DE; `origin` = this repo. Per ES-DE release the patches are rebased onto the new tag and the AppImage is rebuilt, **automatically by GitHub Actions** (see *Getting the ES-DE AppImage* below).

## Repository layout

```
main          ← this branch: the MAD tools (control panel, router, libs, scripts)
deck-patches  ← the patched ES-DE fork source (build the AppImage from here)
```

```
mad-backend.py           the control-panel daemon (NDJSON stdio; spawned by the fork's panel)
wii-nav-bridge.py        Wii Remote → virtual-gamepad navigation bridge (spawned by the fork)
router-config-gui.py     RETIRED Tk control panel, kept as the behavioral reference only
controller-router.py     the game-start/-end router
controller-policy.toml   routing policy (systems, collections, backends, pins, hardware)
lib/                     devices, per-emulator config writers, ES-DE/SDL helpers, GUI theme
lib/mad_paths.py + mad-paths.sh  data-root resolver ($MAD_DATA_ROOT → ~/Emulation), MAD runs on any folder layout
lib/emudeck-shim.sh      stand-in for EmuDeck's all.sh; the launchers run without EmuDeck present
lib/es_systems_standalone.py  seeds a minimal custom_systems.xml for a no-EmuDeck (standalone) install
*.sh                     emulator launchers, build/backup/restore, suspend-mode-setup
hooks/                   activation-hook wrappers install.sh deploys (launch-screens / Sinden / Wii / quit-combo)
install.example.conf     template for install.conf (the picker's saved choices; gitignored live copy)
deck-fetch-esde.sh       download the CI-built AppImage from the GitHub release
.github/workflows/       GitHub Actions: build + publish the ES-DE-MAD AppImage
art/  data/              icons, banner, UI sounds; data/standalone/ = the no-EmuDeck es_systems template
```
> `controller-policy.local.toml` (your live overrides, written by the GUI) is **git-ignored**; `controller-policy.example.toml` is a neutral, fully-commented template; copy it to start your own, or just let the GUI's **Players** / **Priority** pages write it. The shipped defaults in `controller-policy.toml` are sane on a bare Deck (rig-specific warnings stay silent until you identify the matching hardware).

## Install / setup

> ⚠️ This is the **manual** path; most people should just use the one-line **Quick install** above, which does steps 2-4 + 8 for any user. These steps are the map if you want to do it by hand or adapt the layout.

1. **Prereqs**: SteamOS + ES-DE config. EmuDeck installs the emulators and generates ES-DE's config (the emulator-wired `es_systems.xml`); MAD ships its *own* patched ES-DE binary and layers on top of that config. EmuDeck is the easy way to get there but is **optional**; without it, `install.sh --standalone` seeds the ES-DE config + a minimal `es_systems.xml` itself (see *Standalone install* above) and you supply the emulators + ROMs (`~/ROMs/<system>`). Python deps `python3`, `tk` (tkinter, for warning dialogs) and `python-evdev` (pacman). SteamOS's root is immutable and wiped by updates, so `deck-post-update.sh` reinstalls them.
2. **MAD tools**: put this branch at `~/Emulation/tools/launchers/` (it runs from there, the install location is hard-coded; its mutable data root is configurable via `$MAD_DATA_ROOT`, default `~/Emulation`). Then `cp controller-policy.example.toml controller-policy.local.toml` and edit, or just use the GUI's **Players** / **Priority** pages.
3. **Patched ES-DE**: install it as `~/Applications/ES-DE-MAD.AppImage` with the wrapper `~/Applications/ES-DE.AppImage`; either download the CI build or build the fork locally (see *Getting the ES-DE AppImage* below).
   - **MAD theme**: `git clone https://github.com/mmadalone/pixel-es-de.git ~/ES-DE/themes/pixel-es-de` and select it (ES-DE → Menu → UI Settings → Theme = `pixel-es-de`). The MAD panel reads its icons + colours from this theme's `router-config/`, so without it the panel is un-themed and icon-less. `install.sh` does this step for you.
4. **ES-DE hooks**: the router runs from ES-DE's game-start/-end scripts (`~/ES-DE/scripts/game-start/*.sh`, `game-end/*.sh`), which call `controller-router.py`, plus the **activation hooks** `install.sh` deploys from `hooks/` (quit-combo always; launch-screens with the theme; Sinden/Wii with Sinden) that fire those features at launch; the **MAD CONTROL PANEL** menu row opens the native panel (no external window, the Tk app is retired; `MAD.sh` is now a retired-notice stub that, if launched in Desktop mode, just shows a "MAD has moved" popup instead of the stale GUI).
5. **Steam Input OFF** for the ES-DE Steam shortcut: the router needs raw evdev (the Deck must enumerate as `28de:1205`, not the Steam-virtual `28de:11ff`). Trade-off: with Steam Input off the Deck's gyro / trackpads / back-paddles don't work *inside ES-DE*. That's expected; don't turn it back on or routing breaks.
6. **Steam overlay (optional)**: overlay input is handled **natively** by the patched ES-DE. The **PauseGames** Decky plugin is only needed if you also want the few game-context overlay spots (home/notes/guide/resume) covered.
7. **Launch**: open MAD from ES-DE → **Main Menu → Utilities → "MAD CONTROL PANEL"**.
8. **System bits**: Sinden udev rules, Samba, the model-aware suspend mode, etc. are (re)applied by `deck-post-update.sh`; run it after any SteamOS update **or an EmuDeck/ES-DE app update** (both wipe root bits or revert the launch wrapper to stock). It re-applies only what you opted into (`install.conf`).

## Getting the ES-DE AppImage

Either way produces `~/Applications/ES-DE-MAD.AppImage`; a tiny wrapper at `~/Applications/ES-DE.AppImage` regenerates the splash and execs it (keeping the stock AppImage as `.real`).

**1 · Download the CI build (recommended).** Every push to `deck-patches` triggers a [GitHub Actions workflow](.github/workflows/build-appimage.yml) that builds the AppImage on Ubuntu 22.04 (glibc 2.35 → runs on SteamOS) using ES-DE's own `create_AppImage_SteamDeck.sh` *verbatim*, and publishes it to a rolling [`latest-steamdeck`](https://github.com/mmadalone/mad/releases/latest) release (`ES-DE-MAD.AppImage` + `.sha256`). On the Deck:

```bash
deck-fetch-esde.sh        # curl + python3, no gh/jq; sha256-verified; backs up the old build to _TMP, installs the new
```
`deck-post-update.sh` and `deck-restore.sh` call it automatically when the AppImage is missing.

**2 · Build locally** in an `esde-ubuntu` [distrobox](https://distrobox.it/) (the same recipe the CI runs):

```bash
cd ~/esde-build/ES-DE && git checkout deck-patches
distrobox enter esde-ubuntu -- bash ~/esde-build/ubuntu-build.sh   # → ES-DE_x64_SteamDeck.AppImage
```

## Hardware this targets

Steam Deck (SteamOS 3.x / Game Mode / gamescope) · EmuDeck + ES-DE · X-Arcade Tankstick (Xbox mode) · 2× Sinden lightgun · Mayflash DolphinBar · DualSense / DualShock 4 / 8BitDo / Wii U Pro pads.

## Credits & licence

Built on [**ES-DE**](https://es-de.org) (EmulationStation Desktop Edition) by Leon Styhre. The `deck-patches` branch is a fork of its source; all credit for ES-DE itself goes upstream. MAD's own tooling and the fork patches are in this repo; see `LICENSE`.

<p align="center"><i>Made for one very over-configured Steam Deck. 🕹️</i></p>
