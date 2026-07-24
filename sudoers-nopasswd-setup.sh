#!/usr/bin/env bash
# sudoers-nopasswd-setup.sh - OPT-IN passwordless sudo for the deck user.
#
# WHY opt-in + default OFF: passwordless sudo lets ANY process running as this user become root with
# NO authentication - a real widening of attack surface on a Deck that runs a lot of third-party
# emulator/game code. Only enable it if you accept that. It is gated on INSTALL_NOPASSWD (explicitly
# 1/on/yes/true in install.conf) - never enabled by default, never by the "no install.conf =
# do-everything" fallback.
#
# WHY re-applied by deck-post-update.sh: a SteamOS update RESETS /etc (incl. /etc/sudoers.d), so this
# grant is wiped by every update. deck-post-update.sh re-applies it (after you enter the password
# once for that run) when INSTALL_NOPASSWD is on.
#
# SAFETY: the drop-in is generated to a temp file and VALIDATED with `visudo -cf` before install - a
# malformed sudoers file can lock you out of sudo, so we never install one that fails validation.
#
# Usage (run as root):  sudo bash sudoers-nopasswd-setup.sh          # enable
#                       sudo bash sudoers-nopasswd-setup.sh --revoke # disable (remove the drop-in)
set -uo pipefail

DROPIN="${MAD_NOPASSWD_DROPIN:-/etc/sudoers.d/zzz-mad-nopasswd}"   # 'zzz-' loads last; no '.' so sudo does not ignore it (override=tests)
USER_NAME="${SUDO_USER:-${USER:-deck}}"
log(){ printf '[nopasswd] %s\n' "$*"; }

if [ "$(id -u)" != "0" ]; then
  log "must run as root (use: sudo bash $0)"; exit 1
fi

if [ "${1:-}" = "--revoke" ]; then
  if [ -f "$DROPIN" ]; then rm -f "$DROPIN" && log "passwordless sudo REVOKED ($DROPIN removed)"
  else log "already off (no $DROPIN)"; fi
  exit 0
fi

TMP="$(mktemp)" || { log "mktemp failed"; exit 1; }
trap 'rm -f "$TMP"' EXIT
printf '# Managed by MAD (INSTALL_NOPASSWD). Passwordless sudo for %s.\n# Remove this file (or run sudoers-nopasswd-setup.sh --revoke) to revoke.\n%s ALL=(ALL) NOPASSWD: ALL\n' \
  "$USER_NAME" "$USER_NAME" > "$TMP"

# NEVER install a sudoers file that fails validation - it could lock the user out of sudo.
if ! visudo -cf "$TMP" >/dev/null 2>&1; then
  log "REFUSED: the generated sudoers entry failed 'visudo -c' validation - NOT installed"; exit 1
fi
if install -o root -g root -m 0440 "$TMP" "$DROPIN"; then
  log "passwordless sudo ENABLED for $USER_NAME ($DROPIN). Any $USER_NAME process is now root without a password."
  log "  (a SteamOS update wipes this; deck-post-update.sh re-applies it while INSTALL_NOPASSWD is on. --revoke to disable.)"
else
  log "install to $DROPIN FAILED"; exit 1
fi
