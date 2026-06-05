#!/usr/bin/env bash
# Idempotently set up Multi-Pointer X for the Sinden P2 gun.
# Creates a "Sinden P2" master pointer if missing and reattaches P2's
# gun mouse + keyboard slaves to it, so Dolphin (and any X11 app) sees
# each gun as a separate cursor. P1 stays on the default Virtual core
# pointer. Required for 2-player Sinden co-op in Dolphin (works around
# Dolphin bug #13628 / X11 merged-cursor limitation).
#
# Safe to run repeatedly: skips create if the master already exists,
# and reattach is a no-op if the slave is already where we want it.
#
# Called from sinden-start.sh after the driver is up.
set -uo pipefail

# Must run in the user's X session.
export DISPLAY="${DISPLAY:-:0}"

if ! command -v xinput >/dev/null 2>&1; then
    echo "sinden-mpx-setup: xinput not installed — skipping MPX setup (single-player only)" >&2
    exit 0
fi

# Get the canonical P2 gun /dev/input/event* paths from our udev symlinks.
P2_MOUSE_NODE=$(readlink -f /dev/input/sinden-gun-p2-event 2>/dev/null || true)
if [[ -z "$P2_MOUSE_NODE" ]]; then
    echo "sinden-mpx-setup: /dev/input/sinden-gun-p2-event missing — udev rules not loaded? skipping" >&2
    exit 0
fi

# Find the xinput slave id whose "Device Node" property matches the P2 mouse event.
# Walk all devices named "Unknown SindenLightgun Mouse" and pick the one with the
# right /dev/input/event path. This is robust to xinput-id reordering between sessions.
find_slave_by_node() {
    local target_name="$1" target_node="$2"
    while IFS= read -r line; do
        local id=$(awk -F'id=' '{print $2}' <<<"$line" | awk '{print $1}')
        [[ -z "$id" ]] && continue
        local node=$(xinput list-props "$id" 2>/dev/null | awk -F'"' '/Device Node/ {print $2}')
        if [[ -n "$node" && "$(readlink -f "$node")" == "$target_node" ]]; then
            echo "$id"
            return 0
        fi
    done < <(xinput list 2>/dev/null | grep -F "$target_name")
    return 1
}

P2_MOUSE_ID=$(find_slave_by_node "Unknown SindenLightgun Mouse" "$P2_MOUSE_NODE")
if [[ -z "$P2_MOUSE_ID" ]]; then
    echo "sinden-mpx-setup: could not find xinput slave for P2 mouse ($P2_MOUSE_NODE) — skipping" >&2
    exit 0
fi

# Create master pointer if missing.
if ! xinput list 2>/dev/null | grep -q "Sinden P2 pointer"; then
    echo "sinden-mpx-setup: creating master pointer 'Sinden P2'" >&2
    xinput create-master "Sinden P2" || {
        echo "sinden-mpx-setup: create-master failed" >&2
        exit 0
    }
fi

# Reattach P2 mouse slave to the new master. Idempotent: a no-op if already attached.
xinput reattach "$P2_MOUSE_ID" "Sinden P2 pointer" 2>/dev/null && \
    echo "sinden-mpx-setup: P2 mouse (xid=$P2_MOUSE_ID, node=$P2_MOUSE_NODE) attached to Sinden P2 pointer" >&2

# Also reattach the P2 keyboard slave so its key emissions don't merge into
# the Virtual core keyboard. Not strictly required for Dolphin (Wiimote2's
# keyboard bindings use evdev directly), but keeps the per-gun separation clean.
P2_KBD_NODE=$(readlink -e /dev/input/event27 2>/dev/null || true)
# Find via the SindenLightgun Keyboard name + USB topology. Two of these exist
# (P1 and P2). The P2 one is the one whose Device Node matches the keyboard
# interface of vendor=16c0 product=0f39 (we know the kernel path from the udev rule).
if [[ -n "$P2_KBD_NODE" ]]; then
    P2_KBD_ID=$(find_slave_by_node "Unknown SindenLightgun Keyboard" "$P2_KBD_NODE")
    if [[ -n "$P2_KBD_ID" ]]; then
        xinput reattach "$P2_KBD_ID" "Sinden P2 keyboard" 2>/dev/null && \
            echo "sinden-mpx-setup: P2 keyboard (xid=$P2_KBD_ID) attached" >&2
    fi
fi

exit 0
