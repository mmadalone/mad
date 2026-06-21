# Changelog

All notable changes to the MAD tools are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com); the project ships
from `main` and tags releases (e.g. `v0.2.0`).

## [Unreleased]

### Added
- **Interactive component picker** (`whiptail`) on a fresh `install.sh` — choose theme /
  Sinden / Samba; choices saved to `install.conf` (gitignored; `install.example.conf`
  shipped). New flags `--express` (take defaults, no prompt) and `--reconfigure` (re-open the
  picker). `deck-post-update.sh` reads `install.conf` and re-applies **only opted-in**
  components (absent file = legacy "do everything"), so opted-out users aren't restored or nagged.
- **Activation hooks** now shipped (`hooks/`) and deployed by `install.sh` — launch-screens,
  Sinden auto-start, quit-combo watcher, Dolphin-Wii-mode / Wiimote-quit — previously owner-only,
  so these features now actually fire for a fresh install.
- **Model-aware suspend** (`suspend-mode-setup.sh`): LCD (Jupiter) pins deep/S3, OLED (Galileo)
  keeps s2idle — replaces the old unconditional deep-pin that was wrong on OLED. Re-applied by
  `deck-post-update.sh`.
- **Capability-adaptive MAD sidebar** + a **Sidebar** page (fork) — auto-hides Lightgun /
  X-Arcade / Bezel rows you can't use yet, with Auto / Always-show / Always-hide overrides.
- **On-demand bezel pack download** — the Bezel page fetches a system's pack from The Bezel
  Project when it's absent, so a bare user can get bezels for their installed games.

### Changed
- `deck-fetch-esde.sh` now downloads the patched-ES-DE AppImage from the fixed
  `latest-steamdeck` release tag instead of `/releases/latest`, matching the C++
  in-app updater and CI. This decouples the AppImage from GitHub's "latest"
  designation, so versioned releases (e.g. `v0.2.0`) can't change which build the
  Deck pulls.

## [0.2.0] - 2026-06-20

First pass at making the repo safe to install on someone else's Steam Deck
("share-readiness"). The maintainer's full rig (X-Arcade Tankstick, 2× Sinden,
multi-pad) is unaffected — every change below only alters the experience on a
fresh/other Deck.

### Fixed
- **Arcade launches no longer blocked on a Deck without an X-Arcade.** The
  "No X-Arcade detected" dialog now appears only once an X-Arcade has actually
  been identified; previously it blocked *every* arcade / OpenBOR / MUGEN launch
  (~30 s) on any Deck with no arcade stick.
- **`install.sh` fails loudly when ES-DE didn't install.** A failed patched-ES-DE
  download now stops with a clear, actionable recovery message instead of
  reporting success and leaving an unlaunchable front-end.
- **Controllers detected on first install.** `install.sh` now tells a new user to
  reboot / re-login (for the `input` group) *before* launching ES-DE — without it
  MAD silently sees no controllers. The advisory is suppressed under `--dry-run`,
  where nothing was actually changed.

### Changed
- **`controller-policy.example.toml` is now a neutral, fully-commented template.**
  Installing it seeds a clean default policy instead of the maintainer's personal
  setup (stale X-Arcade USB port, a custom splash image, oversized fonts, a Sinden
  lightgun collection). The shipped `controller-policy.toml` carries no
  rig-specific active stanzas, so a bare Deck gets sane defaults and rig-specific
  warnings stay silent until the matching hardware is identified.
- **Hardware-neutral labels** in the `--standalone` systems template (dropped
  "(X-Arcade)" and "(Linux Sinden, 2-player)" from emulator command labels).
- README / GUIDE updated to describe the example policy as a neutral template.

### Removed
- **Maintainer-private files no longer ship in a public clone:**
  `openbor-metadata.json`, `romhack-*.json`, `skyscraper-flagged.json`,
  `review-findings/`, a stray `squashfs-root → ./AppDir` symlink, and the
  superseded `install-bezels*.sh`. Local copies are kept; nothing on the install
  path referenced them.
- Obsolete `model2-m2emu.sh` (renamed to `model-2-emulator.sh`).
