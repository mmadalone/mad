# SteamOS releases — notes relevant to the MPX / router / input rig

Cache for SteamOS version research. ALWAYS check here before re-fetching.

## SteamOS 3.8.10 (stable line 3.8.x)

Released to **stable for all users on 2026-06-17** (research date 2026-06-19).
Device under research was on **3.7.25 / BUILD_ID 20260520.1** at time of writing
(`/etc/os-release`), i.e. NOT yet on 3.8.x.

### Version components
- **Linux kernel 6.16** (up from 6.11 valve-neptune on 3.7).
- **KDE Plasma 6.4.3** (up from 6.2.5).
- **Updated Arch Linux base** + **updated Mesa/graphics driver** (specific Mesa
  version not stated in secondary reporting).
- Steam Deck LCD **BIOS update** (preliminary hibernation support).

### Wayland change — DESKTOP MODE ONLY (does NOT touch Game Mode / gamescope)
- **VERIFIED 2026-06-19 against Valve's OWN notes** at https://www.steamdeck.com/en/news
  (the steamcommunity.com announcement is JS-rendered and NOT fetchable; steamdeck.com/news
  reproduces the official changelog and IS fetchable). Verbatim official lines:
  - "KDE Plasma updated to version 6.4.3 from 6.2.5, **and now uses wayland by default**"
  - "**X11 support may still be selected** via Steam developer settings, **or via `steamosctl`**"
  - "Fixes several cases of reduced performance **in Desktop Mode** compared to Game Mode"
  - "Updated Linux kernel to 6.16"
  - The ONLY Game Mode display item in the notes: "Improved support for screen casting
    in Game Mode (e.g. OBS/Discord)" — NO gamescope/X11/Xwayland change to Game Mode.
  → The Wayland-by-default line is attached to **KDE Plasma = the Desktop session**. The
    notes contain NO statement that Game Mode's display server changed. ("Game Mode is still
    gamescope+Xwayland" is established architecture, corroborated by the ABSENCE of any
    gamescope change — it is not re-stated in the 3.8.10 notes.)
- GamingOnLinux (2026-06): **"Desktop Mode now uses Wayland by default"** and
  **"X11 support may still be selected via Steam developer settings."**
- The Wayland-by-default switch is the **KDE Plasma DESKTOP session**. It is NOT a
  change to Game Mode.
- **Game Mode was ALREADY a gamescope Wayland compositor that embeds Steam/games
  via Xwayland** — that architecture is UNCHANGED in 3.8. Secondary reporting found
  **no gamescope-specific changes** in the 3.8 changelog.
- Implication for our rig: anything that runs under **Game Mode + gamescope's
  Xwayland** (where XInput2 / xinput MPX create-master / reattach lives) is on the
  **same X11/Xwayland surface as 3.7**. The desktop-session Wayland default does not
  remove Xwayland and does not remove the ability to select X11 for the desktop.
  No evidence 3.8 breaks xinput-based MPX. (NOT independently re-verified on-device.)

### Input latency improvement = SCHEDULER, not the input/HID/evdev path
- The headline "controller input latency 5–8ms → 100–500µs" is delivered by the
  **LAVD CPU scheduler** (Latency-Aware Virtual Deadline, a **sched_ext** scheduler),
  i.e. a CPU-scheduling change to keep frame/input times consistent under load.
- It is **NOT** a change to evdev / uinput / the kernel input device API / udev
  input rules. So it does **not** alter how /dev/input devices enumerate or how
  our router/uinput virtual devices behave.
- Note this win is reported mainly for **non-Deck third-party handhelds**.

### Other input-related items (none touch evdev/uinput semantics)
- "Improved support for certain USB racing wheels and USB devices that boot in a
  non-standard mode."
- "Improved motion control support for handhelds with BMI260 IMUs."
- Wake-from-sleep using a connected Steam Controller.
- Fixed Legion Go trackpad losing function after sleep/resume (device-specific).

### Things to RE-VERIFY on-device before/after taking 3.8.x (a SteamOS update wipes /etc)
- **udev rules in /etc** (Sinden, MPX, samba) — wiped by every major update; reapply
  with `deck-post-update.sh` regardless of 3.8 specifics. No evidence 3.8 changed udev
  rule format/semantics.
- **Custom pacman packages** — wiped/reset by the atomic OS update as always; not a
  3.8-specific risk. kernel 6.16 + new Arch base means any out-of-tree/DKMS or
  kernel-version-pinned package must be rebuilt for 6.16.
- **distrobox (ES-DE build env)** — distrobox containers live in `~`, survive the OS
  update. Risk is only if the new host kernel (6.16) / podman / shared libs shift;
  no 3.8-specific distrobox breakage reported. Re-run a build to confirm.
- **xinput MPX (create-master / reattach / floating)** under gamescope Xwayland —
  no reported change, but CONFIRM on 3.8 since gamescope binary + Mesa changed even
  if no input change is documented.

### Sources (all accessed 2026-06-19)
- Phoronix, "SteamOS 3.8.10 Stable Released ... Wayland Desktop Default"
  https://www.phoronix.com/news/SteamOS-3.8.10-Stable
- Phoronix, "SteamOS 3.8 Preview ... KDE Plasma Desktop With Wayland By Default"
  https://www.phoronix.com/news/SteamOS-3.8-Preview
- GamingOnLinux (2026-06), "SteamOS 3.8 is out ... Desktop Mode upgrades, new Graphics Drivers"
  https://www.gamingonlinux.com/2026/06/steamos-3-8-is-out-with-initial-steam-machine-support-desktop-mode-upgrades-new-graphics-drivers/
- Lunar Computer (2026-06-18), "SteamOS 3.8 Reaches Stable With Linux 6.16, KDE Plasma 6.4 and Wayland by Default"
  https://lunar.computer/steamos-3-8-reaches-stable-with-linux-6-16-kde-20260618
- OSTechNix, "SteamOS 3.8 Released: Wayland, KDE Plasma 6 and New Hardware Support"
  https://ostechnix.com/steamos-3-8-released/
- Valve official announcement (JS-rendered, not machine-readable via fetch):
  https://steamcommunity.com/games/1675200/announcements/detail/697641379212298073
- ArchWiki Gamescope (gamescope = Wayland compositor; games via Xwayland; --expose-wayland for native Wayland clients)
  https://wiki.archlinux.org/title/Gamescope
