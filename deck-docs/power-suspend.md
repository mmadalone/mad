# Steam Deck — power / sleep / suspend (config + crash diagnosis)

Cache of findings on Gaming-Mode power settings and the suspend crash.
Investigated 2026-06-09.

## Where the Gaming-Mode "Power" settings live
`Settings → Power` (sleep timeout, screen-dim timeout) are stored by Steam in:
`~/.local/share/Steam/config/config.vdf`  →  `System` block:
```
"IdleBacklightDimBatterySeconds"  "0"   # screen-dim timeout, battery   (0 = disabled)
"IdleBacklightDimACSeconds"       "0"   # screen-dim timeout, AC
"IdleSuspendBatterySeconds"       "0"   # auto-sleep timeout, battery    (0 = disabled)
"IdleSuspendACSeconds"            "0"   # auto-sleep timeout, AC
```
- Steam keeps these in RAM and persists `config.vdf` with an **atomic write**
  (`config.vdf.asyncNNNN.tmp` → rename). It flushes periodically and on clean exit.
  A hard-reset before the flush loses in-session changes → leftover `*.async*.tmp`
  files are proof of an interrupted write. Steam DOES also flush live in-session
  when a setting changes (observed mtime bump with no new orphan).
- Factory defaults written by Steam = dim **300/300**, suspend **600/600** (seconds).
- `es_settings`-style rule: don't hand-edit `config.vdf` while Steam runs — Steam
  rewrites it on exit and clobbers edits. Change via the UI, or with Steam closed.

## BUG: DeckyInhibitScreenSaver resets the power settings on every uninhibit
Plugin `xfangfang/DeckyInhibitScreenSaver` ("Inhibit screensaver during video playback").
Source: https://github.com/xfangfang/DeckyInhibitScreenSaver `src/index.tsx` (read 2026-06-09).
- On **inhibit**:   `updateSetting(0, 0, 0, 0)`        → dim+suspend disabled.
- On **uninhibit**: `updateSetting(300, 300, 600, 600)` → **HARDCODED factory defaults**.
- `updateSetting(battery_idle, ac_idle, battery_suspend, ac_suspend)` maps 1:1 to the
  four `Idle*` keys above and writes them via `SteamClient.System.UpdateSettings` /
  `SteamClient.Settings.SetSetting`. It does **NOT** save/restore the user's real values.
- Net effect: any app that triggers a screensaver-inhibit cycle (e.g. something in the
  ES-DE launch path) clobbers "disable sleep" back to 600/300 on release. Reproduced &
  captured live (watcher: 0→600 then 0→300, ms after `UnInhibit cookie=4`).
- Fix for our use case: **uninstall the plugin** (we keep sleep disabled globally, so a
  screensaver-inhibitor is both redundant and actively harmful).

## SUSPEND MODE: this LCD Deck uses deep/S3 — s2idle is FORBIDDEN by a kernel quirk
**Corrected 2026-06-11. The previous version of this section said the opposite and was wrong.**
- This is an **LCD Steam Deck** (kernel `linux-neptune-611`). Its kernel carries a Valve DMI
  quirk that disables s2idle. Proof in `journalctl -k`:
  ```
  PM: Steam Deck quirk - no s2idle allowed!
  ACPI: PM: (supports S0 S3 S4 S5)     # S3 yes; S0ix/s2idle NOT advertised
  ```
  Suspend mode here is **deep (S3)** — the firmware/kernel default. (s2idle/S0ix is the
  **OLED** model's mode; do not apply OLED suspend lore to this LCD unit.)
- **DON'T force s2idle.** If `mem_sleep` is set to s2idle, every power-button press does:
  `PM: suspend entry (s2idle)` → `PM: s2idle sleep is not supported` → `PM: suspend exit`
  i.e. the suspend animation shows then the screen comes right back — it never sleeps.
  (This exact mistake was made 2026-06-09 and caused the "power button won't sleep" symptom
  reported 2026-06-11; reverting to deep fixed it.)
- **The original "crash on sleep / hard-reset" was the rogue Decky plugins, not the mode.**
  After uninstalling `decky-autosuspend` (firing auto-suspends) + `DeckyInhibitScreenSaver`,
  **deep/S3 suspended and woke cleanly** (on-screen test 2026-06-11, no hard-reset).
- The earlier "FUSE freezer deadlock at suspend" theory (`statx → fuse_lookup →
  request_wait_answer`) was **not borne out**: deep suspended fine with the always-on
  `xdg-document-portal` FUSE mount present. The `request_wait_answer` deadlock in memory
  `esde-appimage-fuse-deadlock` is a real but **separate** issue (ES-DE *launch* path), not suspend.
- **Current state / fix:** keep `mem_sleep` = **deep** (default). Pinned for clarity via
  `/etc/tmpfiles.d/99-mem_sleep.conf` → `w /sys/power/mem_sleep - - - - deep`, re-created +
  applied by `deck-post-update.sh` step 9/9 (flagged by `--check`) since a SteamOS update wipes `/etc`.
  Live revert command if ever needed: `echo deep | sudo tee /sys/power/mem_sleep` → expect `s2idle [deep]`.

## ES-DE "SUSPEND SYSTEM" menu row (Main Menu → Quit) — investigated 2026-06-11
- **Stock ES-DE** (base v3.4.1, NOT a MAD patch): `GuiMenu.cpp:2506-2528`, inside `#if !defined(__HAIKU__)`.
- On "REALLY SUSPEND? → YES" it: (1) `Scripting::fireEvent("suspend")` — runs any
  `~/ES-DE/scripts/suspend/` hooks (none on this Deck → no-op), then (2) `runSuspendCommand()`
  = **`systemctl suspend`** on Linux/systemd (`es-core/src/utils/PlatformUtil.cpp:86`;
  Windows/FreeBSD/macOS have their own branches). Returns non-zero → logs "Couldn't suspend system".
- `systemctl suspend` is the CLI wrapper around the **same logind → kernel `/sys/power/state`
  → `mem_sleep` path** that Steam's power-button suspend uses. So it's gated by the same
  things: it bounced instantly while `mem_sleep`=s2idle (the "does nothing" report), and works
  now under `deep`. No special gamescope/steamos path needed.
- Verified non-destructively (no actual suspend fired): session `Active=yes` (logind permits
  the `deck` user), and **no inhibitor blocks suspend** — `systemd-inhibit --list` shows only
  NetworkManager (pre-sleep `delay`) + PowerDevil (`block` on power-KEY *handling* only, not `sleep`).

## Decky plugins & the suspend lifecycle (audit 2026-06-09)
Enabled plugins that hook suspend/resume — none break suspend at the kernel level:
- DeckyInhibitScreenSaver → calls `System.UpdateSettings` (the settings-reset bug above).
- decky-spy / SDH-PlayTime / TabMaster → benign (reattach monitor / log times / refresh tabs).
- PowerTools → root backend reapplies on resume; global profile = governor `performance`
  only (no undervolt / TDP cap / fixed clocks) → stable.
- Decky-Undervolt → registers a resume hook but `status: Disabled`, `runAtStartup: false` → inactive.
- SDH-PauseGames → hooks suspend/resume but is **disabled** in loader.json.
- SDH-AnimationChanger → **no** suspend/resume hook; only swaps boot/suspend/throbber
  `.webm` via symlinks in `…/config/uioverrides/movies/` (files on ext4, not FUSE) → cosmetic.
- `decky-autosuspend` (AutoSuspend) → was actively firing `triggeredAction: suspend`;
  **uninstalled** 2026-06-09 (it was the auto-sleep trigger).

## CORRECTION 2026-06-21 — this unit is an OLED (Galileo), NOT an LCD
Earlier notes here (and `deck-post-update.sh`) asserted "this is an LCD Steam Deck" and
unconditionally pinned `mem_sleep=deep`. **That was wrong for this hardware.** Verified live:
`/sys/class/dmi/id/{product_name,board_name}=Galileo`, `product_family=Sephiroth`,
`sys_vendor=Valve` -> this is the **2023 OLED**. `mem_sleep` read `s2idle [deep]` (OLED
firmware offers s2idle; an LCD would show only `[deep]`).
- The `neptune-611` kernel name is **NOT** a model signal — it runs on BOTH LCD and OLED.
  The DMI codename is: **Jupiter = LCD, Galileo = OLED**.
- `deep`/S3 is a genuine **LCD-only** workaround (the LCD kernel quirk refuses s2idle:
  "PM: Steam Deck quirk - no s2idle allowed!"). On **OLED** the correct mode is **s2idle**.
- FIX: the live deep pin was moved to `~/Downloads/_TMP-suspend-fix-*` and `mem_sleep` set
  back to s2idle (2026-06-21). Going forward, `suspend-mode-setup.sh` (called by `install.sh`
  + `deck-post-update.sh` step 9) is **model-aware**: LCD->pin deep, OLED->leave s2idle (+ clear
  any stale pin). `MAD_DECK_MODEL=lcd|oled` overrides detection for tests.

## CORRECTION 2026-06-22 — the 2026-06-21 "OLED → s2idle" change was WRONG for THIS unit
**Decisive live evidence (`journalctl -kb`, this boot):**
```
PM: Steam Deck quirk - no s2idle allowed!
ACPI: PM: (supports S0 S3 S4 S5)          # only S3 advertised — NO s2idle/S0ix
```
The "no s2idle allowed!" quirk fires on THIS unit **even though it is an OLED (Galileo)**. So the
DMI model is NOT the deciding factor — **the kernel quirk is.** Forcing `s2idle` here (both the
2026-06-21 "correction" AND the Phase-0 one-liner `echo s2idle | sudo tee /sys/power/mem_sleep`)
makes every power-button press do the dim-then-immediately-wake bounce ("sleeps but comes back up",
reported 2026-06-22). **`deep` (S3) is the only working mode on this Deck — confirmed: sleep works
on `deep` 2026-06-22.**
- **The real rule:** decide by **s2idle support**, NOT by DMI model. If `journalctl -kb` shows
  `no s2idle allowed!` (or ACPI advertises only S3, no S0ix) → use **deep** regardless of OLED/LCD.
  Only allow/force s2idle if the kernel actually offers it.
- **✅ FIXED 2026-06-22 (launchers `main` 456d6bd):** `suspend-mode-setup.sh` now decides by **DMI**
  (`is_steam_deck()` — any Valve Jupiter/Galileo → `deep`), NOT the model-`→`-s2idle mapping and NOT
  the quirk STRING. **Important:** an interim "gate on the quirk string in `journalctl -kb`" version
  was ALSO wrong — that string **ages out of `-kb` within ~an hour on the 4 MB kernel ring** (verified
  live: present right after boot, gone later, SAME boot), so "string absent" wrongly read as
  "s2idle supported" and would have removed the deep pin + set s2idle. DMI is the only reliable
  signal. The journal-string path is kept only for NON-Steam-Decks. `deck-post-update.sh` step 9
  re-pins `deep` post-update; pin = `/etc/tmpfiles.d/99-mem_sleep.conf` → `w /sys/power/mem_sleep - - - - deep`.
  Moved-out pins live in `~/Downloads/_TMP-suspend-fix-*`. (35-agent review confirmed the regression.)
- Secondary: this Deck is often **docked** (RTL8153 USB-Ethernet had `power/wakeup=enabled`). Under
  `deep` that did NOT prevent a clean sleep (2026-06-22 test passed), so the wake source was a red
  herring — the cause was the s2idle force. Keep in mind only if `deep` ever starts bouncing.
