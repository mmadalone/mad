#!/usr/bin/env bash
# Reinstall Sinden Lightgun runtime dependencies after a SteamOS update wipes them.
# Idempotent: safe to re-run.
#
# What a SteamOS update wipes vs. preserves:
#   - WIPED:     /usr (immutable image swap) — all pacman-installed packages
#   - PRESERVED: /home, /var — but /etc/udev/rules.d/ has been known to get clobbered
#                              on some updates, so we keep a master copy in /home
#                              and restore from it if missing.
set -uo pipefail

UDEV_RULE=/etc/udev/rules.d/99-sinden-lightgun.rules
UDEV_BACKUP="$HOME/Emulation/tools/launchers/sinden-shim/etc-backup/99-sinden-lightgun.rules"
SHIM_SRC="$HOME/Emulation/tools/launchers/sinden-shim/sinden-smooth.c"
SHIM_SO="$HOME/Emulation/tools/launchers/sinden-shim/sinden-smooth.so"
LIGHTGUN_DIR="$HOME/Lightgun"

# --- pacman packages (wiped on SteamOS update) ---
# mad_pacman_install does the readonly unlock + keyring (archlinux holo, fixing the old
# holo-only bug) + pacman + a trap that ALWAYS re-locks the root (fixing the old `set -e`
# path that left /usr writable on a pacman failure). runtime: mono drives Sinden's .NET
# binary; sdl12-compat/sdl_image/sdl for libSdlInterface.so; v4l-utils for camera probing.
# build: gcc + glibc headers to recompile the LD_PRELOAD smoothing shim.
echo "==> installing runtime + build deps"
# shellcheck source=lib/pacman-helpers.sh
. "$(dirname "$0")/lib/pacman-helpers.sh"
# Capture the install result so callers (deck-post-update.sh / install.sh) can detect a
# real failure — the rest of the script (udev/shim/group, idempotent) still runs, then we
# exit with this code. (Don't re-add top-level `set -e`: its removal is what lets
# mad_pacman_install's subshell EXIT-trap always re-lock the immutable root.)
DEPS_RC=0
mad_pacman_install mono sdl12-compat sdl_image sdl v4l-utils gcc glibc linux-api-headers xorg-xinput || DEPS_RC=$?

# --- udev rule (may get clobbered by SteamOS update) ---
if [[ ! -f $UDEV_RULE ]]; then
    if [[ -f $UDEV_BACKUP ]]; then
        echo "==> restoring udev rule from backup"
        sudo cp "$UDEV_BACKUP" "$UDEV_RULE"
        sudo chmod 644 "$UDEV_RULE"
        sudo udevadm control --reload-rules
        sudo udevadm trigger --subsystem-match=input --subsystem-match=video4linux
    else
        echo "!! WARN: udev rule missing and no backup at $UDEV_BACKUP — guns won't get stable symlinks" >&2
    fi
else
    # Master copy exists — refresh the persistent backup so future restores are current
    if ! diff -q "$UDEV_RULE" "$UDEV_BACKUP" >/dev/null 2>&1; then
        echo "==> refreshing udev rule backup in $UDEV_BACKUP"
        mkdir -p "$(dirname "$UDEV_BACKUP")"
        cp "$UDEV_RULE" "$UDEV_BACKUP"
    fi
fi

# --- sanity: Sinden driver binaries present? ---
if [[ ! -f "$LIGHTGUN_DIR/LightgunMono.exe" ]]; then
    echo "!! WARN: $LIGHTGUN_DIR/LightgunMono.exe missing — reinstall the Sinden driver" >&2
elif [[ ! -f "$LIGHTGUN_DIR/libSdlInterface.so" || ! -f "$LIGHTGUN_DIR/libCameraInterface.so" ]]; then
    echo "!! WARN: $LIGHTGUN_DIR/lib*.so missing — Sinden driver install is incomplete" >&2
fi

# --- LD_PRELOAD smoothing shim (rebuilt only if missing or stale) ---
if [[ -f $SHIM_SRC && (! -f $SHIM_SO || $SHIM_SRC -nt $SHIM_SO) ]]; then
    echo "==> rebuilding sinden-smooth.so"
    gcc -shared -fPIC -O2 -o "$SHIM_SO" "$SHIM_SRC" -ldl
fi

# --- group membership sanity (uaccess tag covers it, but input is belt+braces) ---
if ! id -nG "$USER" | grep -qw input; then
    echo "==> adding $USER to input group (uaccess tag is primary; this is belt+braces)"
    sudo usermod -aG input "$USER"
    echo "   (you'll need to log out & back in for new group to take effect)"
fi

echo "==> done"
exit "${DEPS_RC:-0}"
