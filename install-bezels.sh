#!/usr/bin/env bash
# Install The Bezel Project's per-game arcade bezels for the Deck's flatpak RetroArch.
# Replaces RetroPie's install.sh (which uses /opt/retropie/configs/all/retroarch/...).
#
# What this does:
#   - Symlinks the bezel CFG + PNG files into the flatpak's overlay dir
#     (~/.var/app/.../retroarch/overlays/GameBezels/<system>/)
#   - For each bezel, creates a per-game RetroArch config in each requested core dir
#     (MAME/<game>.cfg, FinalBurn Neo/<game>.cfg, etc.) that loads the overlay
#   - Per-game config also forces video_aspect_ratio_auto = false + 4:3 lock so the
#     game image sits in the bezel cutout correctly
#
# Idempotent: re-running is safe — symlinks are replaced, per-game configs re-written.
# Cleanup: per-game configs that include `# bezelproject` are recognised and can be removed.

set -euo pipefail

BEZEL_SRC="${1:-/home/deck/Emulation/tools/bezelproject/bezelproject-MAME}"
TARGET_SYSTEM="${2:-MAME}"            # subfolder under GameBezels/
TARGET_CORES=("MAME" "MAME 2010" "MAME 2003-Plus" "FinalBurn Neo" "FB Alpha 2012")

OVERLAY_DIR="$HOME/.var/app/org.libretro.RetroArch/config/retroarch/overlays/GameBezels/$TARGET_SYSTEM"
CONFIG_BASE="$HOME/.var/app/org.libretro.RetroArch/config/retroarch/config"

# Locate the bezel-pack subdir inside the cloned repo. bezelproject-MAME uses
# retroarch/overlay/ArcadeBezels/; older variants used GameBezels/<system>/.
SRC_SUBDIR=""
for candidate in "$BEZEL_SRC/retroarch/overlay/ArcadeBezels" \
                 "$BEZEL_SRC/retroarch/overlay/GameBezels/$TARGET_SYSTEM" \
                 "$BEZEL_SRC/$TARGET_SYSTEM"; do
    if [[ -d "$candidate" ]]; then SRC_SUBDIR="$candidate"; break; fi
done
if [[ -z "$SRC_SUBDIR" || ! -d "$SRC_SUBDIR" ]]; then
    echo "FATAL: could not find a '$TARGET_SYSTEM' subdir inside $BEZEL_SRC" >&2
    exit 1
fi
echo "==> source bezel dir: $SRC_SUBDIR"

mkdir -p "$OVERLAY_DIR"

# 1) symlink every .cfg + .png from the repo into the flatpak overlay dir.
echo "==> linking bezel files into $OVERLAY_DIR"
count=0
shopt -s nullglob
for src in "$SRC_SUBDIR"/*.cfg "$SRC_SUBDIR"/*.png; do
    name=$(basename "$src")
    dest="$OVERLAY_DIR/$name"
    ln -sfn "$src" "$dest"
    count=$((count+1))
done
echo "    linked $count files"

# 2) for each .cfg in the bezel dir, write a per-game retroarch config in each
#    target core dir that loads that overlay. Skip games whose ROMs we don't have.
echo "==> generating per-game RetroArch configs"
roms_dir_fba=/run/media/deck/1tbDeck/ROMs/fba
roms_dir_arcade=/run/media/deck/1tbDeck/ROMs/arcade
roms_dir_fbneo=/run/media/deck/1tbDeck/ROMs/fbneo

generated=0; skipped=0
for cfg_src in "$SRC_SUBDIR"/*.cfg; do
    game=$(basename "$cfg_src" .cfg)
    # Skip if no matching ROM exists anywhere
    if [[ ! -f "$roms_dir_fba/$game.zip" \
        && ! -f "$roms_dir_arcade/$game.zip" \
        && ! -f "$roms_dir_arcade/$game.chd" \
        && ! -f "$roms_dir_fbneo/$game.zip" ]]; then
        skipped=$((skipped+1))
        continue
    fi
    for core in "${TARGET_CORES[@]}"; do
        core_dir="$CONFIG_BASE/$core"
        mkdir -p "$core_dir"
        target="$core_dir/$game.cfg"
        # House rule #5: never clobber a hand-made config. If the file exists and lacks
        # our auto-gen marker, move it to a recoverable _TMP on the SAME filesystem
        # (these live on /home -> ~/Downloads/_TMP) before overwriting. Re-running over
        # our own output is unaffected (the marker matches), so idempotency is kept.
        if [[ -f "$target" ]] && ! grep -q 'bezelproject\|wire-bezels' "$target"; then
            if [[ -z "${BEZEL_TMP:-}" ]]; then
                BEZEL_TMP="$HOME/Downloads/_TMP_bezel-overwrite-$(date +%Y%m%d-%H%M%S)"
                mkdir -p "$BEZEL_TMP"
                printf '%s\n' \
                    "Hand-made RetroArch per-game .cfg files that install-bezels.sh would" \
                    "have overwritten — moved here instead of destroyed (house rule #5)." \
                    "Each file is named <core>__<game>.cfg; restore by moving it back to" \
                    "  $CONFIG_BASE/<core>/<game>.cfg" > "$BEZEL_TMP/RECOVERY.txt"
            fi
            mv "$target" "$BEZEL_TMP/${core}__${game}.cfg"
            echo "    preserved hand-made $core/$game.cfg -> $BEZEL_TMP"
        fi
        cat > "$target" <<EOF
# bezelproject — auto-generated, safe to delete
input_overlay = "$OVERLAY_DIR/$game.cfg"
input_overlay_enable = "true"
input_overlay_opacity = "1.000000"
video_fullscreen = "true"
aspect_ratio_index = "22"
video_aspect_ratio = "1.333333"
EOF
    done
    generated=$((generated+1))
done

echo "    generated per-game configs for $generated games (skipped $skipped — no ROM)"
echo "    each config written to ${#TARGET_CORES[@]} cores: ${TARGET_CORES[*]}"
if [[ -n "${BEZEL_TMP:-}" ]]; then
    echo "    ⚠ hand-made configs were preserved (not overwritten) in: $BEZEL_TMP (see RECOVERY.txt)"
fi
echo
echo "Note: aspect_ratio_index = 22 is 'Config' (manual). Combined with"
echo "video_aspect_ratio = 1.333333, that's 4:3 — the standard arcade aspect"
echo "that fits inside Bezel Project's bezel cutouts."
