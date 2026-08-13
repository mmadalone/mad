# Sinden standalone installer — audit + build plan

**Source:** multi-agent file audit, 2026-06-19 (this session). High confidence on coupling/trigger/installer/firmware findings; verified against live files. Companion memory: `sinden-standalone-portability`.
**Purpose:** brief a future session that builds a USER-FACING Sinden installer for people who are NOT on our exact MAD/ES-DE/EmuDeck rig. Covers both a lightweight "cherry-pick" share and a real `sinden-setup` product.

> ⚠️ A parallel session is/was working on the launchers repo (`~/Emulation/tools/launchers`, branch likely `mad-standalones-pages`). This doc is design knowledge only. When implementation starts, branch fresh and do not clobber its work. Closed-source `LightgunMono.exe` must never be redistributed — download it from sindenlightgun.com.

---

## TL;DR verdict
You can't ship "just install our scripts." The gun-driving **engine** is portable; the **car** around it (when to start, per-emulator configs, the multi-pad arcade layer, the two-guns-are-clones firmware reality) is welded to this rig. Two realistic deliverables:
- **Track A — Cherry-pick share** (hours): hand 3 files + a firmware writeup to a tinkerer. Honest, low effort.
- **Track B — Real `sinden-setup`** (a genuine mini-project): a frontend-neutral installer. Scoped below.

---

## Track A — "Cherry-pick these files + the firmware step"

Give this to anyone who already has the **official Sinden driver (LightgunMono) working for two guns** and just wants our **smoothing + two-cursor separation** on top.

### The firmware step (PREREQUISITE — do this first)
Two Sinden guns ship identical (`product=SindenLightgun`, `serial=HIDLG`); Linux can't tell them apart, so the 2nd clobbers the 1st's device symlink. Our rig separates them purely by **distinct USB Product IDs**, set in the gun firmware:
- Using **Sinden's own config/firmware tool**, give each gun a different PID. Our rig uses `16c0:0f38` (P1) and `16c0:0f39` (P2). Stock firmware commonly enumerates as `16c0:0f01/0f02`.
- You can pick any two distinct PIDs — just make the udev rules below match what you chose.
- Cameras are also pinned (`16d0:1098`=P1, `16d0:1097`=P2) — match those too if you use camera-based features.

### The 3 files to cherry-pick
1. **`99-sinden-lightgun.rules`** (our udev rules → copy to `/etc/udev/rules.d/`, then `sudo udevadm control --reload && sudo udevadm trigger`). Edit the `idProduct` values to YOUR two gun PIDs (and camera PIDs). This is what creates the stable `/dev/input/sinden-gun-p1-event` / `-p2-event` symlinks the smoother reads, and grants your user access to the smoothed virtual mice. **Without this, nothing downstream works.**
2. **`sinden-smoother.py`** (the actual jitter smoother → EVIOCGRAB the two raw guns, EMA/deadzone, emit two named uinput mice `SindenLightgun Mouse (Smoothed P1/P2)`). One edit needed: replace the `from lib import mad_paths` / `mad_paths.storage("sinden","smoother.ini")` line with a plain path (e.g. `~/.config/sinden/smoother.ini`) or hardcode defaults. Deps: `python-evdev` + the kernel `uinput` module. Run it in the background before launching games.
3. **`sinden-mpx-setup.sh`** (OPTIONAL — only for Dolphin/Wii 2-player). Pure `xinput`: creates a second X11 master pointer ("Sinden P2") and reattaches the P2 gun to it so the two guns' **menu cursors** don't merge in Dolphin's UI. Needs `xorg-xinput` + an X session. NOT needed for RetroArch (it separates the two guns by mouse index on the two smoothed devices).

> +1 optional: **`sinden-serial-preflight.py`** if you run LightgunMono with two guns and want recoil/trigger pinned to the right player (it ties LightgunMono's `SerialPortWrite`/`...P2` to each gun's PID-pinned `/dev/sinden-tty-p{1,2}` so a replug/enumeration-order flip doesn't swap players).

### How each emulator then consumes it (what the user wires themselves)
- **RetroArch:** input driver = `udev`; set `input_player1_mouse_index` / `input_player2_mouse_index` to the two `Smoothed P1/P2` devices. (Our `sinden-update-retroarch-mouseindex.py` auto-detects these by name each start — portable except the hardcoded Flatpak path; the user can lift its detection logic.) No MPX needed.
- **Dolphin (Wii):** in `WiimoteNew.ini`, bind each emulated Wiimote's `Device =` to `evdev/0/SindenLightgun Mouse (Smoothed P1)` and `(Smoothed P2)`, and bind IR + buttons to that device. This is the **hand-authored** file — there is no generator (yet). Run `sinden-mpx-setup.sh` for the menu-cursor fix.
- **Trigger:** neither RetroArch nor Dolphin has a native pre/post-launch hook, so wrap the launch: `sinden-smoother.py & ; <emulator> … ; kill the smoother`. (On our rig the ES-DE game hooks do this automatically — outsiders replace that with a wrapper.)

---

## Track B — A real, shippable `sinden-setup`

Scope to turn the above into a one-command install an outsider can trust. Roughly ordered.

### 1. Decouple from the MAD repo (vendoring)
- The sinden scripts source `lib/mad-paths.sh` / `from lib import mad_paths, fsutil` and `lib/devices.py`. Vendor the **Sinden subset** of these (the `mad_paths` resolver, `fsutil.atomic_write` minus the `staterev` page-cache bump, the `devices.py` Sinden helpers) into a self-contained tree so the scripts run from ANY directory.
- Remove the `fsutil → staterev.bump("config")` coupling (harmless off-rig, but a stray MAD-backend tie).
- Drop the hardcoded `$HOME/Emulation/tools/launchers/...` sibling-path and `$HOME/Lightgun` assumptions → resolve relative to an install root + a config var.

### 2. One installer entrypoint
Today setup is split: `sinden-install.sh` (driver download only) + `sinden-reinstall-deps.sh` (pacman deps + udev + smoother shim + input group, but repo-path-hardcoded) + `deck-post-update.sh` orchestrates them. Build a single `sinden-setup.sh` that:
- Downloads the official Sinden driver from sindenlightgun.com (pinned version, with a checksum) into a configurable dir — **never bundle it** (closed source).
- Installs deps with a **distro-aware** path (SteamOS: `steamos-readonly disable` + pacman keyring init + the package set `mono sdl12-compat sdl sdl_image v4l-utils gcc glibc linux-api-headers xorg-xinput python-evdev`; document apt/dnf equivalents for non-Deck Linux).
- Installs the udev rule, builds the `sinden-smooth.so` shim (gcc), adds the user to `input`, reloads udev. Prints the "log out / reboot" reminder.

### 3. Parameterize the hardware identity
- Make gun PIDs, camera PIDs, and the smoothed-mouse device names **config variables**, not hardcoded in udev/devices.py/supermodel.
- Ship a **firmware-step doc + helper**: walk the user through assigning two distinct PIDs with Sinden's tool, OR auto-generate the udev rules from whatever two distinct Sinden PIDs are currently detected. (Detect: enumerate `16c0:*` Sinden guns; if both share a PID, stop and explain the firmware step.)

### 4. Generate per-emulator configs (don't assume hand-made files)
- **Dolphin:** generate `WiimoteNew.ini` (two emulated-Wiimote profiles, Sinden source, IR + side-button bindings) from a template keyed on the configured smoothed-mouse names. This is the single biggest missing piece for "2P Wii out of the box."
- **RetroArch:** ship a documented mouse_index setup + the auto-detect helper (path made configurable, not Flatpak-hardcoded), and a per-core lightgun note (e.g. Flycast Port=Light Gun + crosshair).

### 5. Frontend-neutral trigger
- Provide a documented `sinden-start.sh; <emulator> …; sinden-stop.sh` wrapper pattern (Steam launch option / per-game `.sh` / RetroArch wrapper), since RetroArch & Dolphin have no native pre/post hooks. Keep the ES-DE hook path as the "if you also use ES-DE" option.

### 6. Fence the multi-pad / Supermodel layer
- The Supermodel path (`supermodel-proton.sh`, `supermodel-sinden-smart.py`) hardcodes GE-Proton10-34, X-Arcade `1241:1111`, DualSense, MAD data paths. Ship it (if at all) as a clearly-labeled **optional, rig-specific module** — do NOT present it as part of a generic "all-systems Sinden" offering. Generic "other systems" = RetroArch's own per-core lightgun setup, which is the user's job regardless.

### 7. Honest docs + a real second-machine test
- Document: the firmware PID step, the closed-source driver download, the per-emulator config, SteamOS package/readonly/keyring caveats and that they re-do after each SteamOS update, and the X11-vs-Wayland note (MPX needs X11/Xwayland; fine in Game Mode/gamescope).
- **Test on a second machine with stock guns** before calling it shippable. The whole audit is static; nobody has run this cold on fresh hardware.

---

## Reusable facts to carry forward
- **Smoother is the real 2P mechanism everywhere; MPX is only a Dolphin menu-UI band-aid** (Dolphin #13628). RetroArch = udev mouse_index; Supermodel = ManyMouse and even *detaches* MPX before launch. (See memory `sinden-lightgun-capability`.)
- **`sinden-start.sh`/`sinden-stop.sh` are already standalone-runnable** (no ES-DE/router/collection import) — the rig machinery only decides *when* to call them.
- **Closed-source driver** = the permanent constraint: download-only, pinned version, external-URL dependency.
- The closest existing shipping thread is the **whole-MAD-stack-minus-EmuDeck** request (memory `turnkey-install`), not a Sinden-only package — decide which product you're actually building before starting.
