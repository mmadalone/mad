# shellcheck shell=bash
# lib/install-conf.sh — source me. The single source of truth for install choices.
#
# Loads $MAD_DIR/install.conf (plain shell key=value) if present, then exposes want():
#   want KEY    -> exit 0 (do it) / 1 (skip it)
# A value of 1|on|yes|true|auto means "do it". If install.conf is ABSENT, want() ALWAYS
# returns 0 (the legacy "do everything"). So the file only ever NARROWS what install.sh /
# deck-post-update.sh run; it never adds steps.
#
# No-op-safe to re-source. $MAD_INSTALL_CONF overrides the path (tests).

: "${MAD_INSTALL_CONF:=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd)/install.conf}"
HAVE_INSTALL_CONF=0
if [ -f "$MAD_INSTALL_CONF" ]; then
  # shellcheck disable=SC1090
  . "$MAD_INSTALL_CONF" && HAVE_INSTALL_CONF=1
fi
export HAVE_INSTALL_CONF MAD_INSTALL_CONF

want() {
  [ "$HAVE_INSTALL_CONF" = 1 ] || return 0          # no install.conf => do everything
  local v="${!1:-}"
  case "${v,,}" in 1|on|yes|true|auto) return 0 ;; *) return 1 ;; esac
}
