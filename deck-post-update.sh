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
#   7. MAD GUI launchability         (python3+tkinter+evdev, lib/, router-config-gui.py)
#   8. controller-router integration (router scripts + ES-DE game-start/end hooks)
#   9. Suspend mode deep/S3 (mem_sleep)  (/etc reset; LCD Deck kernel forbids s2idle)
#
# Safe to re-run. Needs sudo for the root bits (run from a Desktop-mode terminal).
# (NOTE: an EmuDeck/ES-DE *app* update is separate — that overwrites
#  ~/Applications/ES-DE.AppImage with stock; see esde-patched-build memory to rebuild.)
# ============================================================================
set -uo pipefail
T="$HOME/Emulation/tools"; L="$T/launchers"
log(){ echo "[post-update] $*"; }

# --- read-only HEALTH CHECK (no sudo, no restore) — used by esde-health-check.sh at
#     ES-DE launch to detect what a SteamOS update wiped. Prints each MISSING component
#     (one per line) to stdout; exit 0 = all present, 1 = something missing. ---
check_missing(){
  local miss=0; _gone(){ echo "$1"; miss=1; }
  { [ -f "$HOME/Applications/ES-DE-MAD.AppImage" ] \
      && grep -q 'ES-DE-MAD' "$HOME/Applications/ES-DE.AppImage" 2>/dev/null; } \
      || _gone "Patched ES-DE (MAD) build"
  python3 -c 'import tkinter, evdev' 2>/dev/null || _gone "MAD GUI deps (python3 tkinter/evdev)"
  local crmiss=0
  for f in "$L/controller-router.py" "$L/controller-router-wrap.sh" "$L/controller-policy.toml" \
           "$HOME/ES-DE/scripts/game-start/04-controller-router-setup.sh" \
           "$HOME/ES-DE/scripts/game-start/05-controller-router-standalone.sh" \
           "$HOME/ES-DE/scripts/game-end/00-controller-router.sh"; do
    [ -r "$f" ] || crmiss=1
  done
  [ "$crmiss" -eq 0 ] || _gone "Controller routing scripts/hooks"
  groups | grep -qw input || _gone "'input' group membership (controllers)"
  command -v mono >/dev/null 2>&1 || _gone "Sinden lightgun deps (mono/SDL)"
  [ -f /etc/udev/rules.d/99-sinden-lightgun.rules ] || _gone "Sinden lightgun udev rule"
  command -v smbd >/dev/null 2>&1 || _gone "Samba file sharing"
  [ -f /etc/tmpfiles.d/99-mem_sleep.conf ] || _gone "Suspend mode deep/S3 (mem_sleep tmpfiles)"
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
APPDIR="$HOME/Applications/ES-DE-MAD.AppDir"
STAMP="$APPDIR/.src-stamp"
WANT="$(stat -c '%Y:%s' "$IMG" 2>/dev/null)"
# (Re)extract if the AppDir is missing or the source AppImage changed.
if [ ! -x "$APPDIR/AppRun" ] || [ "$(cat "$STAMP" 2>/dev/null)" != "$WANT" ]; then
  TMP="$HOME/Applications/.esde-extract-tmp"
  rm -rf "$APPDIR" "$TMP"
  if mkdir -p "$TMP" && ( cd "$TMP" && "$IMG" --appimage-extract >/dev/null 2>&1 ); then
    # --appimage-extract emits squashfs-root (a symlink to ./AppDir on the ES-DE build);
    # move the REAL directory, not the symlink.
    SRC="$TMP/squashfs-root"
    [ -L "$SRC" ] && SRC="$TMP/$(readlink "$SRC" | sed 's#^\./##')"
    if mv "$SRC" "$APPDIR"; then echo "$WANT" > "$STAMP"; fi
  fi
  rm -rf "$TMP"
  # If extraction failed, run the AppImage directly so ES-DE always launches.
  [ -x "$APPDIR/AppRun" ] || exec "$IMG" "$@"
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

# --wrapper : just (re)write the launch wrapper, nothing else. Used by install.sh right
# after a fresh deck-fetch-esde.sh (the AppImage exists but the wrapper doesn't yet).
if [ "${1:-}" = "--wrapper" ]; then
  rewrite_wrapper; exit $?
fi

log "=== 1/9  Samba (root pacman, wiped by update) ==="
if [ -x "$T/samba-setup.sh" ]; then bash "$T/samba-setup.sh" || log "  samba-setup.sh returned nonzero"
else log "  samba-setup.sh not found — skip"; fi

log "=== 2/9  Sinden system deps (mono/SDL, wiped) ==="
if [ -x "$L/sinden-reinstall-deps.sh" ]; then bash "$L/sinden-reinstall-deps.sh" || log "  sinden-reinstall-deps.sh returned nonzero"
else log "  sinden-reinstall-deps.sh not found — skip"; fi

log "=== 3/9  Lightgun udev rule (/etc reset) ==="
M="$T/sinden-shim/etc-backup/99-sinden-lightgun.rules"
if [ -f "$M" ]; then
  sudo cp "$M" /etc/udev/rules.d/99-sinden-lightgun.rules \
    && sudo udevadm control --reload \
    && sudo udevadm trigger --subsystem-match=input \
    && log "  udev rule reinstalled + reloaded" || log "  udev reinstall failed (run manually)"
else log "  udev mirror missing ($M) — skip"; fi

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

log "=== 7/9  MAD GUI launchability (lives on /home) ==="
GUI="$L/router-config-gui.py"
if [ ! -r "$GUI" ]; then
  log "  router-config-gui.py MISSING/UNREADABLE — MAD.sh won't launch"
elif ! command -v python3 >/dev/null 2>&1; then
  log "  python3 MISSING — MAD.sh won't launch"
elif ! python3 -c 'import tkinter, evdev' 2>/dev/null; then
  # These are pacman packages on the immutable root → wiped by a SteamOS update.
  # python-evdev = evdev bindings; tk = the Tk lib tkinter loads (the _tkinter C
  # module ships with `python` itself). Keyring is already inited by steps 1–2.
  log "  python3 missing tkinter/evdev (pacman, wiped by update) — reinstalling python-evdev + tk"
  if sudo pacman -S --needed --noconfirm python-evdev tk; then
    python3 -c 'import tkinter, evdev' 2>/dev/null \
      && log "  reinstalled — tkinter + evdev import OK" \
      || log "  reinstalled but still failing — check pacman keyring / re-run this script"
  else
    log "  pacman reinstall FAILED (keyring/network?) — run manually: sudo pacman -S python-evdev tk"
  fi
elif ! python3 -c "import sys; sys.path.insert(0, '$L'); from lib import localpolicy, es_systems, gui_theme, es_collections" 2>/dev/null; then
  log "  lib/ modules not importable — MAD.sh will fail at runtime"
else
  log "  MAD GUI OK (python3 + tkinter + evdev + lib/ all present)"
fi

log "=== 8/9  Controller-router integration (lives on /home) ==="
for f in "$L/controller-router.py" \
         "$L/controller-router-wrap.sh" \
         "$L/controller-policy.toml" \
         "$HOME/ES-DE/scripts/game-start/04-controller-router-setup.sh" \
         "$HOME/ES-DE/scripts/game-start/05-controller-router-standalone.sh" \
         "$HOME/ES-DE/scripts/game-end/00-controller-router.sh"; do
  if [ -r "$f" ]; then log "  ok: $(basename "$f")"; else log "  MISSING/UNREADABLE: $(basename "$f")"; fi
done

log "=== 9/9  Suspend mode: pin deep/S3 (mem_sleep) — /etc reset by update ==="
# This is an LCD Steam Deck. Its neptune kernel carries a DMI quirk
# ("PM: Steam Deck quirk - no s2idle allowed!") and the firmware advertises only
# S0 S3 S4 S5 — i.e. s2idle is NOT supported on this model (that's the OLED's mode).
# If mem_sleep is forced to s2idle, every power-button press enters suspend, hits
# "s2idle sleep is not supported", and exits instantly (screen never sleeps). So we
# pin the only working mode, deep/S3. (deep is already the firmware default, so this
# is mostly a guard + documents intent.) /etc is wiped by an update, so (re)create now.
if printf 'w /sys/power/mem_sleep - - - - deep\n' \
     | sudo tee /etc/tmpfiles.d/99-mem_sleep.conf >/dev/null \
   && sudo systemd-tmpfiles --create /etc/tmpfiles.d/99-mem_sleep.conf 2>/dev/null; then
  log "  mem_sleep tmpfiles installed; active now: $(cat /sys/power/mem_sleep)"
else
  log "  FAILED to install mem_sleep tmpfiles — apply manually: echo deep | sudo tee /sys/power/mem_sleep"
fi

echo
log "=== done. /home data (ES-DE config, ROMs, themes, collections) was untouched by the update. ==="
log "Reboot or log out/in so the 'input' group + udev changes take full effect."
