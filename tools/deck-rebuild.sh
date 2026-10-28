#!/usr/bin/env bash
set -e
export APPIMAGE_EXTRACT_AND_RUN=1
export TMPDIR="$HOME/esde-build/tmp"; mkdir -p "$TMPDIR"
cd "$HOME/esde-build/ES-DE"
echo "=== rebuild (SDL cached) $(date +%H:%M:%S) ==="
git checkout -q deck-patches      # build from the MAD fork branch (was apply-patches.sh)
echo "  on $(git rev-parse --abbrev-ref HEAD) @ $(git describe --tags --always 2>/dev/null) — patch commits:"
BASE="$(git tag -l 'base/v*' --merged deck-patches --sort=-v:refname | head -1)"
echo "  upstream base: ${BASE:-NONE FOUND}"
[ -n "$BASE" ] && git log --oneline "$BASE..deck-patches" | sed 's/^/    /'
bash tools/create_AppImage_SteamDeck.sh 2>&1 | tail -18
[ -f ES-DE_x64_SteamDeck.AppImage ] && { echo "REBUILD OK"; ls -la ES-DE_x64_SteamDeck.AppImage; } || echo "REBUILD FAILED"
