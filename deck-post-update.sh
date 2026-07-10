#!/usr/bin/env bash
# ============================================================================
# deck-post-update.sh — run this AFTER a SteamOS (system) update.
#
# A SteamOS update keeps /home but RESETS the immutable root (/usr, /etc, pacman
# packages). So everything under /home survives untouched — ES-DE (incl. our
# PATCHED build), ~/Emulation, ~/ROMs, ~/Applications, distrobox *containers*
# (~/.local/share/containers), ~/esde-build. You do NOT need deck-restore.sh
# (that's for a new Deck / disaster). You only need to RE-APPLY the root-level
# things the update wiped, which this script does:
#   1. Samba file sharing            (root pacman -> wiped)
#   2. Sinden system deps (mono/sdl) (root pacman -> wiped)
#   3. Lightgun udev rule            (/etc reset)
#   4. 'input' group membership      (/etc reset)
#   5. distrobox tooling check       (/usr/bin -> wiped; containers survive)
#   6. patched-ES-DE sanity check    (lives on /home -> should be intact)
#   7. MAD panel health             (mad-backend.py --selfcheck + live lib/; tk/evdev support deps)
#   8. controller-router integration (router scripts + ES-DE game-start/end hooks)
#   9. Suspend mode deep/S3 (mem_sleep)  (/etc reset; this kernel's quirk forbids s2idle)
#  10. VNC: re-pin Desktop Mode to X11 (update resets default->Wayland; only if VNC enabled)
#
# Safe to re-run. Needs sudo for the root bits (run from a Desktop-mode terminal).
# (NOTE: an EmuDeck/ES-DE *app* update is separate — that overwrites
#  ~/Applications/ES-DE.AppImage with stock; see esde-patched-build memory to rebuild.)
# ============================================================================
set -uo pipefail
T="$HOME/Emulation/tools"; L="$T/launchers"
log(){ echo "[post-update] $*"; }

# Component gating: expose want() from install.conf. ABSENT install.conf => want() is
# ALWAYS true = legacy "do everything", so existing setups are unaffected. Sourced before
# check_missing() (which uses want()). Define a fallback want() FIRST so a missing
# install-conf.sh can't invert every gate to SKIP (which would make this restore script
# do NOTHING and falsely report all-OK) — mirrors install.sh.
want() { return 0; }
# shellcheck source=lib/install-conf.sh
[ -f "$L/lib/install-conf.sh" ] && . "$L/lib/install-conf.sh"

# --- read-only HEALTH CHECK (no sudo, no restore) — used by esde-health-check.sh at
#     ES-DE launch to detect what a SteamOS update wiped. Prints each MISSING component
#     (one per line) to stdout; exit 0 = all present, 1 = something missing. ---
check_missing(){
  local miss=0; _gone(){ echo "$1"; miss=1; }
  { [ -f "$HOME/Applications/ES-DE-MAD.AppImage" ] \
      && grep -q 'ES-DE-MAD' "$HOME/Applications/ES-DE.AppImage" 2>/dev/null; } \
      || _gone "Patched ES-DE (MAD) build"
  python3 -c 'import tkinter' 2>/dev/null || _gone "Tk (warning dialogs)"
  python3 -c 'import yaml' 2>/dev/null || _gone "PyYAML (RPCS3 input mapping)"
  python3 "$L/mad-backend.py" --selfcheck >/dev/null 2>&1 || _gone "MAD backend (mad-backend.py --selfcheck)"
  local crmiss=0
  for f in "$L/controller-router.py" "$L/controller-router-wrap.sh" "$L/controller-policy.toml" \
           "$L/mad-switch-launch.py" \
           "$HOME/ES-DE/scripts/game-start/04-controller-router-setup.sh" \
           "$HOME/ES-DE/scripts/game-start/05-controller-router-standalone.sh" \
           "$HOME/ES-DE/scripts/game-end/00-controller-router.sh" \
           "$HOME/ES-DE/scripts/game-end/06-mad-switch-restore.sh" \
           "$HOME/ES-DE/scripts/game-start/03-mad-power.sh" \
           "$HOME/ES-DE/scripts/game-start/06-dolphin-res.sh" \
           "$HOME/ES-DE/scripts/game-end/07-mad-power-restore.sh" \
           "$HOME/ES-DE/scripts/game-end/08-dolphin-res-restore.sh"; do
    [ -r "$f" ] || crmiss=1
  done
  [ "$crmiss" -eq 0 ] || _gone "Controller routing scripts/hooks"
  groups | grep -qw input || _gone "'input' group membership (controllers)"
  [ -x /usr/bin/steamos-polkit-helpers/steamos-priv-write ] \
    || _gone "steamos-priv-write (on-the-go TDP watt cap privilege)"
  # Optional components: only probe what the user opted into, so opted-out users aren't
  # nagged. want() true when install.conf is absent (legacy) — so old setups still probe all.
  want INSTALL_SINDEN && { command -v mono >/dev/null 2>&1 || _gone "Sinden lightgun deps (mono/SDL)"; }
  want INSTALL_SINDEN && { [ -f /etc/udev/rules.d/99-sinden-lightgun.rules ] || _gone "Sinden lightgun udev rule"; }
  want INSTALL_SAMBA  && { command -v smbd >/dev/null 2>&1 || _gone "Samba file sharing"; }
  # Suspend: quirk-aware — delegate to the setup script's --check. Kernels that forbid s2idle
  # (the LCD AND this OLED, per the 'no s2idle allowed' quirk) want the deep pin; a kernel that
  # truly supports s2idle wants it absent; INSTALL_SUSPEND=off = always OK.
  [ -x "$L/suspend-mode-setup.sh" ] && { "$L/suspend-mode-setup.sh" --check >/dev/null 2>&1 \
    || _gone "Suspend mode (mem_sleep) for this Deck model"; }
  return "$miss"
}

if [ "${1:-}" = "--check" ]; then
  check_missing; exit $?
fi

# (Re)write the ES-DE.AppImage wrapper so it launches our patched build from a PERMANENTLY
# EXTRACTED AppDir instead of FUSE-mounting the AppImage (no squashfuse /tmp mount → a native
# Steam game launched from ES-DE can't deadlock on it). Defined here near the top so the
# `--wrapper` mode and install.sh can reuse this single source of truth.
rewrite_wrapper(){
  # If the current ES-DE.AppImage is a stock one (NOT already our wrapper), keep it as the
  # emergency fallback before we overwrite it.
  [ -s "$HOME/Applications/ES-DE.AppImage" ] \
    && ! grep -q 'ES-DE-MAD' "$HOME/Applications/ES-DE.AppImage" 2>/dev/null \
    && cp -f "$HOME/Applications/ES-DE.AppImage" "$HOME/Applications/ES-DE.AppImage.real" 2>/dev/null \
    && log "    kept the current stock AppImage as ES-DE.AppImage.real (emergency fallback)"
  if ! cat > "$HOME/Applications/ES-DE.AppImage" <<'WRAP'
#!/usr/bin/env bash
# Runs our MAD ES-DE build from a PERMANENTLY EXTRACTED AppDir instead of FUSE-mounting
# the AppImage. WHY: a native Steam game launched from ES-DE deadlocks reading ES-DE's
# squashfuse /tmp mount (the game's pressure-vessel container sees /tmp/.mount_ESDE* and
# its asset loader blocks forever on request_wait_answer). Running the extracted AppRun
# creates NO FUSE mount, so the deadlock can't happen. The AppDir is re-extracted
# automatically whenever the source AppImage changes (rebuild / deck-fetch-esde.sh / CI),
# keyed on the AppImage's mtime:size stamp. Splash is still regenerated first.
[ -x "$HOME/Emulation/tools/launchers/esde-splash-gen.sh" ] && \
  "$HOME/Emulation/tools/launchers/esde-splash-gen.sh" 2>/dev/null || true
# Source AppImage (fall back to the stock build kept as ES-DE.AppImage.real).
IMG="$HOME/Applications/ES-DE-MAD.AppImage"
[ -x "$IMG" ] || IMG="$HOME/Applications/ES-DE.AppImage.real"
# Make ES-DE's in-app updater (F4) target the AppImage, not the extracted AppDir
# binary: getEsBinary() returns $APPIMAGE when set (FileSystemUtil.cpp), and it's
# the ONLY thing in ES-DE that reads $APPIMAGE (resource/theme paths use the exe
# path, unaffected). Without this the updater would overwrite AppDir/usr/bin/es-de
# and corrupt the extracted dir. With it, the updater replaces THIS file and the
# re-extract check below rebuilds the AppDir on the next launch.
export APPIMAGE="$IMG"
# Let ES-DE's in-app updater re-exec THIS wrapper for an auto-restart after an
# update (QuitMode::RESTART) — re-running the wrapper re-extracts the freshly
# installed AppImage. execl keeps the same PID, so Game Mode stays seamless.
export MAD_WRAPPER="$HOME/Applications/ES-DE.AppImage"
APPDIR="$HOME/Applications/ES-DE-MAD.AppDir"
STAMP="$APPDIR/.src-stamp"
WANT="$(stat -c '%Y:%s' "$IMG" 2>/dev/null)"
# (Re)extract if the AppDir is missing or the source AppImage changed.
if [ ! -x "$APPDIR/AppRun" ] || [ "$(cat "$STAMP" 2>/dev/null)" != "$WANT" ]; then
  TMP="$HOME/Applications/.esde-extract-tmp"
  # Don't wipe a working AppDir we can't replace: if ~/Applications can't hold ~2x the
  # AppImage size, keep the existing (possibly stale) AppDir instead of re-extracting into
  # a full disk and falling through to the FUSE path (which re-introduces the native-Steam
  # launch deadlock this wrapper exists to avoid).
  NEED=$(stat -c '%s' "$IMG" 2>/dev/null || echo 0)
  AVAIL=$(df -kP "$HOME/Applications" 2>/dev/null | awk 'NR==2{print $4*1024}')
  if [ "${AVAIL:-0}" -lt "$((NEED*2))" ]; then
    echo "ES-DE wrapper: low disk on ~/Applications (need ~$((NEED*2/1048576))MB) — keeping existing AppDir, NOT re-extracting" >&2
    exec "$APPDIR/AppRun" "$@" 2>/dev/null || exec "$IMG" "$@"
  fi
  rm -rf "$APPDIR" "$TMP"
  if mkdir -p "$TMP" && ( cd "$TMP" && "$IMG" --appimage-extract >/dev/null 2>&1 ); then
    # --appimage-extract emits squashfs-root (a symlink to ./AppDir on the ES-DE build);
    # move the REAL directory, not the symlink.
    SRC="$TMP/squashfs-root"
    [ -L "$SRC" ] && SRC="$TMP/$(readlink "$SRC" | sed 's#^\./##')"
    # Only stamp the extract as good if the AppDir is actually runnable, so a truncated
    # extract (power loss / disk full mid-copy) isn't cached as current and re-extracts next time.
    if mv "$SRC" "$APPDIR" && [ -x "$APPDIR/AppRun" ] && [ -x "$APPDIR/usr/bin/es-de" ]; then echo "$WANT" > "$STAMP"; fi
  fi
  rm -rf "$TMP"
  # If extraction failed, run the AppImage directly so ES-DE always launches.
  [ -x "$APPDIR/AppRun" ] || { echo "ES-DE wrapper: extraction failed — falling back to FUSE AppImage (Steam-game-launch deadlock possible); free up disk and relaunch" >&2; exec "$IMG" "$@"; }
fi
exec "$APPDIR/AppRun" "$@"
WRAP
  then
    log "    FATAL: failed to write the ES-DE.AppImage wrapper (disk full / read-only FS?)"
    return 1
  fi
  chmod +x "$HOME/Applications/ES-DE.AppImage"
  log "    wrapper → ES-DE-MAD.AppImage"
}

# extract_appdir : eagerly (re)extract ES-DE-MAD.AppImage -> ES-DE-MAD.AppDir WITHOUT
# launching ES-DE. The wrapper extracts lazily on first launch, but install.sh
# --standalone needs the bundled es_systems.xml NOW (to seed custom_systems), so the
# AppDir must exist before seeding. Mirrors the wrapper's extract block; idempotent
# (skips if already extracted + current). Best-effort: a failure just defers to the
# wrapper's lazy extract on first launch.
extract_appdir(){
  local IMG="$HOME/Applications/ES-DE-MAD.AppImage"
  local APPDIR="$HOME/Applications/ES-DE-MAD.AppDir"
  local STAMP="$APPDIR/.src-stamp"
  [ -x "$IMG" ] || { log "    extract: ES-DE-MAD.AppImage not present — skipping"; return 1; }
  local WANT; WANT="$(stat -c '%Y:%s' "$IMG" 2>/dev/null)"
  if [ -x "$APPDIR/AppRun" ] && [ "$(cat "$STAMP" 2>/dev/null)" = "$WANT" ]; then
    log "    AppDir already extracted + current"; return 0
  fi
  local NEED AVAIL; NEED=$(stat -c '%s' "$IMG" 2>/dev/null || echo 0)
  AVAIL=$(df -kP "$HOME/Applications" 2>/dev/null | awk 'NR==2{print $4*1024}')
  if [ "${AVAIL:-0}" -lt "$((NEED*2))" ]; then
    log "    extract: low disk on ~/Applications — NOT extracting (lazy extract on first launch)"; return 1
  fi
  local TMP="$HOME/Applications/.esde-extract-tmp"
  rm -rf "$APPDIR" "$TMP"
  if mkdir -p "$TMP" && ( cd "$TMP" && "$IMG" --appimage-extract >/dev/null 2>&1 ); then
    local SRC="$TMP/squashfs-root"
    [ -L "$SRC" ] && SRC="$TMP/$(readlink "$SRC" | sed 's#^\./##')"
    if mv "$SRC" "$APPDIR" && [ -x "$APPDIR/AppRun" ] && [ -x "$APPDIR/usr/bin/es-de" ]; then
      echo "$WANT" > "$STAMP"; log "    extracted AppDir (bundled resources available)"
    fi
  fi
  rm -rf "$TMP"
  [ -x "$APPDIR/AppRun" ] || { log "    extract: failed (will lazy-extract on first ES-DE launch)"; return 1; }
  return 0
}

# --wrapper : just (re)write the launch wrapper, nothing else. Used by install.sh right
# after a fresh deck-fetch-esde.sh (the AppImage exists but the wrapper doesn't yet).
# --extract : eagerly extract the AppDir (install.sh --standalone seeds from bundled).
if [ "${1:-}" = "--wrapper" ]; then
  rewrite_wrapper; exit $?
fi
if [ "${1:-}" = "--extract" ]; then
  extract_appdir; exit $?
fi

# One sudo prompt up front (steps 1-3 + 9 need root); keep the timestamp warm so the
# per-step sudo calls below don't re-prompt mid-run. Best-effort: if sudo can't auth,
# the root steps just log their failure and the run continues.
FAILED=""
if sudo -v 2>/dev/null; then
  ( while sudo -n true 2>/dev/null; do sleep 50; kill -0 "$$" 2>/dev/null || exit; done ) &
  SUDO_KEEPALIVE_PID=$!
  trap '[ -n "${SUDO_KEEPALIVE_PID:-}" ] && kill "$SUDO_KEEPALIVE_PID" 2>/dev/null' EXIT
else
  log "  (no sudo yet — the root steps 1-3/9 will prompt or log a failure)"
fi

log "=== 1/9  Samba (root pacman, wiped by update) ==="
if ! want INSTALL_SAMBA; then log "  not selected (INSTALL_SAMBA=0) — skip"
elif [ -x "$L/samba-setup.sh" ]; then sudo bash "$L/samba-setup.sh" || { log "  samba-setup.sh returned nonzero"; FAILED="$FAILED samba"; }
else log "  samba-setup.sh not found — skip"; fi

log "=== 2/9  Sinden system deps (mono/SDL, wiped) ==="
if ! want INSTALL_SINDEN; then log "  not selected (INSTALL_SINDEN=0) — skip"
else
  if [ -x "$L/sinden-reinstall-deps.sh" ]; then bash "$L/sinden-reinstall-deps.sh" || { log "  sinden-reinstall-deps.sh returned nonzero"; FAILED="$FAILED sinden-deps"; }
  else log "  sinden-reinstall-deps.sh not found — skip"; fi
  # Driver files live on /home (survive updates) — report only; the MAD Lightgun
  # page offers INSTALL DRIVER (sinden-install.sh) when they're missing.
  if [ -x "$L/sinden-install.sh" ]; then bash "$L/sinden-install.sh" --check | sed 's/^/  /'; fi
fi

log "=== 3/9  Lightgun udev rule (/etc reset) ==="
if ! want INSTALL_SINDEN; then log "  not selected (INSTALL_SINDEN=0) — skip"
else
  M="$L/sinden-shim/etc-backup/99-sinden-lightgun.rules"
  if [ -f "$M" ]; then
    sudo cp "$M" /etc/udev/rules.d/99-sinden-lightgun.rules \
      && sudo udevadm control --reload \
      && sudo udevadm trigger --subsystem-match=input \
      && log "  udev rule reinstalled + reloaded" || log "  udev reinstall failed (run manually)"
  else log "  udev mirror missing ($M) — skip"; fi
fi

log "=== 4/9  'input' group ==="
if groups | grep -qw input; then log "  already in 'input'"
else sudo usermod -aG input "$USER" && log "  added '$USER' to input (LOG OUT/IN to take effect)"; fi

log "=== 5/9  distrobox tooling ==="
if command -v distrobox >/dev/null 2>&1; then
  log "  distrobox present; containers: $(distrobox list 2>/dev/null | tail -n +2 | awk -F'|' '{print $2}' | xargs echo)"
else
  log "  distrobox MISSING (only needed to REBUILD ES-DE). Reinstall:"
  log "    curl -s https://raw.githubusercontent.com/89luca89/distrobox/main/install | sh -s -- --prefix ~/.local"
  log "    (your build containers in ~/.local/share/containers are still there.)"
fi

# rewrite_wrapper() is defined near the top of this script (so the `--wrapper` mode and
# install.sh can reuse the single source of truth). The "freshly downloaded" path below
# just calls it.

log "=== 6/9  MAD ES-DE build + wrapper (should be intact on /home) ==="
if [ -f "$HOME/Applications/ES-DE-MAD.AppImage" ]; then
  if grep -q 'ES-DE-MAD' "$HOME/Applications/ES-DE.AppImage" 2>/dev/null; then
    log "  MAD ES-DE intact (wrapper -> ES-DE-MAD.AppImage)"
  else
    # An EmuDeck / ES-DE *app* update overwrites ~/Applications/ES-DE.AppImage (our wrapper)
    # with a stock AppImage. The patched ES-DE-MAD.AppImage survives, so just RE-WRITE the
    # wrapper and ES-DE launches our build again (no rebuild needed).
    log "  wrapper missing/clobbered (EmuDeck/ES-DE update?) — re-writing it"
    rewrite_wrapper
  fi
else
  # The patched AppImage itself is gone (fresh/restored Deck, or it was deleted). Try the
  # fast CI-download recovery first (deck-fetch-esde.sh: pulls the GitHub Actions build);
  # only fall back to the slow local rebuild hint if that's unavailable.
  log "  MAD ES-DE BUILD missing — trying CI download (deck-fetch-esde.sh)"
  if [ -x "$L/deck-fetch-esde.sh" ] && bash "$L/deck-fetch-esde.sh"; then
    log "  installed CI-built ES-DE-MAD.AppImage — re-writing wrapper"
    rewrite_wrapper
  else
    log "  CI download unavailable — restore from a deck-backup, or rebuild via ~/esde-build (git checkout deck-patches + ubuntu-build.sh)"
  fi
fi

log "=== 7/9  MAD panel health (lives on /home) ==="
# The live MAD panel is the C++ GuiMadPanel compiled into the ES-DE fork (opened
# in-process from Main Menu → Utilities), backed by the mad-backend.py daemon — NOT
# the retired Tk router-config-gui.py. So health = the live lib/ modules + the
# backend selfcheck. The pacman deps below are evdev (the router reads controllers)
# and tk (the warning_dialog popups) — both wiped by a SteamOS update; they are
# SUPPORT deps, not what the panel itself needs.
if ! command -v python3 >/dev/null 2>&1; then
  log "  python3 MISSING — MAD can't run"
elif ! python3 -c 'import evdev, tkinter, yaml' 2>/dev/null; then
  # python-evdev = controller reading (router); tk = the Tk lib warning_dialog loads; python-yaml = RPCS3 input mapping.
  # These are pacman packages on the immutable root → wiped by a SteamOS update. Steps
  # 1-2 re-lock the read-only root and only init the keyring conditionally, so we
  # disable read-only + (re)init the keyring defensively here, then re-lock after the
  # install below. Mirrors install.sh:146-151.
  log "  python3 missing evdev/tkinter/yaml (pacman, wiped by update) — reinstalling python-evdev + tk + python-yaml"
  # shellcheck source=lib/pacman-helpers.sh
  . "$L/lib/pacman-helpers.sh"
  if mad_pacman_install python-evdev tk python-yaml; then
    python3 -c 'import evdev, tkinter, yaml' 2>/dev/null \
      && log "  reinstalled — evdev + tkinter + yaml import OK" \
      || log "  reinstalled but still failing — check pacman keyring / re-run this script"
  else
    log "  pacman reinstall FAILED (keyring/network?) — run manually: sudo pacman -S python-evdev tk python-yaml"
  fi
elif ! python3 -c "import sys; sys.path.insert(0, '$L'); from lib import localpolicy, es_systems, es_collections, policy, wii_slot_reader, routing, mad_config, mad_backup, standalone_preview" 2>/dev/null; then
  log "  live lib/ modules not importable — MAD will fail at runtime"
elif ! python3 "$L/mad-backend.py" --selfcheck >/dev/null 2>&1; then
  log "  mad-backend.py --selfcheck FAILED — the native ES-DE MAD panel's backend is broken"
else
  log "  MAD panel OK (python3 + evdev + tk + live lib/ + mad-backend selfcheck all present)"
fi

log "=== 8/9  Controller-router integration (lives on /home) ==="
for f in "$L/controller-router.py" \
         "$L/controller-router-wrap.sh" \
         "$L/controller-policy.toml" \
         "$L/mad-switch-launch.py" \
         "$L/mad-standalone-launch.py" \
         "$HOME/ES-DE/scripts/game-start/04-controller-router-setup.sh" \
         "$HOME/ES-DE/scripts/game-start/05-controller-router-standalone.sh" \
         "$HOME/ES-DE/scripts/game-end/00-controller-router.sh" \
         "$HOME/ES-DE/scripts/game-end/06-mad-switch-restore.sh"; do
  if [ -r "$f" ]; then log "  ok: $(basename "$f")"; else log "  MISSING/UNREADABLE: $(basename "$f")"; fi
done
# Re-wrap Switch(Ryujinx/Eden/Citron)/PS2/PS3/Xbox <command>s with MAD's launch binders AND
# re-dynamize the custom emulator paths to %EMULATOR_X% (idempotent). Survives SteamOS updates
# (es_systems lives on /home); here in case an EmuDeck re-setup regenerated es_systems.xml and
# dropped the wrapping. Extracted to lib/mad_launch_wrap.py (shared with install.sh).
python3 -c "import sys; sys.path.insert(0,'$L'); from lib import mad_launch_wrap; mad_launch_wrap.wrap_console_launchers()" 2>/dev/null \
  && log "  es_systems Switch/PS2/PS3/Xbox commands: wrapping + %EMULATOR_* ensured" \
  || log "  es_systems re-wrap skipped (file missing?)"
# Ensure the custom es_find_rules.xml carries MAD's dynamic emulator rules (additive; complements
# the bundled rules) so the %EMULATOR_* tokens above resolve. Shared with install.sh.
python3 -c "import sys; sys.path.insert(0,'$L'); from lib import es_find_rules; es_find_rules.ensure_find_rules()" 2>/dev/null \
  && log "  es_find_rules.xml: dynamic emulator resolution (Citron/Eden/Yuzu/Suyu/pcsx2x6) ensured" \
  || log "  es_find_rules ensure skipped (file missing?)"
# Carousel sort order (arcade clustered, Pew last): restore from the repo reference if a
# custom_systems rewrite dropped it (never clobber a live order the user may have re-tuned).
_sort_live="$HOME/ES-DE/custom_systems/es_systems_sorting.xml"; _sort_ref="$L/data/es_systems_sorting.reference.xml"
if [ ! -f "$_sort_live" ] && [ -f "$_sort_ref" ]; then
  cp "$_sort_ref" "$_sort_live" && log "  es_systems_sorting.xml (carousel order) restored from reference" \
    || log "  es_systems_sorting.xml restore failed"
fi

log "=== 9/9  Suspend mode: quirk-aware (deep unless the kernel truly allows s2idle) — /etc reset by update ==="
# Delegated to suspend-mode-setup.sh, which decides by the kernel's 'no s2idle allowed' quirk,
# NOT the DMI model: the quirk forbids s2idle on the LCD AND this OLED, so pin deep there;
# only leave s2idle on a kernel that genuinely supports it. (A prior 'OLED => s2idle' version
# re-broke suspend on this Deck every update.) Honors INSTALL_SUSPEND.
if [ -x "$L/suspend-mode-setup.sh" ]; then
  bash "$L/suspend-mode-setup.sh" || { log "  suspend-mode-setup.sh returned nonzero"; FAILED="$FAILED suspend"; }
else
  log "  suspend-mode-setup.sh not found — skip"
fi

# --- On-the-go: ensure the --user oneshot that sweeps an orphaned handheld TDP watt cap at every
#     session start. It lives on /home so a SteamOS update does NOT wipe it (unlike Samba/etc.);
#     this is a belt-and-suspenders re-assert for fresh/restored Decks + setups predating the
#     feature. See deck-docs/on-the-go.md. ---
_svc="$HOME/.config/systemd/user/mad-power-sweep.service"
mkdir -p "$HOME/.config/systemd/user"
cat > "$_svc" <<'SWEEP_EOF'
[Unit]
Description=MAD on-the-go: sweep an orphaned handheld TDP watt cap at session start
After=graphical-session.target
[Service]
Type=oneshot
ExecStart=/usr/bin/python3 %h/Emulation/tools/launchers/lib/deck_power.py sweep
[Install]
WantedBy=graphical-session.target
SWEEP_EOF
systemctl --user daemon-reload 2>/dev/null || true
if systemctl --user enable mad-power-sweep.service 2>/dev/null; then
  log "=== on-the-go: mad-power-sweep.service ensured (session-start orphan-cap sweep) ==="
else
  log "  mad-power-sweep.service written; will enable on next login (no user bus here)"
fi

# --- VNC: re-pin Desktop Mode to the X11 session. SteamOS updates reset the default desktop
#     session to Wayland (plasma.desktop), where x11vnc cannot capture the rootless-Xwayland
#     screen (black). Self-gating: only re-applied when this Deck actually runs the x11vnc bridge
#     (vnc-distrobox.service enabled) — never forces X11 on a Wayland user. Leaves login/boot mode
#     untouched (stays Game Mode). See deck-docs/vnc-remote-access.md. ---
if systemctl --user is-enabled vnc-distrobox.service >/dev/null 2>&1 \
   && command -v steamosctl >/dev/null 2>&1; then
  log "=== VNC: re-pin Desktop Mode to X11 (vnc-distrobox.service enabled) ==="
  _cur="$(steamosctl get-default-desktop-session 2>/dev/null)"
  if [ "$_cur" = "plasmax11.desktop" ]; then
    log "  already pinned to plasmax11.desktop — ok"
  else
    steamosctl set-default-desktop-session plasmax11.desktop 2>/dev/null \
      && log "  default desktop session re-pinned: ${_cur:-?} -> plasmax11.desktop" \
      || { log "  steamosctl set-default-desktop-session FAILED"; FAILED="$FAILED vnc-x11-pin"; }
  fi
fi

echo
if [ -n "${FAILED:-}" ]; then
  log "!! Some steps FAILED:${FAILED} — re-run this script (usually a transient network/keyring issue)."
fi
log "=== done. /home data (ES-DE config, ROMs, themes, collections) was untouched by the update. ==="
log "Reboot or log out/in so the 'input' group + udev changes take full effect."

# Record the current SteamOS BUILD_ID so esde-health-check.sh stops nagging as soon as a
# full restore succeeds — otherwise it only writes the marker on a later all-present launch.
# Only on success (check_missing passes), so a still-broken restore keeps nagging.
if check_missing >/dev/null 2>&1; then
  _bid="$(grep -m1 '^BUILD_ID=' /etc/os-release 2>/dev/null | cut -d= -f2 | tr -d '"')"
  [ -n "$_bid" ] && printf '%s\n' "$_bid" > "$L/.last-os-build" 2>/dev/null || true
fi
