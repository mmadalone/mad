#!/usr/bin/env bash
# Install Bezel Project per-game bezels for every console the user has ROMs for.
# Wraps install-bezels.sh per system, with the right ROM dir + cores list per system.
#
# Trusts the Bezel Project's curation re. 16:9 games (they don't ship bezels for
# widescreen-native titles). For Dreamcast/Saturn/Naomi, if a 16:9 game still gets
# a bezel and looks bad, delete its per-game config (tagged "# bezelproject").

set -uo pipefail

BEZEL_BASE=/home/deck/Emulation/tools/bezelproject
ROMS=/run/media/deck/1tbDeck/ROMs
OVERLAY_BASE="$HOME/.var/app/org.libretro.RetroArch/config/retroarch/overlays/GameBezels"
CONFIG_BASE="$HOME/.var/app/org.libretro.RetroArch/config/retroarch/config"

# Each entry: REPO_NAME|TARGET_SUBDIR|ROM_DIRS|CORES
# ROM_DIRS is comma-separated relative paths under $ROMS (script will OR-match against all)
# CORES is comma-separated core names (per-game config goes into each)
declare -a SYSTEMS=(
    "3DO|3DO|3do|Opera"
    "Amiga|Amiga|amigacd32|PUAE"
    "Dreamcast|Dreamcast|dreamcast|Flycast"
    "Famicom|Famicom|famicom|Mesen,Nestopia,FCEUmm"
    "MegaDrive|Megadrive|genesis,megadrive|Genesis Plus GX,BlastEm,PicoDrive"
    "MasterSystem|MasterSystem|mastersystem|Genesis Plus GX,Gearsystem"
    "N64|Nintendo 64|n64|Mupen64Plus-Next,ParaLLEl N64"
    "NES|NES|nes|Mesen,Nestopia,FCEUmm"
    "PCEngine|PC Engine|pcenginecd|Beetle PCE,Beetle PCE Fast"
    "PCFX|PCFX|pcfx|Beetle PC-FX"
    "Saturn|Saturn|saturn|Beetle Saturn,Kronos,YabaSanshiro"
    "SegaCD|Sega CD|segacd|Genesis Plus GX,PicoDrive"
    "SNES|SNES|snes,sfc|Snes9x,bsnes,Snes9x - Current"
    "SuperGrafx|SuperGrafx|supergrafx|Beetle SuperGrafx,Beetle PCE"
    "GameGear|GameGear|gamegear|Gearsystem,Genesis Plus GX"
    "Sega32x|Sega32X|sega32x|PicoDrive"
)

total_games=0
total_links=0

for entry in "${SYSTEMS[@]}"; do
    IFS='|' read -r repo target_subdir rom_dirs cores <<< "$entry"
    repo_path="$BEZEL_BASE/bezelproject-$repo"

    if [[ ! -d "$repo_path" ]]; then
        echo "==> [$repo] repo missing at $repo_path — skipping"
        continue
    fi

    # Find the bezel source dir inside the repo
    src_subdir=""
    for c in "$repo_path/retroarch/overlay/GameBezels/$target_subdir" \
             "$repo_path/retroarch/overlay/ArcadeBezels" \
             "$repo_path/retroarch/overlay/GameBezels/$repo"; do
        if [[ -d "$c" ]]; then src_subdir="$c"; break; fi
    done
    if [[ -z "$src_subdir" ]]; then
        echo "==> [$repo] no bezel dir found under retroarch/overlay/ — skipping"
        find "$repo_path/retroarch/overlay" -maxdepth 2 -type d 2>/dev/null | head -5
        continue
    fi

    target_dir="$OVERLAY_BASE/$target_subdir"
    mkdir -p "$target_dir"

    echo "==> [$repo] linking from $src_subdir → $target_dir"
    links=0
    shopt -s nullglob
    for src in "$src_subdir"/*.cfg "$src_subdir"/*.png; do
        ln -sfn "$src" "$target_dir/$(basename "$src")"
        links=$((links+1))
    done

    # Generate per-game configs for ROMs we have
    games=0
    IFS=',' read -ra DIRS <<< "$rom_dirs"
    IFS=',' read -ra CORE_LIST <<< "$cores"

    for cfg_src in "$src_subdir"/*.cfg; do
        game=$(basename "$cfg_src" .cfg)
        # Check if any matching ROM exists across the allowed dirs / extensions
        found=0
        for dir in "${DIRS[@]}"; do
            for ext in zip 7z chd iso cue cdi bin nes sfc smc smd gen md gb gbc gba n64 z64 v64 pce sgx wbfs rvz gcm; do
                if [[ -f "$ROMS/$dir/$game.$ext" ]]; then
                    found=1; break 2
                fi
            done
        done
        [[ $found -eq 0 ]] && continue

        for core in "${CORE_LIST[@]}"; do
            core_dir="$CONFIG_BASE/$core"
            mkdir -p "$core_dir"
            cat > "$core_dir/$game.cfg" <<EOF
# bezelproject — auto-generated, safe to delete
input_overlay = "$target_dir/$game.cfg"
input_overlay_enable = "true"
input_overlay_opacity = "1.000000"
video_fullscreen = "true"
aspect_ratio_index = "22"
video_aspect_ratio = "1.333333"
EOF
        done
        games=$((games+1))
    done

    echo "    linked $links files, generated configs for $games games across ${#CORE_LIST[@]} cores"
    total_games=$((total_games + games))
    total_links=$((total_links + links))
done

echo
echo "==> GRAND TOTAL: $total_links overlay files linked, $total_games games configured"
echo
echo "Reminder: 16:9-native games on Saturn/Dreamcast may not look right with the"
echo "4:3 aspect lock. If you find one, delete its per-game config (search for"
echo "  grep -l '# bezelproject' \"$CONFIG_BASE\"/*/\$gamename.cfg"
echo ")"
