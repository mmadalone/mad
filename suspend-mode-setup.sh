#!/usr/bin/env bash
# suspend-mode-setup.sh — pin the CORRECT suspend mode for THIS Steam Deck.
#
# DECIDE BY THE KERNEL QUIRK, NOT THE DMI MODEL. The Steam Deck kernel carries a DMI quirk
# ("PM: Steam Deck quirk - no s2idle allowed!") that forbids s2idle. It fires on the LCD
# (Jupiter) AND — confirmed live 2026-06-22 — on this OLED (Galileo) too. So the model is NOT
# the signal. Where s2idle is blocked, a power-button press hits the unsupported path and the
# screen dims then immediately wakes ("sleeps but comes back up"); deep/S3 is the only mode
# that actually sleeps. (An earlier "OLED => s2idle" version of this script was wrong and
# re-broke suspend on this Deck on every update — see deck-docs/power-suspend.md.)
#   * s2idle NOT allowed (quirk present / kernel doesn't list it / can't verify) => PIN deep.
#   * s2idle genuinely allowed (kernel lists it AND no quirk in the boot log) => leave s2idle
#     (modern standby), and REMOVE any stale deep pin.
# Unverifiable => deep, because deep is always safe on a Steam Deck. /etc is wiped by a SteamOS
# update, so deck-post-update.sh re-runs this each time.
#
# Honors install.conf INSTALL_SUSPEND:  auto (default = quirk-aware) | on (force deep) |
# off (don't touch suspend).  $MAD_S2IDLE_OK=1|0 overrides s2idle detection for tests.
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

# True only if the kernel ACTUALLY allows s2idle (so it would really sleep). The Steam Deck
# quirk forbids it on both models; absence of proof => treat as blocked (deep is safe).
s2idle_supported() {
  case "${MAD_S2IDLE_OK:-}" in 1|on|yes|true) return 0 ;; 0|off|no|false) return 1 ;; esac
  grep -qw s2idle "${MAD_MEM_SLEEP_FILE:-/sys/power/mem_sleep}" 2>/dev/null || return 1  # kernel doesn't list it
  local klog; klog="$(journalctl -kb 2>/dev/null)"
  [ -n "$klog" ] || return 1                              # can't verify -> safe default: deep
  # Pure-bash substring test (NOT `… | grep -q`): under `set -o pipefail` a `grep -q` closes the
  # pipe early, SIGPIPEs the upstream, and the pipeline reports failure even on a match — which
  # silently flipped "blocked" to "supported" here. The case avoids the pipe entirely.
  case "$klog" in *"no s2idle allowed"*) return 1 ;; esac  # the quirk -> blocked
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
