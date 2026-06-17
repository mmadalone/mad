#!/usr/bin/env bash
# Run Supermodel (Windows build) under Proton with -input-system=rawinput.
#
# Pre-launch xinput setup (restored on exit via trap):
#   - Float the X-Arcade pointer so its trackball doesn't merge with Sinden P1
#     into the default master pointer
#   - Reattach Sinden P2 mouse/keyboard from its MPX master onto the default
#     master, so Wine's rawinput enumerates it (Wine only surfaces devices
#     attached to the core pointer)
#
# Invocation: supermodel-proton.sh <rom-path> [extra-args...]
set -uo pipefail

ROM="${1:?usage: supermodel-proton.sh <rom-path>}"
shift || true

. "$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]:-$0}")" && pwd)/lib/mad-paths.sh" 2>/dev/null || . "$HOME/Emulation/tools/launchers/lib/mad-paths.sh"
SUPERMODEL_DIR="$MAD_DATA_ROOT/emulators/supermodel-win"
PROTON="$HOME/.local/share/Steam/compatibilitytools.d/GE-Proton10-34"
PREFIX="$MAD_DATA_ROOT/wine-prefixes/supermodel"

export STEAM_COMPAT_CLIENT_INSTALL_PATH="$HOME/.local/share/Steam"
export STEAM_COMPAT_DATA_PATH="$PREFIX"

# Helper: get xinput master id by exact device name
get_master_id() {
    xinput list | grep -E "$1" | grep -oP 'id=\K[0-9]+' | head -1
}
# Helper: get slave id whose master matches the given master id
get_slave_id() {
    local name_pat="$1" master="$2"
    xinput list | awk -v n="$name_pat" -v m="$master" '
        $0 ~ n && match($0, "\\("m"\\)") { match($0, "id=[0-9]+"); print substr($0,RSTART+3,RLENGTH-3); exit }'
}

CORE_PTR=$(get_master_id 'Virtual core pointer')
CORE_KBD=$(get_master_id 'Virtual core keyboard')
P2_PTR_MASTER=$(get_master_id 'Sinden P2 pointer')
P2_KBD_MASTER=$(get_master_id 'Sinden P2 keyboard')

P2_MOUSE_SLAVE=""
P2_KBD_SLAVE=""
[ -n "$P2_PTR_MASTER" ] && P2_MOUSE_SLAVE=$(get_slave_id 'Unknown SindenLightgun Mouse' "$P2_PTR_MASTER")
[ -n "$P2_KBD_MASTER" ] && P2_KBD_SLAVE=$(get_slave_id 'Unknown SindenLightgun Keyboard' "$P2_KBD_MASTER")

# X-Arcade pointer slave (HID 1241:1111 in xinput list)
XARCADE_PTR_SLAVE=$(xinput list | grep -E 'HID 1241:1111' | grep -oP 'id=\K[0-9]+' | head -1)

# Apply
[ -n "$P2_MOUSE_SLAVE" ] && [ -n "$CORE_PTR" ] && {
    echo "[supermodel-proton] Reattach Sinden P2 mouse $P2_MOUSE_SLAVE → core pointer $CORE_PTR"
    xinput reattach "$P2_MOUSE_SLAVE" "$CORE_PTR" 2>/dev/null || true
}
[ -n "$P2_KBD_SLAVE" ] && [ -n "$CORE_KBD" ] && {
    echo "[supermodel-proton] Reattach Sinden P2 keyboard $P2_KBD_SLAVE → core keyboard $CORE_KBD"
    xinput reattach "$P2_KBD_SLAVE" "$CORE_KBD" 2>/dev/null || true
}
[ -n "$XARCADE_PTR_SLAVE" ] && {
    echo "[supermodel-proton] Float X-Arcade pointer $XARCADE_PTR_SLAVE so trackball doesn't move the crosshair"
    xinput float "$XARCADE_PTR_SLAVE" 2>/dev/null || true
}

# Restore on exit
restore() {
    [ -n "$XARCADE_PTR_SLAVE" ] && [ -n "$CORE_PTR" ] && xinput reattach "$XARCADE_PTR_SLAVE" "$CORE_PTR" 2>/dev/null || true
    [ -n "$P2_MOUSE_SLAVE" ] && [ -n "$P2_PTR_MASTER" ] && xinput reattach "$P2_MOUSE_SLAVE" "$P2_PTR_MASTER" 2>/dev/null || true
    [ -n "$P2_KBD_SLAVE" ] && [ -n "$P2_KBD_MASTER" ] && xinput reattach "$P2_KBD_SLAVE" "$P2_KBD_MASTER" 2>/dev/null || true
}
trap restore EXIT INT TERM

WIN_ROM="Z:$(echo "$ROM" | sed 's|/|\\|g')"

cd "$SUPERMODEL_DIR" || exit 1

"$PROTON/proton" run ./supermodel.exe \
    -input-system=rawinput \
    -fullscreen \
    "$WIN_ROM" \
    "$@"
