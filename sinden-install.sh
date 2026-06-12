#!/usr/bin/env bash
# Install / repair the official Sinden Steam Deck driver into ~/Lightgun.
#
# Downloads the official software bundle from sindenlightgun.com (we never
# redistribute the binaries) and extracts ONLY the SteamdeckVersion/Lightgun
# payload. Preserves an existing LightgunMono.exe.config (the user's tuned
# settings) and any extra files (sinden-smooth.so, etc-backup, …); files it
# replaces are backed up to ~/Downloads/_TMP_sinden-install-<ts>/ first.
#
#   sinden-install.sh --check   print status lines (driver/mono/config), no changes
#   sinden-install.sh           install/repair (progress on stdout, errors exit != 0)
#
# mono itself comes from pacman (wiped by SteamOS updates) and is owned by
# deck-post-update.sh step 2 — this script only REPORTS it.
set -euo pipefail

VERSION="V2.08b"
URL="https://www.sindenlightgun.com/software/SindenLightgunSoftwareReleaseV2.08b.zip"
ZIP_SUBDIR="SindenLightgunSoftwareReleaseV2.08b/SindenLightgunLinuxSoftwareV2.05/SteamdeckVersion/Lightgun"
LG="$HOME/Lightgun"
OFFICIAL_FILES=(LightgunMono.exe libCameraInterface.so libSdlInterface.so License.txt)

driver_present() {
    [[ -f "$LG/LightgunMono.exe" && -f "$LG/libCameraInterface.so" \
       && -f "$LG/libSdlInterface.so" ]]
}

if [[ "${1:-}" == "--check" ]]; then
    driver_present && echo "driver: present" || echo "driver: missing"
    command -v mono >/dev/null 2>&1 && echo "mono: present" || echo "mono: missing"
    [[ -f "$LG/LightgunMono.exe.config" ]] && echo "config: present" || echo "config: missing"
    exit 0
fi

echo "Downloading the official Sinden software bundle ($VERSION, ~25 MB)…"
TMP="$(mktemp -d /tmp/sinden-install.XXXXXX)"
trap 'rm -rf "$TMP"' EXIT
curl -fSL --retry 2 -o "$TMP/bundle.zip" "$URL"
echo "Extracting the Steam Deck driver…"
unzip -q "$TMP/bundle.zip" "$ZIP_SUBDIR/*" -d "$TMP"
SRC="$TMP/$ZIP_SUBDIR"
[[ -f "$SRC/LightgunMono.exe" ]] || { echo "FATAL: bundle layout changed — LightgunMono.exe not at $ZIP_SUBDIR" >&2; exit 1; }

mkdir -p "$LG"

# Back up anything we are about to replace (house rule: never delete).
TS="$(date +%Y%m%d-%H%M%S)"
BK="$HOME/Downloads/_TMP_sinden-install-$TS"
backed=0
for f in "${OFFICIAL_FILES[@]}"; do
    if [[ -e "$LG/$f" ]]; then
        mkdir -p "$BK"
        cp -a "$LG/$f" "$BK/"
        backed=1
    fi
done
if [[ -d "$LG/Overlays" ]]; then
    mkdir -p "$BK"
    cp -a "$LG/Overlays" "$BK/"
    backed=1
fi
if [[ $backed -eq 1 ]]; then
    printf 'Replaced by sinden-install.sh %s on %s.\nCopy any file back to ~/Lightgun to undo.\n' \
        "$VERSION" "$TS" > "$BK/RECOVERY.txt"
    echo "Previous files backed up to $BK"
fi

echo "Installing driver files into ~/Lightgun…"
for f in "${OFFICIAL_FILES[@]}"; do
    cp -a "$SRC/$f" "$LG/$f"
done
cp -a "$SRC/Overlays" "$LG/" 2>/dev/null || true

# The config holds the user's tuned settings — only seed a default when absent.
if [[ ! -f "$LG/LightgunMono.exe.config" ]]; then
    cp -a "$SRC/LightgunMono.exe.config" "$LG/LightgunMono.exe.config"
    echo "Installed the default LightgunMono.exe.config (no existing config found)."
else
    echo "Kept the existing LightgunMono.exe.config (your tuned settings)."
fi

command -v mono >/dev/null 2>&1 \
    && echo "Driver $VERSION installed — ready." \
    || echo "Driver $VERSION installed, but mono is MISSING — run deck-post-update.sh from Desktop Mode."
