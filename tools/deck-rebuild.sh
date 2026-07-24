#!/usr/bin/env bash
set -e
export APPIMAGE_EXTRACT_AND_RUN=1
export TMPDIR="$HOME/esde-build/tmp"; mkdir -p "$TMPDIR"
cd "$HOME/esde-build/ES-DE"
echo "=== rebuild (SDL cached) $(date +%H:%M:%S) ==="
git checkout -q deck-patches      # build from the MAD fork branch (was apply-patches.sh)
echo "  on $(git rev-parse --abbrev-ref HEAD) @ $(git describe --tags --always 2>/dev/null) — patch commits:"
git log --oneline base/v3.4.1..deck-patches 2>/dev/null | sed 's/^/    /'
bash tools/create_AppImage_SteamDeck.sh 2>&1 | tail -18
[ -f ES-DE_x64_SteamDeck.AppImage ] && { echo "REBUILD OK"; ls -la ES-DE_x64_SteamDeck.AppImage; } || echo "REBUILD FAILED"
