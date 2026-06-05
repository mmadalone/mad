#!/usr/bin/env bash
# Switch Dolphin's Wii Remote slots 1 & 2 between setups WITHOUT touching any
# button/IR/extension mappings (only the per-slot `Source =` line changes):
#
#   sinden  -> W1=Emulated(1), W2=Emulated(1)   = the 2 Sinden lightgun profiles
#   real    -> W1=Real(2),     W2=None(0)        = ONE real Wiimote via the DolphinBar
#   real2   -> W1=Real(2),     W2=Real(2)        = TWO real Wiimotes (2-player)
#
# Why `real` leaves slot 2 OFF: with slot 2 enabled but unused (single-player game
# or only one remote connected), Dolphin spams "Wii Remote 2 is disabled by the
# emulator". Use `real2` only when you actually want a second remote.
#
# Only `Source =` inside [Wiimote1]/[Wiimote2] is changed; the Sinden mapping
# lines in those blocks are never modified. Also toggles WiimoteContinuousScanning.
# MUST run with Dolphin CLOSED (Dolphin rewrites its config on exit).
set -euo pipefail

D="$HOME/.var/app/org.DolphinEmu.dolphin-emu/config/dolphin-emu"
WN="$D/WiimoteNew.ini"
DI="$D/Dolphin.ini"

mode="${1:-}"
case "$mode" in
  sinden) w1=1; w2=1; scan=False ;;
  real)   w1=2; w2=0; scan=True  ;;
  real2)  w1=2; w2=2; scan=True  ;;
  *) echo "usage: $(basename "$0") {sinden|real|real2}"; exit 2 ;;
esac

[ -f "$WN" ] || { echo "ERROR: not found: $WN" >&2; exit 1; }

if pgrep -fa 'dolphin-emu|DolphinEmu|dolphin_emu' >/dev/null 2>&1; then
  echo "ERROR: Dolphin appears to be running — close it first, then re-run." >&2
  exit 1
fi

cp -f "$WN" "$WN.preswitch-bak"
cp -f "$DI" "$DI.preswitch-bak"

# Set Source per slot; leave every other line (incl. all mappings) untouched.
tmp="$(mktemp)"
awk -v w1="$w1" -v w2="$w2" '
  /^\[/ { sec=$0 }
  ($0 ~ /^Source[[:space:]]*=/) && sec=="[Wiimote1]" { print "Source = " w1; next }
  ($0 ~ /^Source[[:space:]]*=/) && sec=="[Wiimote2]" { print "Source = " w2; next }
  { print }
' "$WN" > "$tmp" && mv "$tmp" "$WN"

if grep -qE '^WiimoteContinuousScanning' "$DI"; then
  sed -i "s/^WiimoteContinuousScanning *=.*/WiimoteContinuousScanning = $scan/" "$DI"
elif grep -qE '^\[Core\]' "$DI"; then
  sed -i "0,/^\[Core\]/s//[Core]\nWiimoteContinuousScanning = $scan/" "$DI"
fi

echo "Dolphin Wii mode = '$mode'  (Wiimote1 Source=$w1, Wiimote2 Source=$w2, ContinuousScanning=$scan)"
awk '/^\[Wiimote[12]\]/{s=$0} /^Source/{print "  "s" -> "$0}' "$WN"
