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
  return "$miss"
}

if [ "${1:-}" = "--check" ]; then
  check_missing; exit $?
fi

log "=== 1/8  Samba (root pacman, wiped by update) ==="
if [ -x "$T/samba-setup.sh" ]; then bash "$T/samba-setup.sh" || log "  samba-setup.sh returned nonzero"
else log "  samba-setup.sh not found — skip"; fi

log "=== 2/8  Sinden system deps (mono/SDL, wiped) ==="
if [ -x "$L/sinden-reinstall-deps.sh" ]; then bash "$L/sinden-reinstall-deps.sh" || log "  sinden-reinstall-deps.sh returned nonzero"
else log "  sinden-reinstall-deps.sh not found — skip"; fi

log "=== 3/8  Lightgun udev rule (/etc reset) ==="
M="$T/sinden-shim/etc-backup/99-sinden-lightgun.rules"
if [ -f "$M" ]; then
  sudo cp "$M" /etc/udev/rules.d/99-sinden-lightgun.rules \
    && sudo udevadm control --reload \
    && sudo udevadm trigger --subsystem-match=input \
    && log "  udev rule reinstalled + reloaded" || log "  udev reinstall failed (run manually)"
else log "  udev mirror missing ($M) — skip"; fi

log "=== 4/8  'input' group ==="
if groups | grep -qw input; then log "  already in 'input'"
else sudo usermod -aG input deck && log "  added 'deck' to input (LOG OUT/IN to take effect)"; fi

log "=== 5/8  distrobox tooling ==="
if command -v distrobox >/dev/null 2>&1; then
  log "  distrobox present; containers: $(distrobox list 2>/dev/null | tail -n +2 | awk -F'|' '{print $2}' | xargs echo)"
else
  log "  distrobox MISSING (only needed to REBUILD ES-DE). Reinstall:"
  log "    curl -s https://raw.githubusercontent.com/89luca89/distrobox/main/install | sh -s -- --prefix ~/.local"
  log "    (your build containers in ~/.local/share/containers are still there.)"
fi

log "=== 6/8  MAD ES-DE build + wrapper (should be intact on /home) ==="
if [ -f "$HOME/Applications/ES-DE-MAD.AppImage" ]; then
  if grep -q 'ES-DE-MAD' "$HOME/Applications/ES-DE.AppImage" 2>/dev/null; then
    log "  MAD ES-DE intact (wrapper -> ES-DE-MAD.AppImage)"
  else
    # An EmuDeck / ES-DE *app* update overwrites ~/Applications/ES-DE.AppImage (our wrapper)
    # with a stock AppImage. The patched ES-DE-MAD.AppImage survives, so just RE-WRITE the
    # wrapper and ES-DE launches our build again (no rebuild needed).
    log "  wrapper missing/clobbered (EmuDeck/ES-DE update?) — re-writing it"
    [ -s "$HOME/Applications/ES-DE.AppImage" ] \
      && cp -f "$HOME/Applications/ES-DE.AppImage" "$HOME/Applications/ES-DE.AppImage.real" 2>/dev/null \
      && log "    kept the current stock AppImage as ES-DE.AppImage.real (emergency fallback)"
    cat > "$HOME/Applications/ES-DE.AppImage" <<'WRAP'
#!/usr/bin/env bash
# Runs our MAD ES-DE build (patched 3.4.1, source-built): full-screen splash baked
# into Window.cpp, launched-from collection passed to game-start as $5, and the native
# "MAD CONTROL PANEL" menu row. Regenerate the (random) splash image first, then exec
# the MAD AppImage. Emergency fallback: the stock AppImage is kept as ES-DE.AppImage.real.
[ -x "$HOME/Emulation/tools/launchers/esde-splash-gen.sh" ] && \
  "$HOME/Emulation/tools/launchers/esde-splash-gen.sh" 2>/dev/null || true
# Fall back to the stock AppImage if the MAD build is missing, so ES-DE always launches.
TARGET="$HOME/Applications/ES-DE-MAD.AppImage"
[ -x "$TARGET" ] || TARGET="$HOME/Applications/ES-DE.AppImage.real"
exec "$TARGET" "$@"
WRAP
    chmod +x "$HOME/Applications/ES-DE.AppImage"
    log "    wrapper re-written → ES-DE-MAD.AppImage"
  fi
else
  log "  MAD ES-DE BUILD missing — restore from a deck-backup, or rebuild via ~/esde-build (git checkout deck-patches + ubuntu-build.sh)"
fi

log "=== 7/8  MAD GUI launchability (lives on /home) ==="
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

log "=== 8/8  Controller-router integration (lives on /home) ==="
for f in "$L/controller-router.py" \
         "$L/controller-router-wrap.sh" \
         "$L/controller-policy.toml" \
         "$HOME/ES-DE/scripts/game-start/04-controller-router-setup.sh" \
         "$HOME/ES-DE/scripts/game-start/05-controller-router-standalone.sh" \
         "$HOME/ES-DE/scripts/game-end/00-controller-router.sh"; do
  if [ -r "$f" ]; then log "  ok: $(basename "$f")"; else log "  MISSING/UNREADABLE: $(basename "$f")"; fi
done

echo
log "=== done. /home data (ES-DE config, ROMs, themes, collections) was untouched by the update. ==="
log "Reboot or log out/in so the 'input' group + udev changes take full effect."
