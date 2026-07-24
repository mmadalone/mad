#!/usr/bin/env bash
# Fetch the rclone + restic static binaries the cloud backup uses into ~/Emulation/tools/bin. They
# live on /home (so a SteamOS update does NOT wipe them), but there was no re-provision path if they
# were ever deleted - deck-post-update.sh (and a fresh setup) can call this. Idempotent: a binary
# that's already present is skipped. Needs curl + unzip (rclone) + bzip2 (restic) - all base packages.
set -uo pipefail

BIN="${MAD_BIN_DIR:-$HOME/Emulation/tools/bin}"
RCLONE_VER="${RCLONE_VER:-v1.74.4}"
RESTIC_VER="${RESTIC_VER:-0.19.1}"
ARCH="linux-amd64"
log() { echo "[fetch-cloud-bins] $*"; }

mkdir -p "$BIN" || { log "cannot create $BIN"; exit 1; }
rc=0

fetch_rclone() {
    [ -x "$BIN/rclone" ] && { log "rclone present - skip"; return 0; }
    command -v unzip >/dev/null 2>&1 || { log "unzip missing - cannot fetch rclone"; return 1; }
    local url="https://downloads.rclone.org/${RCLONE_VER}/rclone-${RCLONE_VER}-${ARCH}.zip"
    local tmp; tmp="$(mktemp -d)"
    log "fetching rclone ${RCLONE_VER} ..."
    if curl -fsSL --max-time 300 "$url" -o "$tmp/r.zip" && unzip -qo "$tmp/r.zip" -d "$tmp" \
        && cp -f "$tmp"/rclone-*/rclone "$BIN/rclone" && chmod +x "$BIN/rclone" \
        && "$BIN/rclone" version >/dev/null 2>&1; then
        log "rclone installed: $("$BIN/rclone" version 2>/dev/null | head -1)"
    else
        log "rclone fetch FAILED ($url)"; rc=1
    fi
    rm -rf "$tmp"
}

fetch_restic() {
    [ -x "$BIN/restic" ] && { log "restic present - skip"; return 0; }
    command -v bunzip2 >/dev/null 2>&1 || { log "bunzip2 missing - cannot fetch restic"; return 1; }
    local url="https://github.com/restic/restic/releases/download/v${RESTIC_VER}/restic_${RESTIC_VER}_linux_amd64.bz2"
    local tmp; tmp="$(mktemp -d)"
    log "fetching restic ${RESTIC_VER} ..."
    if curl -fsSL --max-time 300 "$url" -o "$tmp/restic.bz2" && bunzip2 -f "$tmp/restic.bz2" \
        && cp -f "$tmp/restic" "$BIN/restic" && chmod +x "$BIN/restic" \
        && "$BIN/restic" version >/dev/null 2>&1; then
        log "restic installed: $("$BIN/restic" version 2>/dev/null | head -1)"
    else
        log "restic fetch FAILED ($url)"; rc=1
    fi
    rm -rf "$tmp"
}

fetch_rclone
fetch_restic
exit "$rc"
