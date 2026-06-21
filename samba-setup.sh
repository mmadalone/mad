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

echo "==> 1/4 Install samba (root-unlock + keyring + re-lock handled by mad_pacman_install)"
# shellcheck source=lib/pacman-helpers.sh
. "$(dirname "$0")/lib/pacman-helpers.sh"
mad_pacman_install --refresh samba

echo "==> 2/4 Install smb.conf"
if [[ -f "$CONF_SRC" ]]; then
  install -Dm644 "$CONF_SRC" /etc/samba/smb.conf
  echo "    installed from $CONF_SRC"
else
  echo "    WARNING: $CONF_SRC missing — leaving existing /etc/samba/smb.conf"
fi
testparm -s >/dev/null 2>&1 && echo "    smb.conf syntax OK" || echo "    WARNING: testparm reported issues"

echo "==> 3/4 Enable + start services"
# Arch unit names are smb/nmb; fall back to smbd/nmbd just in case.
systemctl enable --now smb nmb 2>/dev/null || systemctl enable --now smbd nmbd

echo "==> 4/4 Samba password for '${DECK_USER}'"
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
