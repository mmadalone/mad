# MAD & our patched ES‑DE — a plain‑language guide

*What this whole project is, what it does, how it differs from a normal ES‑DE, and how to install it. Written to be understandable without being a programmer.*

---

## The 60‑second version

On a Steam Deck, **EmuDeck** sets up your emulators and **ES‑DE** is the pretty menu you browse your games in. This project adds a layer on top of that so a couch‑only, multi‑controller arcade/console setup *just works*:

- **A custom build of ES‑DE** (our “fork”) — the exact same ES‑DE everyone uses, plus a set of Steam‑Deck‑specific extras baked in (most importantly, the MAD control panel runs *inside* the ES‑DE window).
- **MAD** — the tools that do the real work: an on‑screen **control panel** you drive with a gamepad, and a behind‑the‑scenes **controller router** that makes the right controller become the right player in every game, every time.
- **A theme** (`pixel‑es‑de`) — supplies MAD’s on‑screen icons and colours, and the full‑screen “loading” art shown when a game starts.

You install it with **one command**. **EmuDeck** is the easy way to get your emulators set up, but it's **optional** — there's a *standalone* mode for people who install their emulators their own way (more in §5). The custom ES‑DE comes **pre‑built** (no compiling), and everything is designed so a SteamOS update or a dead SD card is recoverable.

---

## 1. The big picture — what the pieces are

Think of it as a stack, bottom to top:

| Layer | What it is | Who provides it |
|---|---|---|
| **SteamOS** | The Deck’s operating system | Valve |
| **EmuDeck** | Installs and configures the actual emulators (RetroArch, PCSX2, RPCS3, Dolphin, Cemu…), Proton, BIOS layout, the folder structure, and ES‑DE’s configuration | EmuDeck (**recommended, but optional** — see §5) |
| **ES‑DE** | The game‑browser menu you see in Game Mode | We ship our **own patched build** of it |
| **MAD** | The control panel + controller router + launch screens + backup/recovery tools | This project |
| **Theme** | Icons, colours, and launch‑screen art | The `pixel‑es‑de` theme |

**Key point:** ES‑DE is *only the menu*. It doesn’t contain the emulators — something has to install those and tell ES‑DE how to launch each kind of game. EmuDeck is the easy way to do that, and MAD layers its tools on top. But EmuDeck is **optional**: if it isn’t there, MAD sets up the ES‑DE side itself and leans on ES‑DE’s own built‑in ability to find whatever emulators you installed — you just bring the emulators and ROMs (more in §5).

**Two code homes** (both on the project `mmadalone/mad`):
- the **MAD tools** (the control panel’s helper program, the router, all the scripts) are installed from the `main` branch and run from `~/Emulation/tools/launchers`;
- the **patched ES‑DE source** lives on the `deck-patches` branch.
- the **theme** is its own separate project, `mmadalone/pixel-es-de`.

---

## 2. What our patched ES‑DE adds over the official ES‑DE

We don’t use the off‑the‑shelf ES‑DE that EmuDeck installs. We run our own build that **starts from the exact official version (3.4.1)** and adds about **90 small, self‑contained changes** on top. *(Verified: our starting point is byte‑for‑byte identical to the official ES‑DE 3.4.1 release; our changes touch roughly 115 files (~110 of them C++ source). So the list below really is “us vs. stock”.)*

It **looks and behaves exactly like normal ES‑DE** — same menus, same themes, same game and emulator support — with these Steam‑Deck extras:

- **The MAD control panel runs inside the ES‑DE window.** The whole panel (controllers, lightguns, backups, settings, theming…) is drawn as native ES‑DE screens, opened from **Main Menu → Utilities → “MAD CONTROL PANEL”**. *(Our earlier version opened a separate desktop window that fought the Steam overlay for focus; running it inside ES‑DE fixed that.)*
- **Four extra menu rows:** “MAD CONTROL PANEL” and “USER MANUALS” under Utilities; “RESTART STEAM (FIX AUDIO)” and “SWITCH TO DESKTOP” under Quit.
- **The sidebar adapts to your hardware.** The control panel auto-hides rows you can’t use yet — Lightgun until a Sinden driver is installed, X‑Arcade until a cabinet is identified, Bezel Project until RetroArch is present. The **Sidebar** page lets you reorder every entry and show/hide any of them (Auto / Always show / Always hide), with an Apply that updates the sidebar at once.
- **A built‑in PDF reader for your own manuals.** Drop PDFs into `~/ES-DE/usermanuals` and read them on screen with the controller (controller/hardware manuals, etc.). Stock ES‑DE can only show a PDF attached to a specific game.
- **Full‑screen startup splash** — the boot splash fills the whole screen edge‑to‑edge instead of a small centered image.
- **Native “pause when the Steam overlay opens.”** When the Steam overlay / Quick Access Menu is up (or ES‑DE is in the background), ES‑DE stops responding to the controller, pauses the preview videos, and stops drawing to save battery — so it no longer silently scrolls around behind the overlay. It also blocks the Guide‑button combo so Steam shortcuts don’t leak into ES‑DE. *(This replaces a third‑party plugin for the common cases.)*
- **No “replayed” button presses.** Any presses that piled up while ES‑DE was paused or while you were in a game are discarded on return, instead of firing as a burst of menu navigation.
- **Custom collections respect the sort‑order file**, so your curated game groups appear in the order you intend.
- **The “which collection did this launch from?” signal.** When you start a game, our ES‑DE tells the launch scripts which custom collection it came from. This is what makes the controller router *and the launch screens* behave correctly per collection (see §4 and §3). Stock ES‑DE doesn’t pass this.
- **A self‑updater pointed at *our* builds.** ES‑DE’s normal “update available” popup checks our rolling release instead of the official feed, downloads + verifies + installs our build, can **auto‑restart** afterwards, and shows our build number in the menu.
- **Theme fonts + a fully themeable MAD panel.** UI Settings gains a font picker that uses fonts bundled with the active theme; the MAD panel takes its colours/icons/background from the active theme. With a theme that ships nothing, it looks pixel‑identical to stock.
- **Wii Remote navigation** — an optional bridge lets you steer ES‑DE and MAD with a Wii Remote (via a DolphinBar); it steps aside automatically whenever a game launches.
- **A Sega Model 2 settings editor** built into the MAD panel (that emulator’s config is an unusual format, so it gets its own screen).

Every one of these is kept as a single, clean patch, so when ES‑DE puts out a new version we re‑apply our extras on top and stay current.

> **One correction worth stating plainly:** the **full‑screen launch screens** (the art shown while a game loads) are **ours**, not stock ES‑DE. Stock ES‑DE only has a small “loading info” overlay. Our launch screens are described in §3 and §4.

---

## 3. Launch screens (ours)

When you start a game, MAD shows a **full‑screen image** (`launching.png`) that stays up until the game window actually appears — bridging the black gap while a slow emulator loads. It’s smart about *which* image:

- The images are **part of the theme** — they live in `~/ES-DE/themes/pixel-es-de/_launching-screens/` (so they ship *in the theme repo*), one folder per system and per collection.
- If you launched the game **from a custom collection**, you get that collection’s screen exactly (thanks to the “which collection” signal from our patched ES‑DE); otherwise you get the game’s system screen.
- A small helper (`show-launchscreen.py`) draws the image and is told to hold it until the game takes over the screen, then it closes cleanly.

*(Because the launch‑screen images are theme assets, they ship in the theme repo — not in the MAD tools repo. Don’t confuse them with the ES‑DE startup splash, which is your own personal folder; see §4 “Splash”.)*

---

## 4. What MAD does

MAD has two halves: the **control panel** you see, and the **router** you don’t.

### 4a. The MAD Control Panel (the on‑screen dashboard)

A full‑screen, **gamepad‑only** dashboard inside ES‑DE (it deliberately ignores the keyboard so a Sinden lightgun, which types fake keystrokes, can’t wander through it). A sidebar down the left holds the sections; the **L/R shoulder buttons** flip between them; the help strip at the bottom always shows what each button does right now. Changes **save the instant you make them**, and each page re‑reads from disk so what you see is always the truth. You can even navigate it with a Wii Remote (through a DolphinBar).

The top‑level sections:

- **Preview** — a live “what would happen right now” screen. Lists every connected controller (with battery % for wireless), shows DolphinBar / Wii‑Remote status, and shows **which pad would become Player 1, 2, 3…** for each system if you launched a game now. Also where you **identify your X‑Arcade** (press a button on it so MAD learns its USB port — see §4b).
- **Systems** — per‑system controller settings and a few key RetroArch options, one system at a time. A dot marks systems you’ve customised. Switches like “Hands‑off”, “require a DolphinBar/Sinden gun”, and X‑Arcade warnings live here.
- **Priority** — set, per system, which *kind* of controller should be Player 1, Player 2, etc. (and make a collection rule — e.g. lightgun games — that overrides it).
- **Players** — lock one **exact physical controller** to a player number by pressing a button on it (“press‑to‑identify” — you never need to know any technical IDs).
- **Quit combo** — choose the button combination that exits a game and returns to ES‑DE (re‑detect it by just holding the buttons you want; per‑system exceptions allowed).
- **Lightgun** — the full Sinden control center: install the driver, calibrate, a **live on‑screen camera view** with brightness/exposure sliders, per‑player button mapping, recoil, a pointer “smoother” with presets, and an optional **Home Assistant** LED toggle. (The LED toggle fires a webhook to switch a TV LED strip on when the gun driver starts and off when it stops — you set your HA base URL + webhook IDs in `~/Emulation/tools/launchers/sinden.conf`; no token is needed — an HA webhook is triggered by its *secret URL* (`/api/webhook/<id>`), so the id itself is the credential. Keep it secret, use the random ids HA generates, and leave HA's `local_only` on so only your home network can fire it.)
- **Standalones** — a tile per standalone emulator you have games for (Model 2/3, Dolphin/Wii, Cemu/Wii U, Switch, PS3, PS2, Xbox, OpenBOR, Daphne). Opening a tile gets you that emulator’s **Settings** (video/audio), **Controllers** (which pads are players, with a Hands‑off switch), and where supported **per‑button input mapping** and **per‑game settings** — all on screen, no Desktop Mode.
- **RetroArch** — global RetroArch settings plus per‑player button/hotkey mapping, with an automatic one‑time backup.
- **Bezel Project** — turn the decorative screen borders on/off per system or per game.
- **X‑Arcade** — confirm the arcade controller’s mode, run a live on‑screen test (every stick/button lights up a cabinet overlay), and calibrate that overlay to your cabinet.
- **Gamepads** — live test any controller (or a Wii Remote through the DolphinBar): press things and watch them light up on an on‑screen controller picture.
- **Splash** — control the **ES‑DE startup splash** (the boot image shown when ES‑DE/games start) — this is *separate* from the per‑game launch screens in §3. Set off / single / random and pick which images the random pool may use. The splash images are your own and live in `~/ES-DE/splashscreens/` (a personal folder — not part of any repo).
- **Backup** — run a full backup of your whole setup, or surgically restore just the controller configs, or reset all of MAD’s own customisations back to defaults.
- **Sidebar** — **reorder every entry and show/hide each one.** Move a row (press **A** to lift it, up/down to move, **A** to drop), press **X** to cycle a row **Auto / Always show / Always hide**, then **Apply** — the sidebar updates **immediately** and the layout is remembered (no need to reopen the panel). Capability rows (**Lightgun / X‑Arcade / Bezel Project**) still **Auto**-hide until their hardware or data is present (handy: if an install‑time Sinden download failed, force Lightgun on to reach its INSTALL button). Core pages can be hidden too — but the **Sidebar** entry itself can be reordered and never hidden, so you can always get back.

*(Behind the scenes the on‑screen pages talk to a small background helper program — the “MAD backend” — that reads controllers, writes config files, and runs tools, so the screen stays responsive while jobs run. Logs go to `~/Emulation/storage/controller-router/mad-backend.log`.)*

### 4b. The controller router (the behind‑the‑scenes part)

This is the part that makes the **right physical controller become the right player in every game, every launch** — the single most useful thing MAD does in a multi‑controller setup. Before each game it reads your rules, sees what’s actually plugged in, configures the emulator, and tidies up when the game ends. Highlights:

- **A plain rules file** (`controller-policy.toml`) holds every preference (which pad is which player, per system), with your personal overrides kept separately so they’re never clobbered.
- **It handles three ways you launch a game** — docked in ES‑DE with external pads, handheld in ES‑DE (falls back to the Deck’s own controls), and **straight from the Steam UI on the go**. Because all three share one emulator config, MAD’s changes are **temporary and revert when the game ends**, so fixing one situation never breaks another.
- **X‑Arcade vs. real Xbox pad.** In Xbox mode an X‑Arcade arcade stick looks *byte‑identical* to a genuine Xbox 360 pad — the only way to tell them apart is the USB port. You identify the X‑Arcade once (in Preview); afterwards MAD knows the stick from a real pad and assigns the arcade player slots correctly.
- **Sinden lightguns** — for lightgun games it requires a gun (shows a “plug in your gun” prompt if missing), aims it at the right player, and only starts the gun driver when needed.
- **Wii Remotes via a Mayflash DolphinBar** — counts the live remotes and points Dolphin at them, without touching your button mappings.
- **Pin a specific controller** — “this exact DualSense is always Player 1.” (Two *identical* pads with no unique ID are a known limitation — they can swap seats.)
- **Helpful warnings before launch** — e.g. a console game with only the arcade stick connected suggests a gamepad; a lightgun game with no gun warns you. A warning that can’t display is treated as “proceed”, so a broken dialog can never trap you.
- **Quit combo for standalone emulators** — emulators without a quit hotkey get a “hold these buttons to quit” watcher, so every game has a consistent way out.

It’s wired into ES‑DE through small **game‑start / game‑end hook scripts** the installer sets up, so routing fires automatically for every game — old and new — with nothing to do per game.

> **One thing you must set yourself:** for the ES‑DE shortcut in Steam, **Steam Input must be OFF** (the router needs to read the raw controllers). Trade‑off: under ES‑DE the Deck’s gyro/trackpads/back‑paddles produce no input (face buttons, sticks, triggers still work). This is by design.

---

## 5. Installing it (step by step)

**Before you start — the recommended path:** install **EmuDeck**, enable its **ES‑DE** frontend, and run ES‑DE once. EmuDeck installs the actual emulators *and* writes ES‑DE’s configuration; MAD ships its own patched ES‑DE but builds on that configuration. (MAD replaces the ES‑DE *program*, not EmuDeck’s setup.) Get EmuDeck from <https://www.emudeck.com>. **EmuDeck is no longer required** — if you’d rather set up emulators your own way, skip to *“Without EmuDeck (standalone)”* below.

**Then, one command** — from a **Desktop‑Mode terminal**:

```bash
curl -fsSL https://raw.githubusercontent.com/mmadalone/mad/main/install.sh | bash
```

*(Tip: add `--dry-run` to the end first to see everything it would do, changing nothing.)*

What that one command does, automatically:
1. Checks what you have — if it finds EmuDeck/ES‑DE it uses it as‑is; if not, it offers **standalone** mode (see below).
2. **Downloads our pre‑built patched ES‑DE** (no compiling — about a minute) and sets up its launch wrapper.
3. Copies the MAD tools into `~/Emulation/tools/launchers`.
4. Installs the **controller hooks** and wraps the relevant emulator launch commands for routing.
5. **Downloads and selects the MAD theme** (so the panel has its icons and colours).
6. Seeds a neutral starter controller profile (a clean, commented template — never overwriting one you’ve already customised).
7. Installs the system bits it needs (controller‑reading library, dialog toolkit, adds you to the `input` group).

**Two things you finish by hand** (it prints these at the end):
1. **Switch back to Game Mode** (Steam → Power → Switch to Game Mode), then **Library → “Add a Non‑Steam Game”** and point it at `~/Applications/ES-DE.AppImage` (adding it from Desktop‑Mode Steam works too and persists). Then right‑click it → **Properties → Controller → set Steam Input to OFF**.
2. Launch ES‑DE from Steam, open **Main Menu → Utilities → MAD CONTROL PANEL**, and identify your controllers on the **Players / Priority** pages.

*(If you were just added to the `input` group, log out and back in — or reboot — for controller access to take effect.)*

It’s safe to re‑run the installer any time; it never clobbers your own settings and backs up anything it must replace.

**Without EmuDeck (standalone).** If you don’t use EmuDeck, run the installer with `--standalone` (or just run it — it offers standalone when it can’t find EmuDeck):

```bash
curl -fsSL https://raw.githubusercontent.com/mmadalone/mad/main/install.sh | bash -s -- --standalone
```

In this mode MAD sets up the ES‑DE side itself and writes a **small** systems file: it wires the consoles MAD does special controller work for (Switch, PS2, PS3, Xbox) plus its own arcade/lightgun systems, and lets ES‑DE handle everything else automatically — ES‑DE already knows ~195 systems and **finds each emulator wherever you installed it**. **What you provide:** the emulators themselves (Flatpak, AppImages dropped in `~/Applications`, or however you like) and your ROMs under `~/ROMs/<system>` (e.g. `~/ROMs/snes`, `~/ROMs/ps2`). MAD is the menu + control panel, **not** an emulator installer — it won’t download emulators or BIOS for you. (Existing EmuDeck users are unaffected: the installer auto‑detects EmuDeck and behaves exactly as before.)

---

## 6. Hardware setup (optional extras)

MAD’s core works with any controller. These are the *optional* pieces of the maintainer’s rig — each is only needed if you actually own the hardware, and MAD auto‑hides the control‑panel rows for anything it can’t detect (override that on the **Sidebar** page).

- **X‑Arcade Tankstick (arcade stick).** Put the stick in its **Xbox‑360 mode**; the Deck then sees it as an Xbox‑360 receiver. Because both joystick halves look identical, MAD tells them apart by which USB port they’re on — so you **identify it once**: open the control panel’s **Preview** page, press a button on the stick, and let MAD record its port. After that P1/P2 land correctly every launch (re‑cabling to a different port = re‑identify once). To check the Deck sees the buttons at all, run `joystick-button-detector.py` or `ra-input-monitor.py` (see §8).
- **Sinden lightgun(s).** You need the **gun(s) flashed with distinct firmware IDs** (a one‑time flash so two guns aren’t identical — the installer can’t do it), the **mono** runtime (the installer pulls it), and Sinden’s own **LightgunMono** driver (closed‑source; the installer downloads it into `~/Lightgun/`). The installer also lays down the device rules. **Honest note:** full **two‑player** co‑op (the cursor‑smoother, the separate‑cursor X11 trick, the per‑gun serial pinning) is tuned to the maintainer’s exact two‑gun setup; a **single** gun is much simpler and more portable. The control panel’s **Lightgun** page has a live camera preview to aim‑test.
- **Mayflash DolphinBar (Wii Remotes).** Set the **physical MODE switch on the bar to 4** for Dolphin and for on‑screen Wii‑Remote navigation (mode 4 streams the raw remote; mode 3 makes the bar act as a plain gamepad instead). Pair your remote(s) to the bar. To confirm the bar and remote are talking, run `wii-monitor.py` (see §8).
- **Wii‑Remote menu navigation.** With the DolphinBar in **mode 4** and at least one remote paired, the patched ES‑DE automatically starts a small bridge so you can drive the whole menu (and the MAD panel) with the remote — nothing to launch by hand.

> None of this is required to use MAD. Skip the whole section if you just play with a gamepad or the Deck’s own controls.

---

## 7. Keeping it working

- **Updating ES‑DE/MAD:** the patched ES‑DE updates itself — ES‑DE’s normal “update available” popup pulls our latest build, verifies it, and can restart itself. No building, ever.
- **After a SteamOS *system* update:** SteamOS keeps all your personal files but resets the system core, wiping a few low‑level pieces (file sharing, lightgun deps, device rules, the `input` group, sleep mode). Run:
  ```bash
  ~/Emulation/tools/launchers/deck-post-update.sh
  ```
  It reinstalls exactly those pieces (and re‑downloads our ES‑DE if it went missing). **ES‑DE even nudges you on screen** if it notices an update happened, so you don’t have to remember.
- **After an EmuDeck/ES‑DE *app* update:** that can replace our ES‑DE with stock. The same `deck-post-update.sh` points the launch wrapper back at our build — no rebuild.
- **Backups:** `deck-backup.sh` archives your whole setup (with choices for the big stuff like ROMs/media); `deck-restore.sh` brings it back on a wiped or new Deck, guiding you and protecting current files during the restore. *(BIOS is backed up by default; the big stuff — ROMs and downloaded media — is off by default but offered, to keep routine backups small.)*

---

## 8. Maintenance scripts (optional, command‑line)

Beyond the installer and the panel, the `launchers/` folder ships some **command‑line** helpers for tidying a library. They have **no panel UI** — you run them from a Desktop‑Mode terminal in `~/Emulation/tools/launchers`. They read your paths from ES‑DE itself (ROM dir, gamelists, and the media folder ES‑DE is configured to use), so they work whether your media lives on the internal drive or an SD card. **Close ES‑DE before running anything that edits gamelists.** Changes are reversible, but the mechanism varies: `clean-manual-cruft.py` moves cruft to a recoverable `_TMP` (with a `RECOVERY.txt`, dropped beside your media folder — the SD card if that’s where your media lives); the gamelist editors (`dedup-disc-gamelists.py`, `skyscraper-apply.py`) keep a timestamped `.bak`; `reorganize-cd-games.py` and `fix-media-names-for-dir-as-file.py` move/rename files in place, so **preview with `--dry-run` first**; `wire-bezels.py` only writes new config files and never overwrites existing ones. Nothing is deleted.

**Gamelist & media tidiness**
- `dedup-disc-gamelists.py [systems…]` — for disc games that show twice (the disc file *and* an `.m3u`), hides the redundant entry: single‑disc → hide the `.m3u`; multi‑disc → keep the `.m3u`, hide the parts.
- `reorganize-cd-games.py --dry-run|--apply (<system> | --all)` — groups multi‑disc/CD games into the per‑folder “directory‑as‑file” layout ES‑DE 3.4 expects (`--all` does every default multi‑disc system).
- `fix-media-names-for-dir-as-file.py --dry-run|--apply [systems…]` — after that reorg, renames media so ES‑DE finds it for the folder‑as‑file entries (e.g. `Game.png` → `Game.cue.png`).
- `clean-manual-cruft.py [--apply]` — tidies manual PDFs: fixes wrong‑named ones; moves redundant/orphaned ones to a recoverable `_TMP`.

**Scraping (needs Skyscraper)**
- `skyscraper-apply.py [systems…]` — applies metadata and art you’ve already scraped with [Skyscraper](https://github.com/Gemba/skyscraper) into your ES‑DE gamelists and media. Install and run Skyscraper first; this only *applies* its output.

**Bezels**
- The easy way is the control panel’s **Bezel** page (it downloads a system’s pack on demand). `wire-bezels.py [--apply]` is the bulk command‑line equivalent for wiring RetroArch bezel overlays across many games at once.

**Theme porting (for theme authors)**
- `convert-pixel-theme.py <theme-dir>`, `convert-pixel-systems.py <theme-dir>`, `inject-carousel-logos.py <theme-dir>` — convert an old EmulationStation “Pixel” theme to the modern ES‑DE format. Pass the theme folder to convert.

**Hardware check utilities** (read‑only, just report)
- `joystick-button-detector.py` / `ra-input-monitor.py` — show raw joystick / X‑Arcade button presses. `wii-monitor.py` — show a DolphinBar (mode 4) Wii Remote’s reports. `switch-to-desktop.sh` — jump from Game Mode to Desktop Mode.

> A few maintainer‑only scripts (`steam-collection-*`, `scrape-manuals.sh`, `openbor-fetch-media.py`, `singe-indexer.sh`) are tied to the author’s own library (curated game lists, a personal scraper account, specific binaries) and aren’t meant for general use — they’re intentionally left undocumented here.

---

## 9. Important rules & gotchas (worth knowing)

- **Steam Input OFF for ES‑DE** — required, by design. Never turn it on for ES‑DE.
- **EmuDeck is recommended, not required** — MAD is the menu + control panel, not an emulator installer. Without EmuDeck, use `install.sh --standalone` and bring your own emulators + ROMs (see §5).
- **Don’t edit settings files while ES‑DE is running** — ES‑DE rewrites them on exit and would discard your change. (The tools already guard against this.)
- **Re‑cabling the X‑Arcade** to a different USB port means **re‑identifying it once** in Preview.
- **Two identical controllers** with no unique ID can swap player order — distinct models pin exactly.
- The old separate‑window MAD panel is **retired**; MAD now lives inside ES‑DE. (`MAD.sh` is just a “MAD has moved” notice now.)

---

## 10. Where things live / quick glossary

**Paths**
- MAD tools & scripts: `~/Emulation/tools/launchers/`
- The patched ES‑DE you launch: `~/Applications/ES-DE.AppImage` (a small wrapper) → `~/Applications/ES-DE-MAD.AppImage` (the real build)
- ES‑DE config, themes, hooks: `~/ES-DE/` (`settings/`, `themes/`, `scripts/`, `gamelists/`)
- Per‑game **launch‑screen** art (ships *with the theme*): `~/ES-DE/themes/pixel-es-de/_launching-screens/`
- ES‑DE **startup splash** images (your own — documented here, *not* committed to any repo): `~/ES-DE/splashscreens/`
- Your personal controller overrides: `~/Emulation/tools/launchers/controller-policy.local.toml`
- Router log (for diagnosing a wrong assignment): `~/Emulation/storage/controller-router/router.log`

**Repos**
- MAD tools: `github.com/mmadalone/mad` (branch `main`); the patched ES‑DE source is branch `deck-patches`.
- Theme: `github.com/mmadalone/pixel-es-de`.

**Glossary**
- **EmuDeck** — the easy installer that sets up all your emulators + ES‑DE’s config (recommended, but optional — MAD has a standalone mode).
- **ES‑DE** — the game‑browser menu (we ship a patched build).
- **AppImage** — a single‑file Linux program; our ES‑DE ships as one.
- **Router** — the part of MAD that assigns controllers to players at launch.
- **Policy** — the plain rules file the router reads.
- **Transient** — MAD’s controller changes are temporary and revert when a game ends.
- **X‑Arcade** — an arcade‑stick controller; looks like an Xbox pad, told apart by USB port.
- **Sinden** — a camera‑based lightgun.
- **DolphinBar** — a Mayflash bar that connects Wii Remotes to the Deck.
- **Steam Input** — Steam’s controller layer; kept OFF for ES‑DE so MAD sees raw controllers.

---

*This guide describes the project as of June 2026. The technical reference for developers is in `README.md`; cached deep‑dive notes are in `deck-docs/`.*
