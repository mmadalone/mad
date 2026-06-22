#!/usr/bin/env bash
# suspend-mode-setup.sh — pin the CORRECT suspend mode for THIS Steam Deck.
#
# A SteamOS update wipes /etc, so deck-post-update.sh re-runs this to re-pin the suspend mode.
#
# EVERY current Steam Deck (LCD Jupiter + OLED Galileo) carries a kernel DMI quirk
# ("PM: Steam Deck quirk - no s2idle allowed!") that forbids s2idle — so deep/S3 is the ONLY mode
# that actually sleeps; under s2idle the power button dims then immediately wakes ("sleeps but
# comes back up"). DECIDE BY DMI: if this is a Steam Deck => pin deep, period.
# Do NOT rely on the quirk STRING in the boot log: it ages out of `journalctl -kb` on the 4 MB
# kernel ring buffer within ~an hour of the SAME boot (verified 2026-06-22), so "string absent" is
# NOT proof s2idle works — a journal-string-only version flipped to s2idle and would have re-broken
# suspend on this Deck (and the earlier "OLED => s2idle" model version was wrong too).
# See deck-docs/power-suspend.md.
#   * Steam Deck (or s2idle otherwise unproven / unverifiable) => PIN deep (always safe).
#   * a NON-Deck kernel that genuinely lists s2idle and logs no quirk => leave s2idle, remove pin.
#
# Honors install.conf INSTALL_SUSPEND:  auto (default = quirk-aware) | on (force deep) |
# off (don't touch suspend).  $MAD_S2IDLE_OK=1|0 (decision) + $MAD_DMI_PRODUCT (model) override
# detection for tests.
#
#   suspend-mode-setup.sh           apply (needs sudo for /etc + sysfs)
#   suspend-mode-setup.sh --check   report only, NO sudo: exit 0 = already correct, 1 = needs apply
set -uo pipefail

PIN="${MAD_SUSPEND_PIN:-/etc/tmpfiles.d/99-mem_sleep.conf}"   # override for tests
HERE="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
# Pull INSTALL_SUSPEND from install.conf if present (sets the var; unset => default auto).
# shellcheck source=/dev/null
[ -f "$HERE/lib/install-conf.sh" ] && . "$HERE/lib/install-conf.sh"

# DMI model — for LOGGING context only, never for the decision.
detect_model() {
  local s; s="$(cat /sys/class/dmi/id/product_name /sys/class/dmi/id/board_name 2>/dev/null)"
  case "$s" in *Galileo*) echo OLED ;; *Jupiter*) echo LCD ;; *) echo unknown ;; esac
}

# Is this a Steam Deck? DMI is reliable + always present (unlike the boot-log quirk string).
# $MAD_DMI_PRODUCT overrides for tests.
is_steam_deck() {
  local s="${MAD_DMI_PRODUCT:-}"
  [ -n "$s" ] || s="$(cat /sys/class/dmi/id/product_name /sys/class/dmi/id/board_name \
                          /sys/class/dmi/id/sys_vendor 2>/dev/null)"
  case "$s" in *Galileo*|*Jupiter*|*Valve*) return 0 ;; esac
  return 1
}

# True only if the kernel ACTUALLY allows s2idle (so it would really sleep). A Steam Deck NEVER
# does (the quirk) — decided by reliable DMI, not the transient journal string. Non-Deck: trust
# the kernel (lists s2idle AND logs no quirk); unverifiable => deep (always safe).
s2idle_supported() {
  case "${MAD_S2IDLE_OK:-}" in 1|on|yes|true) return 0 ;; 0|off|no|false) return 1 ;; esac
  is_steam_deck && return 1     # every current Steam Deck forbids s2idle -> deep
  grep -qw s2idle "${MAD_MEM_SLEEP_FILE:-/sys/power/mem_sleep}" 2>/dev/null || return 1  # not listed
  local klog; klog="$(journalctl -kb 2>/dev/null)"
  [ -n "$klog" ] || return 1                              # can't verify -> safe default: deep
  case "$klog" in *"no s2idle allowed"*) return 1 ;; esac  # the quirk (non-Deck) -> blocked
  return 0
}

# Echo the desired end-state: deep | none | skip.
desired_mode() {
  local pref="${INSTALL_SUSPEND:-auto}"
  case "${pref,,}" in
    off) echo skip; return ;;
    on)  echo deep; return ;;
  esac
  if s2idle_supported; then echo none; else echo deep; fi   # auto: deep unless s2idle truly works
}

main() {
  local mode; mode="$(desired_mode)"
  local model; model="$(detect_model)"

  if [ "${1:-}" = "--check" ]; then
    case "$mode" in
      skip) exit 0 ;;
      deep) [ -f "$PIN" ] && exit 0 || { echo "Suspend mode deep/S3 (mem_sleep) — this kernel forbids s2idle"; exit 1; } ;;
      none) [ -f "$PIN" ] && { echo "Stale mem_sleep=deep pin — this kernel actually supports s2idle"; exit 1; } || exit 0 ;;
    esac
  fi

  case "$mode" in
    skip)
      echo "[suspend] INSTALL_SUSPEND=off — leaving suspend untouched"; exit 0 ;;
    deep)
      echo "[suspend] s2idle unavailable on this kernel (Steam Deck quirk; model=$model) -> pin deep/S3"
      if printf 'w /sys/power/mem_sleep - - - - deep\n' | sudo tee "$PIN" >/dev/null \
         && sudo systemd-tmpfiles --create "$PIN" 2>/dev/null; then
        echo "[suspend] pinned; active now: $(cat /sys/power/mem_sleep 2>/dev/null)"
      else
        echo "[suspend] FAILED to pin — apply manually: echo deep | sudo tee /sys/power/mem_sleep"
      fi ;;
    none)
      echo "[suspend] s2idle supported (modern standby; model=$model) -> no deep pin needed"
      if [ -f "$PIN" ]; then
        local tmp="$HOME/Downloads/_TMP-suspend-fix-$(date +%Y%m%d-%H%M%S)"
        # Write the RECOVERY note only AFTER a successful move, so a failed mv doesn't orphan
        # a _TMP dir pointing at a file that was never moved there.
        if mkdir -p "$tmp" && sudo mv "$PIN" "$tmp/99-mem_sleep.conf" 2>/dev/null; then
          printf 'Removed a deep-sleep pin (this kernel supports s2idle). Restore with:\n  sudo cp "%s/99-mem_sleep.conf" /etc/tmpfiles.d/ && sudo systemd-tmpfiles --create\n' "$tmp" > "$tmp/RECOVERY.txt"
          echo "[suspend] moved stale deep pin -> $tmp/ (recoverable)"
          echo s2idle | sudo tee /sys/power/mem_sleep >/dev/null 2>&1 || true
        else
          rmdir "$tmp" 2>/dev/null || true
          echo "[suspend] could not remove $PIN (need sudo) — remove it manually to restore s2idle"
        fi
      fi ;;
  esac
}

main "$@"
