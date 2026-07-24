#!/usr/bin/env bash
set -e
export DEBIAN_FRONTEND=noninteractive
export APPIMAGE_EXTRACT_AND_RUN=1   # linuxdeploy/appimagetool: no FUSE in container
export TMPDIR="$HOME/esde-build/tmp"; mkdir -p "$TMPDIR"
echo "=== apt update + deps ($(date +%H:%M:%S)) ==="
sudo apt-get update -y >/dev/null
sudo apt-get install -y \
  build-essential git cmake gettext wget file patchelf desktop-file-utils squashfs-tools libfuse2 ca-certificates \
  libharfbuzz-dev libicu-dev libsdl2-dev libavcodec-dev libavfilter-dev libavformat-dev libavutil-dev \
  libfreeimage-dev libfreetype6-dev libgit2-dev libcurl4-openssl-dev libpugixml-dev libasound2-dev \
  libbluetooth-dev libgl1-mesa-dev libpoppler-cpp-dev \
  libpipewire-0.3-dev libpulse-dev libx11-dev libxext-dev libxrandr-dev libxcursor-dev libxi-dev \
  libwayland-dev wayland-protocols libxkbcommon-dev libdrm-dev libgbm-dev libegl1-mesa-dev libudev-dev 2>&1 | tail -3
echo "=== clean prior Arch build artifacts ($(date +%H:%M:%S)) ==="
cd "$HOME/esde-build/ES-DE"
rm -f CMakeCache.txt; rm -rf CMakeFiles es-core/CMakeFiles es-app/CMakeFiles external/SDL es-de AppDir
find . -name '*.o' -delete 2>/dev/null || true
echo "=== select the MAD fork branch + report patch commits ==="
git checkout -q deck-patches      # build from the fork branch (replaces apply-patches.sh)
echo "  on $(git rev-parse --abbrev-ref HEAD) @ $(git describe --tags --always 2>/dev/null) — patch commits:"
git log --oneline base/v3.4.1..deck-patches 2>/dev/null | sed 's/^/    /'
echo "=== run ES-DE SteamDeck AppImage recipe ($(date +%H:%M:%S)) ==="
bash tools/create_AppImage_SteamDeck.sh 2>&1 | tail -25
echo "=== RESULT ($(date +%H:%M:%S)) ==="
if [ -f ES-DE_x64_SteamDeck.AppImage ]; then echo "APPIMAGE OK"; ls -la ES-DE_x64_SteamDeck.AppImage; else echo "APPIMAGE FAILED"; fi
