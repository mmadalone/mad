# shellcheck shell=bash
# lib/pacman-helpers.sh — source me. ONE correct pacman install for SteamOS's
# immutable root, replacing the readonly+keyring dance that was copy-pasted (and had
# drifted) across install.sh, deck-post-update.sh, samba-setup.sh, sinden-reinstall-deps.sh.
#
#   mad_pacman_install [--refresh] PKG...
#
# Does, in a SUBSHELL so the EXIT trap is scoped + ALWAYS fires:
#   1. steamos-readonly disable        (the immutable /usr must be writable for pacman)
#   2. init + populate the pacman keyring IF empty — BOTH `archlinux` and `holo` (packages
#      come from both repos; populating only one fails to verify the other's signatures —
#      this was sinden-reinstall-deps.sh's latent bug)
#   3. pacman -S (or -Sy with --refresh) --needed --noconfirm PKG...
#   4. steamos-readonly enable          (via the EXIT trap — so a pacman FAILURE can never
#                                        leave /usr writable, the bug in the old set -e path)
# Returns pacman's exit status. `sudo` is used for each privileged step (a no-op when the
# caller is already root, e.g. samba-setup.sh). NOTE: writes to /etc (udev rules, smb.conf,
# tmpfiles) do NOT need this — /etc is a writable overlay; only pacman needs the unlock.

mad_pacman_install() {
  local refresh=0
  [ "${1:-}" = "--refresh" ] && { refresh=1; shift; }
  [ "$#" -gt 0 ] || return 0
  (
    set -e
    trap 'sudo steamos-readonly enable 2>/dev/null || true' EXIT
    sudo steamos-readonly disable 2>/dev/null || true
    # Keyring: only (re)init when empty — idempotent + avoids a slow re-populate every call.
    if [ "$(sudo pacman-key --list-keys 2>/dev/null | grep -c '^pub')" = 0 ]; then
      sudo pacman-key --init
      # holo may be unavailable on some images — fall back to archlinux-only (samba-setup's pattern).
      sudo pacman-key --populate archlinux holo 2>/dev/null || sudo pacman-key --populate archlinux
    fi
    if [ "$refresh" = 1 ]; then
      sudo pacman -Sy --needed --noconfirm "$@"
    else
      sudo pacman -S  --needed --noconfirm "$@"
    fi
  )
}
