#!/usr/bin/env bash
# Native Samba on SteamOS — durable, idempotent installer.
#
# SteamOS has an immutable A/B root: every OS update REPLACES it, wiping
# pacman-installed packages and /etc changes. So run this:
#   * once now, and
#   * again after every SteamOS update (it's safe to re-run anytime).
#
#   sudo bash ~/Emulation/tools/launchers/samba-setup.sh
#
# The canonical share config lives at ~/Emulation/tools/smb.conf (on /home,
# which DOES survive updates) and is copied into place here. Edit that file to
# change shares, then re-run.
set -uo pipefail

[[ $EUID -eq 0 ]] || { echo "Run with sudo: sudo bash $0"; exit 1; }

DECK_USER="deck"
CONF_SRC="/home/${DECK_USER}/Emulation/tools/smb.conf"

echo "==> 1/6 Disable read-only root"
steamos-readonly disable || true

echo "==> 2/6 Initialise pacman keyring (only if empty)"
if [[ "$(pacman-key --list-keys 2>/dev/null | grep -c '^pub')" -eq 0 ]]; then
  pacman-key --init
  pacman-key --populate archlinux holo 2>/dev/null || pacman-key --populate archlinux
else
  echo "    keyring already populated"
fi

echo "==> 3/6 Install samba"
pacman -Sy --noconfirm --needed samba

echo "==> 4/6 Install smb.conf"
if [[ -f "$CONF_SRC" ]]; then
  install -Dm644 "$CONF_SRC" /etc/samba/smb.conf
  echo "    installed from $CONF_SRC"
else
  echo "    WARNING: $CONF_SRC missing — leaving existing /etc/samba/smb.conf"
fi
testparm -s >/dev/null 2>&1 && echo "    smb.conf syntax OK" || echo "    WARNING: testparm reported issues"

echo "==> 5/6 Enable + start services"
# Arch unit names are smb/nmb; fall back to smbd/nmbd just in case.
systemctl enable --now smb nmb 2>/dev/null || systemctl enable --now smbd nmbd

echo "==> 6/6 Samba password for '${DECK_USER}'"
if pdbedit -L 2>/dev/null | grep -q "^${DECK_USER}:"; then
  echo "    '${DECK_USER}' already has an SMB password (kept)"
else
  echo "    Set the password you'll use from your other machine:"
  smbpasswd -a "${DECK_USER}"
fi

IP="$(ip -4 route get 1.1.1.1 2>/dev/null | grep -oE 'src [0-9.]+' | awk '{print $2}')"
echo
echo "Done. Samba is running and will serve in Game Mode or Desktop."
echo "Connect from another machine to:   \\\\${IP:-<deck-ip>}\\deck-home   or   \\\\${IP:-<deck-ip>}\\sdcard"
echo "(user: ${DECK_USER}, the SMB password you just set)"
echo "Re-run this script after any SteamOS update."

# Re-lock the immutable root we disabled in step 1/6. SteamOS expects the A/B root
# read-only; leaving it writable until the next reboot is wrong end-state. Matches
# install.sh:151 / sinden-reinstall-deps.sh:80 (unconditional re-enable, non-fatal off-SteamOS).
echo "==> Re-locking read-only root"
steamos-readonly enable || true
