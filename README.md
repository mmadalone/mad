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

> ⚠️ **Personal project / work-in-progress.** Built for one specific Steam Deck (EmuDeck + ES-DE, an X-Arcade Tankstick, two Sinden lightguns, a Mayflash DolphinBar, and an assortment of Bluetooth pads). There's now a one-shot installer (below) that sets up the ES-DE + MAD **core** on any Deck — but the *controller routing* is inherently per-rig, so you configure your own pads in the GUI. Cherry-pick freely.

## Quick install

On a Steam Deck that already has **EmuDeck + ES-DE** working, from a Desktop-Mode terminal:

```bash
curl -fsSL https://raw.githubusercontent.com/mmadalone/mad/main/install.sh | bash
```

It fetches the patched ES-DE AppImage, drops the MAD tools in place, installs + selects the MAD theme (`pixel-es-de`), installs the ES-DE hooks + Python deps, and seeds a default controller policy — all idempotent and non-destructive. Two things it can't safely script (it prints them at the end): **add ES-DE to Steam and set Steam Input OFF**, and configure your pads in the **MAD CONTROL PANEL**. Pass `--dry-run` first to see every action. The manual, step-by-step path is in *Install / setup* below.

## What is MAD?

On a Steam Deck running ES-DE in Game Mode you have *many* controllers (an arcade stick, lightguns, several pads) and *many* emulators, each with its own idea of how to assign players. MAD makes that coherent and controllable **without a keyboard or Desktop Mode**:

- 🎛️ **A control panel** (opened from ES-DE's **Main Menu → Utilities → "MAD CONTROL PANEL"**) — rendered NATIVELY inside ES-DE's window (`GuiMadPanel` in the fork + the `mad-backend.py` NDJSON daemon in this branch) — a fullscreen, fully gamepad-navigable UI for everything below: live device preview, per-system controller priority, player pinning, backends, quit combos, lightgun (incl. live camera preview), Daphne binds, three live controller testers, splash screens, backups. Wii Remotes on a mode-4 DolphinBar navigate it (and all of ES-DE) via `wii-nav-bridge.py`.
- 🔀 **A controller router** (`controller-router.py`) — runs at ES-DE game-start/-end hooks and writes each emulator's controller config so the right pad lands on the right player, every launch. Policy-driven (`controller-policy.toml`), never hard-coded behaviour.
- 🎨 **Fully themeable** — the active ES-DE theme can recolor the whole panel and swap its icons: drop `router-config/mad-theme.xml` (global palette) and per-page `<pagename>-theme.xml` files into the theme (pages: preview, systems, priority, players, quit-combo, backends, lightgun, daphne, x-arcade, gamepads, splash, backup). Colors: `frame/primary/secondary/title/selector/red/green/separators/panelDimmed/buttonFlat/helpText` (hex RGB[A], `${var}` substitution); icons: `<icon name="sidebar">./icons/x.png</icon>`. **No files = the stock look** — see `art/mad-theme-examples/pixel-es-de/` for a complete reference set. Themes can also ship UI fonts (any `*.ttf`/`*.otf` in the theme) selectable under **UI Settings → THEME FONTS**.
- 🧩 **A patched ES-DE fork** (the `deck-patches` branch of this repo) — a handful of small source patches that the control panel relies on (see below).

## Highlights

- **Per-system & per-collection priority** — e.g. arcade systems default to the X-Arcade on P1/P2, console systems to gamepads; collections (e.g. lightgun games) override per-ROM.
- **Device pinning** — pin a *specific* physical pad to a player (`uniq:` Bluetooth-MAC / `port:` USB-port / `vidpid:` model key), globally or per-system, via the **Players** page (press-to-identify).
- **X-Arcade Tankstick** support, including **telling it apart from a real Xbox 360 pad** (both enumerate as `045e:02a1`) by its USB port — identified once from the **Preview** page.
- **Sinden lightguns** (2-player), **Mayflash DolphinBar** Wii-Remote detection, and **multi-pad** rigs handled as first-class cases.
- **Per-emulator backends** — RetroArch (per-game `reserved_device` overrides), plus standalone Cemu, Dolphin, PCSX2, xemu, RPCS3, Eden, Supermodel, Hypseus/Daphne, OpenBOR and MUGEN.
- **Live Preview** — see every connected controller (with battery %), what each system *would* route, and the DolphinBar Wii-Remote count, updating as you plug/unplug.
- **Resilience** — `deck-post-update.sh` detects what a SteamOS update wiped and restores it (Samba, Sinden deps, udev rules, the patched AppImage wrapper, Python GUI deps); if the patched AppImage itself is gone it's pulled from the CI release by `deck-fetch-esde.sh` before falling back to a local rebuild. Backups via `deck-backup.sh`.
- **CI-built AppImage** — every push to `deck-patches` has **GitHub Actions** build the patched ES-DE AppImage (Ubuntu 22.04, matching the Deck's glibc) and publish it to a rolling release, so recovery is a download, not a 30-min rebuild.
- **Steam-overlay input** — with Steam Input *off* (required for raw-evdev routing) ES-DE would double-input under the Steam overlay. The fork now **pauses input + preview videos natively** when it loses gamescope keyboard focus — no plugin required. The **PauseGames** Decky plugin is optional, only for the few game-context overlay spots (home/notes/guide/resume).

## The ES-DE fork (`deck-patches`)

Small, rebase-able source patches on top of upstream ES-DE (`base/v3.4.1`), built into the AppImage the control panel uses:

| Patch | Why |
|---|---|
| Full-screen startup splash | Edge-to-edge custom splash on the Deck |
| `arg5` to game-start scripts | Pass the *launched-from* collection so launch screens & routing are correct |
| Honour `es_systems_sorting.xml` for custom collections | Stable custom-collection ordering |
| **"MAD CONTROL PANEL"** + **"Restart Steam (fix audio)"** menu rows | Launch MAD / recover audio from inside ES-DE |
| Drop queued input after a long pause | No replay of buffered presses after returning from a launched game |
| **Native PauseGames** | Block input + pause preview videos while the Steam overlay holds gamescope keyboard focus, plus a Guide-button chord guard — replaces the Decky plugin for general overlay use |

`upstream` = the official GitLab ES-DE; `origin` = this repo. Per ES-DE release the patches are rebased onto the new tag and the AppImage is rebuilt — **automatically by GitHub Actions** (see *Getting the ES-DE AppImage* below).

## Repository layout

```
main          ← this branch: the MAD tools (control panel, router, libs, scripts)
deck-patches  ← the patched ES-DE fork source (build the AppImage from here)
```

```
mad-backend.py           the control-panel daemon (NDJSON stdio; spawned by the fork's panel)
wii-nav-bridge.py        Wii Remote → virtual-gamepad navigation bridge (spawned by the fork)
router-config-gui.py     RETIRED Tk control panel — kept as the behavioral reference only
controller-router.py     the game-start/-end router
controller-policy.toml   routing policy (systems, collections, backends, pins, hardware)
lib/                     devices, per-emulator config writers, ES-DE/SDL helpers, GUI theme
*.sh                     emulator launchers, build/backup/restore, ES-DE hooks
deck-fetch-esde.sh       download the CI-built AppImage from the GitHub release
.github/workflows/       GitHub Actions: build + publish the ES-DE-MAD AppImage
art/  data/              icons, banner, UI sounds
```
> `controller-policy.local.toml` (your live overrides, written by the GUI) is **git-ignored**; copy `controller-policy.example.toml` to start your own.

## Install / setup

> ⚠️ This is the **manual** path — most people should just use the one-line **Quick install** above, which does steps 2–4 + 8 for any user. These steps are the map if you want to do it by hand or adapt the layout.

1. **Prereqs** — SteamOS + EmuDeck + ES-DE already working. EmuDeck installs the emulators and generates ES-DE's config (the emulator-wired `es_systems.xml`); MAD ships its *own* patched ES-DE binary and layers on top of that config — so you still need EmuDeck's ES-DE frontend set up + run once. Python deps `python3`, `tk` (tkinter, for warning dialogs) and `python-evdev` (pacman). SteamOS's root is immutable and wiped by updates, so `deck-post-update.sh` reinstalls them.
2. **MAD tools** — put this branch at `~/Emulation/tools/launchers/` (it runs from there — paths are hard-coded). Then `cp controller-policy.example.toml controller-policy.local.toml` and edit, or just use the GUI's **Players** / **Priority** pages.
3. **Patched ES-DE** — install it as `~/Applications/ES-DE-MAD.AppImage` with the wrapper `~/Applications/ES-DE.AppImage` — either download the CI build or build the fork locally (see *Getting the ES-DE AppImage* below).
   - **MAD theme** — `git clone https://github.com/mmadalone/pixel-es-de.git ~/ES-DE/themes/pixel-es-de` and select it (ES-DE → Menu → UI Settings → Theme = `pixel-es-de`). The MAD panel reads its icons + colours from this theme's `router-config/`, so without it the panel is un-themed and icon-less. `install.sh` does this step for you.
4. **ES-DE hooks** — the router runs from ES-DE's game-start/-end scripts (`~/ES-DE/scripts/game-start/*.sh`, `game-end/*.sh`), which call `controller-router.py`; the **MAD CONTROL PANEL** menu row opens the native panel (no external window — the Tk app is retired; `MAD.sh` is now a retired-notice stub that, if launched in Desktop mode, just shows a "MAD has moved" popup instead of the stale GUI).
5. **Steam Input OFF** for the ES-DE Steam shortcut — the router needs raw evdev (the Deck must enumerate as `28de:1205`, not the Steam-virtual `28de:11ff`).
6. **Steam overlay (optional)** — overlay input is handled **natively** by the patched ES-DE. The **PauseGames** Decky plugin is only needed if you also want the few game-context overlay spots (home/notes/guide/resume) covered.
7. **Launch** — open MAD from ES-DE → **Main Menu → Utilities → "MAD CONTROL PANEL"**.
8. **System bits** — Sinden udev rules, Samba, etc. are (re)applied by `deck-post-update.sh`; run it after any SteamOS update.

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

Built on [**ES-DE**](https://es-de.org) (EmulationStation Desktop Edition) by Leon Styhre — the `deck-patches` branch is a fork of its source; all credit for ES-DE itself goes upstream. MAD's own tooling and the fork patches are in this repo; see `LICENSE`.

<p align="center"><i>Made for one very over-configured Steam Deck. 🕹️</i></p>
