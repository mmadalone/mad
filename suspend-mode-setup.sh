#!/usr/bin/env bash
# suspend-mode-setup.sh — pin the CORRECT suspend mode for THIS Steam Deck model.
#
# WHY model-aware (this replaced an unconditional `pin deep` that was wrong on OLED):
#   * LCD Deck (Jupiter): the neptune kernel forbids s2idle (DMI quirk; firmware offers
#     only S0 S3 S4 S5). It MUST use deep/S3 — unpinned, a power-button press hits
#     "s2idle not supported" and exits instantly (screen never sleeps). So: PIN deep.
#   * OLED Deck (Galileo): DOES support s2idle (modern standby) and that's its correct
#     default. Pinning deep there gives up modern standby → leave s2idle, and REMOVE any
#     stale deep pin a prior unconditional version left behind.
# /etc is wiped by a SteamOS update, so deck-post-update.sh re-runs this each time.
#
# Honors install.conf INSTALL_SUSPEND:  auto (default = model-aware) | on (force deep) |
# off (don't touch suspend).  $MAD_DECK_MODEL overrides detection for tests (lcd|oled).
#
#   suspend-mode-setup.sh           apply (needs sudo for /etc + sysfs)
#   suspend-mode-setup.sh --check   report only, NO sudo: exit 0 = already correct, 1 = needs apply
set -uo pipefail

PIN="${MAD_SUSPEND_PIN:-/etc/tmpfiles.d/99-mem_sleep.conf}"   # override for tests
HERE="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
# Pull INSTALL_SUSPEND from install.conf if present (sets the var; unset => default auto).
# shellcheck source=/dev/null
[ -f "$HERE/lib/install-conf.sh" ] && . "$HERE/lib/install-conf.sh"

detect_model() {
  if [ -n "${MAD_DECK_MODEL:-}" ]; then printf '%s' "${MAD_DECK_MODEL,,}"; return; fi
  local s; s="$(cat /sys/class/dmi/id/product_name /sys/class/dmi/id/board_name 2>/dev/null)"
  case "$s" in *Galileo*) echo oled ;; *Jupiter*) echo lcd ;; *) echo unknown ;; esac
}

# Echo the desired end-state: deep | none | skip.
desired_mode() {
  local pref="${INSTALL_SUSPEND:-auto}"
  case "${pref,,}" in
    off) echo skip; return ;;
    on)  echo deep; return ;;
  esac
  case "$(detect_model)" in lcd) echo deep ;; *) echo none ;; esac   # oled/unknown => s2idle
}

main() {
  local mode; mode="$(desired_mode)"
  local model; model="$(detect_model)"

  if [ "${1:-}" = "--check" ]; then
    case "$mode" in
      skip) exit 0 ;;
      deep) [ -f "$PIN" ] && exit 0 || { echo "Suspend mode deep/S3 (mem_sleep) — LCD needs it"; exit 1; } ;;
      none) [ -f "$PIN" ] && { echo "Stale mem_sleep=deep pin on an OLED Deck (should use s2idle)"; exit 1; } || exit 0 ;;
    esac
  fi

  case "$mode" in
    skip)
      echo "[suspend] INSTALL_SUSPEND=off — leaving suspend untouched"; exit 0 ;;
    deep)
      echo "[suspend] model=$model -> pin deep/S3 (LCD-only s2idle workaround)"
      if printf 'w /sys/power/mem_sleep - - - - deep\n' | sudo tee "$PIN" >/dev/null \
         && sudo systemd-tmpfiles --create "$PIN" 2>/dev/null; then
        echo "[suspend] pinned; active now: $(cat /sys/power/mem_sleep 2>/dev/null)"
      else
        echo "[suspend] FAILED to pin — apply manually: echo deep | sudo tee /sys/power/mem_sleep"
      fi ;;
    none)
      echo "[suspend] model=$model -> s2idle (modern standby); no pin needed"
      if [ -f "$PIN" ]; then
        local tmp="$HOME/Downloads/_TMP-suspend-fix-$(date +%Y%m%d-%H%M%S)"
        # Write the RECOVERY note only AFTER a successful move, so a failed mv doesn't orphan
        # a _TMP dir pointing at a file that was never moved there.
        if mkdir -p "$tmp" && sudo mv "$PIN" "$tmp/99-mem_sleep.conf" 2>/dev/null; then
          printf 'OLED Deck: removed an LCD-only deep-sleep pin. Restore with:\n  sudo cp "%s/99-mem_sleep.conf" /etc/tmpfiles.d/ && sudo systemd-tmpfiles --create\n' "$tmp" > "$tmp/RECOVERY.txt"
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
