#!/usr/bin/env bash
# Launch a MUGEN game from ES-DE.
#   mugen.sh ikemen <game-folder>      -> run via Ikemen GO (preferred)
#   mugen.sh native <path-to-binary>   -> run native Linux binary
#   mugen.sh win    <path-to-exe>      -> run Windows .exe via Proton (legacy)
# Target paths may be relative to ES-DE's starting directory (the mugen rom folder).

set -uo pipefail

mode=${1:-}; target=${2:-}
if [[ -z $mode || -z $target ]]; then
    echo "Usage: $0 {ikemen|native|win} <path> [args...]" >&2
    exit 64
fi

[[ ${target:0:1} != / ]] && target="$PWD/$target"

. "$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]:-$0}")" && pwd)/lib/mad-paths.sh" 2>/dev/null || . "$HOME/Emulation/tools/launchers/lib/mad-paths.sh"
log_dir="$storageRoot/mugen/logs"
mkdir -p "$log_dir"
log_name=$(basename "${target%/}")
# Strip only known binary extensions, preserve dots inside folder names (e.g. v3.0)
log="$log_dir/${log_name%.exe}"; log="${log%.EXE}.log"

{
    echo "==== $(date) ===="
    echo "mode=$mode  target=$target"
} >> "$log"

# Per-game engine override: a .mugen launcher can export IKEMEN_HOME to use a
# different Ikemen GO build (e.g. v0.99.0 side-by-side install). Falls back to
# the nightly default when unset.
ikemen_home="${IKEMEN_HOME:-$HOME/Emulation/tools/ikemen-go}"

case "$mode" in
    ikemen)
        [[ ! -d $target ]] && { echo "Not a directory: $target" >&2; exit 66; }
        [[ ! -x $ikemen_home/Ikemen_GO_Linux ]] && {
            echo "Ikemen GO binary missing at $ikemen_home/Ikemen_GO_Linux" >&2; exit 70;
        }

        cd "$target"

        # Bootstrap: Ikemen GO needs its own external/ (Lua scripts) and a
        # handful of common data files that Mugen 1.0 games don't ship with.
        # Symlink in whatever the game folder lacks; never overwrite game files.
        # Rule #4 exception (symlinks OK'd here) recorded in
        # deck-docs/mugen-ikemen-symlinks.md. -f so a STALE/dangling link (Ikemen
        # or the fonts moved) is force-repaired instead of ln failing on the
        # leftover link file — the [[ ! -e ]] guard still protects real game files.
        [[ ! -e external ]] && ln -sf "$ikemen_home/external" external
        # Fill in any data/ and font/ files Ikemen GO ships that the game lacks.
        for sub in data font; do
            mkdir -p "$sub"
            for f in "$ikemen_home/$sub"/*; do
                [[ -e $f ]] || continue
                name=$(basename "$f")
                [[ ! -e "$sub/$name" ]] && ln -sf "$f" "$sub/$name"
            done
        done

        # Mugen 1.0 motifs often reference Windows TTFs (arial.ttf, tahoma.ttf)
        # that aren't installed on Linux. Substitute with system DejaVu Sans;
        # metric mismatch is cosmetic — Ikemen GO panics if the file is missing.
        for win_ttf in arial ariblk tahoma verdana times calibri comic impact georgia; do
            [[ -e "font/${win_ttf}.ttf" ]] && continue
            for sys in /usr/share/fonts/TTF/DejaVuSans.ttf \
                       /usr/share/fonts/truetype/ttf-dejavu/DejaVuSans.ttf; do
                [[ -f $sys ]] && { ln -sf "$sys" "font/${win_ttf}.ttf"; break; }
            done
        done

        # First-run config init: copy Ikemen GO's default config then patch in
        # this game's preferred motif and resolution. Subsequent runs preserve
        # whatever the user sets in-game (fullscreen toggle, controls, etc.).
        if [[ ! -f save/config.json ]]; then
            mkdir -p save
            cp "$ikemen_home/save/config.json" save/config.json 2>/dev/null \
                || echo '{}' > save/config.json

            motif="data/system.def"
            cfg="data/mugen.cfg"
            if [[ -f $cfg ]]; then
                m=$(awk -F'=' '
                    /^[[:space:]]*[^;#[:space:]]/ && /[Mm][Oo][Tt][Ii][Ff]/ {
                        gsub(/^[[:space:]]+|[[:space:]]+$|\r/, "", $2)
                        gsub(/\\/, "/", $2)
                        sub(/[[:space:]]*;.*$/, "", $2)
                        gsub(/^[[:space:]]+|[[:space:]]+$/, "", $2)
                        if ($2 != "") { print $2; exit }
                    }' "$cfg")
                [[ -n $m && -f $m ]] && motif="$m"
            fi

            # Ikemen GO's GameWidth/Height is the authoring coord system —
            # i.e. the motif's localcoord, not mugen.cfg's render resolution.
            # Using the wrong values causes vertical cropping when display
            # aspect differs from game aspect.
            gw=640; gh=480
            if [[ -f $motif ]]; then
                lc=$(grep -iE '^[[:space:]]*localcoord[[:space:]]*=' "$motif" | head -1 | grep -oE '[0-9]+' | head -2)
                lw=$(echo "$lc" | head -1)
                lh=$(echo "$lc" | tail -1)
                [[ -n ${lw:-} && -n ${lh:-} ]] && { gw=$lw; gh=$lh; }
            fi

            # Compute a fullscreen size that preserves the game's aspect
            # inside the actual screen. Ikemen GO's default behavior fits to
            # screen width, which crops top/bottom when the game is taller
            # (e.g. 4:3 motif on the 16:10 Deck panel) — pinning the size
            # explicitly produces letterbox side-bars instead.
            sw=1280; sh=800
            if command -v xrandr >/dev/null; then
                read sw sh < <(xrandr 2>/dev/null | awk '/^Screen 0/ {for(i=1;i<=NF;i++) if($i=="current"){w=$(i+1); h=$(i+3); gsub(/[^0-9]/,"",w); gsub(/[^0-9]/,"",h); print w, h; exit}}')
                [[ -z ${sw:-} ]] && { sw=1280; sh=800; }
            fi
            # Pick whichever dimension is the limiter: aspectGame vs aspectScreen.
            # Use integer math: compare gw*sh vs gh*sw.
            if (( gw * sh > gh * sw )); then
                # Game wider than screen → fit by width, letterbox top/bottom
                fw=$sw
                fh=$(( gh * sw / gw ))
            else
                # Game taller (or equal) → fit by height, letterbox sides
                fh=$sh
                fw=$(( gw * sh / gh ))
            fi

            tmp=$(mktemp)
            jq --arg motif "$motif" \
               --argjson gw "$gw" --argjson gh "$gh" \
               --argjson fw "$fw" --argjson fh "$fh" '
                .Fullscreen = true
                | .Borderless = true
                | .FullscreenWidth = $fw
                | .FullscreenHeight = $fh
                | .GameWidth = $gw
                | .GameHeight = $gh
                | .Motif = $motif
                | .MSAA = false
                | .DebugMode = false
                | .DebugKeys = false
            ' save/config.json > "$tmp" && mv "$tmp" save/config.json

            echo "first-run init: Motif=$motif Game=${gw}x${gh} Fullscreen=${fw}x${fh} (screen=${sw}x${sh})" >> "$log"
        fi

        # Every-launch: recompute FullscreenWidth/Height for the current screen.
        # Catches screen changes (Deck panel ↔ docked TV) so letterbox math stays
        # correct without the user having to wipe save/.
        if [[ -f save/config.json ]] && command -v xrandr >/dev/null; then
            gw=$(jq -r '.GameWidth // 640' save/config.json)
            gh=$(jq -r '.GameHeight // 480' save/config.json)
            read sw sh < <(xrandr 2>/dev/null | awk '/^Screen 0/ {for(i=1;i<=NF;i++) if($i=="current"){w=$(i+1); h=$(i+3); gsub(/[^0-9]/,"",w); gsub(/[^0-9]/,"",h); print w, h; exit}}')
            if [[ -n ${sw:-} && -n ${sh:-} && $gw -gt 0 && $gh -gt 0 ]]; then
                if (( gw * sh > gh * sw )); then
                    fw=$sw; fh=$(( gh * sw / gw ))
                else
                    fh=$sh; fw=$(( gw * sh / gh ))
                fi
                cur_fw=$(jq -r '.FullscreenWidth // 0' save/config.json)
                cur_fh=$(jq -r '.FullscreenHeight // 0' save/config.json)
                if [[ $cur_fw != $fw || $cur_fh != $fh ]]; then
                    tmp=$(mktemp)
                    jq --argjson fw "$fw" --argjson fh "$fh" \
                       '.FullscreenWidth = $fw | .FullscreenHeight = $fh' \
                       save/config.json > "$tmp" && mv "$tmp" save/config.json
                    echo "fullscreen retune: ${cur_fw}x${cur_fh} -> ${fw}x${fh} (screen=${sw}x${sh})" >> "$log"
                fi
            fi
        fi

        # If the binary has already created save/config.ini (from a previous
        # launch), default-set RenderMode to Vulkan 1.3 once. Marker prevents
        # overriding subsequent user choices via the in-game video menu.
        if [[ -f save/config.ini && ! -e save/.deck-vulkan-set ]]; then
            sed -i 's|^RenderMode[[:space:]]*=.*|RenderMode              = Vulkan 1.3|' save/config.ini
            touch save/.deck-vulkan-set
        fi

        # Use -r to point at the game's motif so Ikemen GO uses the game's
        # select.def, chars, stages and screenpack — not bundled kfm720.
        motif_arg=$(jq -r '.Motif // empty' save/config.json 2>/dev/null)
        if [[ -n $motif_arg && -f $motif_arg ]]; then
            echo "launching with -r $motif_arg" >> "$log"
            exec "$ikemen_home/Ikemen_GO_Linux" -r "$motif_arg" "${@:3}" >>"$log" 2>&1
        else
            exec "$ikemen_home/Ikemen_GO_Linux" "${@:3}" >>"$log" 2>&1
        fi
        ;;

    native)
        [[ ! -f $target ]] && { echo "Not a file: $target" >&2; exit 66; }
        cd "$(dirname "$target")"
        chmod +x "./$(basename "$target")" 2>/dev/null || true
        exec "./$(basename "$target")" "${@:3}" >>"$log" 2>&1
        ;;

    win)
        [[ ! -f $target ]] && { echo "Not a file: $target" >&2; exit 66; }
        game_dir=$(dirname "$target")
        bin_name=$(basename "$target")
        compat="$HOME/.steam/root/compatibilitytools.d"
        steamapps="$HOME/.steam/root/steamapps/common"
        proton_dir=$(ls -1d "$compat"/GE-Proton* 2>/dev/null | sort -V | tail -1 || true)
        if [[ -z $proton_dir ]]; then
            proton_dir=$(ls -1d "$steamapps"/Proton\ * 2>/dev/null | grep -v 'Runtime\|Hotfix' | sort -V | tail -1 || true)
        fi
        [[ -z $proton_dir ]] && { echo "No Proton found" >&2; exit 70; }

        export STEAM_COMPAT_CLIENT_INSTALL_PATH="$HOME/.steam/root"
        export STEAM_COMPAT_DATA_PATH="$storageRoot/mugen/prefix"
        export WINEPREFIX="$STEAM_COMPAT_DATA_PATH/pfx"
        export WINEDEBUG=${WINEDEBUG:--all}
        mkdir -p "$STEAM_COMPAT_DATA_PATH"
        [[ ! -d $WINEPREFIX ]] && "$proton_dir/proton" run wineboot --init >> "$log" 2>&1
        cd "$game_dir"
        wine_bin="$proton_dir/files/bin/wine"
        [[ ! -x $wine_bin ]] && wine_bin="$proton_dir/dist/bin/wine"
        exec "$wine_bin" "$target" "${@:3}" >>"$log" 2>&1
        ;;

    *)
        echo "Unknown mode: $mode (use ikemen, native, or win)" >&2
        exit 64
        ;;
esac
