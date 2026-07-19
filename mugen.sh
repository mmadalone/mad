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

# Launcher's own dir (for `python3 -m lib.*` / mad-openbor-pads.py). Defined BEFORE the case so
# every mode can use it (native/win call mugen_res too, not just ikemen). set -u would crash on it.
SELF_DIR="$(dirname "$(readlink -f "$0")")"

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

        # Motif for -r: point Ikemen at THIS game's screenpack (its select.def,
        # chars, stages), not the bundled kfm720. Sources, in priority order:
        #   1. save/config.json .Motif -- the authoritative per-game motif every
        #      already-played game was set up with, and correct even where fresh
        #      detection is NOT (verified 2026-07-19: MvC2 needs data/Mvc2/system.def
        #      but detection yields data/system.def). READ-ONLY: the current Ikemen
        #      build ignores config.json (it reads config.ini), so we never write
        #      it -- it survives purely as this motif record. (config.ini's own
        #      [Config] Motif is unusable here: -r overrides it at runtime without
        #      persisting, so on disk it is a stale engine default.)
        #   2. else detect data/mugen.cfg's Motif= (Mugen 1.0 games), falling back
        #      to data/system.def -- for a game that has no config.json yet.
        # (The old first-run config.json creation + every-launch FullscreenWidth/
        # Height retune were removed: they wrote ONLY to config.json, which the
        # current engine does not read, so they were dead against this build.)
        motif=""
        if [[ -f save/config.json ]]; then
            motif=$(jq -r '.Motif // empty' save/config.json 2>/dev/null)
            [[ -n $motif && ! -f $motif ]] && motif=""     # stale path -> re-detect
        fi
        if [[ -z $motif ]]; then
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
        fi

        # If the binary has already created save/config.ini (from a previous
        # launch), default-set RenderMode to Vulkan 1.3 once. Marker prevents
        # overriding subsequent user choices via the in-game video menu.
        if [[ -f save/config.ini && ! -e save/.deck-vulkan-set ]]; then
            sed -i 's|^RenderMode[[:space:]]*=.*|RenderMode              = Vulkan 1.3|' save/config.ini
            touch save/.deck-vulkan-set
        fi

        # --- controller merger: canonical twins in OUR seat order --------------
        # Reuse of the OpenBOR pad pipeline (mad-openbor-pads.py --backend mugen),
        # here on NATIVE SDL2. The merger grabs the configured player pads and emits
        # one recognised canonical GameController per player; Ikemen is whitelisted to
        # see ONLY the twins, so seats follow [backends.mugen].pad_classes, the X-Arcade
        # is de-rotated, and stick+d-pad both drive movement. No player pad connected
        # (--probe exits non-zero) -> raw pads, exactly as before.
        MERGER_PID=""
        CANON=0
        if (cd "$SELF_DIR" && python3 mad-openbor-pads.py --backend mugen --probe) >> "$log" 2>&1; then
            READY_F="$(mktemp)"
            # exec so python is THIS script's direct child: the merger's PR_SET_PDEATHSIG
            # then binds to us, so a death that skips the trap (SIGKILL/SIGHUP) still takes
            # the merger down -- it can never orphan with the pads grabbed (rig-wide mute).
            (cd "$SELF_DIR" && exec python3 mad-openbor-pads.py --backend mugen > "$READY_F" 2>> "$log") &
            MERGER_PID=$!
            # Arm the trap BEFORE the READY wait: until it exists a TERM here leaks the merger.
            trap 'kill ${game_pid:-} ${MERGER_PID:-} 2>/dev/null' TERM INT
            # The twins must EXIST before Ikemen's startup pad scan.
            for _ in $(seq 1 80); do
                grep -q READY "$READY_F" 2>/dev/null && break
                kill -0 "$MERGER_PID" 2>/dev/null || break
                sleep 0.1
            done
            if grep -q READY "$READY_F" 2>/dev/null; then
                sleep 0.3   # let udev/SDL settle on the new twin nodes
                # Ask the merger for the twin pids so the two can never drift apart.
                TWIN_WL="$(cd "$SELF_DIR" && python3 -c 'import importlib.util as u; s = u.spec_from_file_location("p", "mad-openbor-pads.py"); m = u.module_from_spec(s); s.loader.exec_module(m); print(m.sdl_whitelist())' 2>>"$log")"
                # CRITICAL (found on-device 2026-07-19): Ikemen bundles its OWN
                # SDL 2.0.18 (lib/libSDL2-2.0.so.0.18.2), whose HIDAPI driver reads
                # gamepads straight from hidraw -- which bypasses BOTH the whitelist
                # below AND the merger's evdev EVIOCGRAB. So with HIDAPI on, Ikemen
                # opened the LIVE DualSense and bound to its analog stick instead of
                # the twin (config went GUID=054c, down=LS_Y+). Forcing the evdev
                # backend makes the whitelist actually hide the raw pads and makes the
                # grab effective. (openbor.sh sets this too, for the same reason.)
                export SDL_JOYSTICK_HIDAPI=0
                export SDL_GAMECONTROLLER_IGNORE_DEVICES_EXCEPT="${TWIN_WL:-0x4d41/0x0002,0x4d41/0x0003,0x4d41/0x0004,0x4d41/0x0005}"
                # Belt-and-suspenders: also block Steam's virtual Deck pad.
                export SDL_GAMECONTROLLER_IGNORE_DEVICES="0x28de/0x11ff,0x28de/0x1205"
                # LOAD-BEARING mapping: drops the analog-stick axes so Ikemen reads the
                # left stick purely as the D-PAD (the merger digitises stick -> hat ->
                # DP_*). GUIDs are computed by IKEMEN's SDL 2.0.18 (vid/pid only, no name
                # hash), so they are stable; regenerate the file against the bundled SDL
                # if that engine is replaced. A GUID mismatch no-ops to auto-recognition
                # (analog stick reappears, still playable). Verified 2026-07-19.
                [[ -f "$SELF_DIR/data/mugen-twins.gamecontrollerdb" ]] && \
                    export SDL_GAMECONTROLLERCONFIG="$(cat "$SELF_DIR/data/mugen-twins.gamecontrollerdb")"
                CANON=1
                echo "pads: merger READY (pid $MERGER_PID) -- Ikemen sees twins only" >> "$log"
            else
                echo "pads: merger failed to signal READY -- falling back to raw pads" >> "$log"
                kill "$MERGER_PID" 2>/dev/null
                MERGER_PID=""
            fi
            rm -f "$READY_F"
        else
            echo "pads: no configured player pads -- raw pads (handheld/solo)" >> "$log"
        fi

        # Canonical joystick config ONLY when Ikemen will see the twins: the standard
        # binding is correct for the twin but wrong for a raw pad, so never on the
        # fallback path. The engine rewrites config.ini on exit; this is the session's truth.
        if [ "$CANON" -eq 1 ]; then
            (cd "$SELF_DIR" && python3 -m lib.mugen_cfg apply "$target/save/config.ini") >> "$log" 2>&1 \
                || echo "mugen_cfg apply crashed -- launching with config.ini as-is" >> "$log"
        fi

        # On-the-go: downshift the render resolution in HANDHELD (config.ini GameWidth/
        # Height, aspect-preserving) to save battery; no-op docked / feature-off. Restored
        # after the engine exits (below); a leftover downshift from a crash is swept on the
        # next apply. Keyed by the game folder.
        (cd "$SELF_DIR" && python3 -m lib.mugen_res apply "$(basename "$target")" \
            "$target/save/config.ini") >> "$log" 2>&1 || true

        # Launch Ikemen in the BACKGROUND (not exec): mugen.sh must stay alive as the
        # merger's parent (for PDEATHSIG) and to watchdog it. `kill $game_pid` really
        # stops a native child (unlike openbor.sh's Proton fork, which needs wineserver -k).
        if [[ -n $motif && -f $motif ]]; then
            echo "launching with -r $motif" >> "$log"
            "$ikemen_home/Ikemen_GO_Linux" -r "$motif" "${@:3}" >>"$log" 2>&1 &
        else
            "$ikemen_home/Ikemen_GO_Linux" "${@:3}" >>"$log" 2>&1 &
        fi
        game_pid=$!

        stop_game() { kill "$game_pid" 2>/dev/null || true; }
        # Put the resting GameWidth/Height back after the engine exits (it rewrote
        # config.ini with the handheld downshift). No-op when nothing was downshifted.
        hhres_restore() { (cd "$SELF_DIR" && python3 -m lib.mugen_res restore \
            "$target/save/config.ini") >> "$log" 2>&1 || true; }
        # Re-arm now that game_pid exists (the pre-READY trap only knew the merger).
        trap 'stop_game; kill ${MERGER_PID:-} 2>/dev/null' TERM INT

        if [ -n "$MERGER_PID" ]; then
            # Wait on BOTH. If the merger dies first the twins vanish and the raw pads
            # stay hidden -> an input-dead game in Game Mode; killing it lands the user
            # back in ES-DE (the kinder failure). Losing a pad does NOT kill the merger
            # (it holds the twins and waits for one to return -- see mad-openbor-pads.pump),
            # so reaching here means it genuinely crashed.
            wait -n "$game_pid" "$MERGER_PID"
            if kill -0 "$game_pid" 2>/dev/null; then
                echo "pads: merger died first -- stopping the game (it would have no input)" >> "$log"
                stop_game
            fi
            wait "$game_pid" 2>/dev/null
            rc=$?
            kill "$MERGER_PID" 2>/dev/null
            wait "$MERGER_PID" 2>/dev/null
            hhres_restore
            exit $rc
        fi
        wait "$game_pid"
        rc=$?
        hhres_restore
        exit $rc
        ;;

    native)
        [[ ! -f $target ]] && { echo "Not a file: $target" >&2; exit 66; }
        game_dir=$(dirname "$target")
        cfg="$game_dir/save/config.ini"
        # On-the-go: downshift the render resolution in HANDHELD (aspect-preserving), restored
        # after the binary exits. Native runs on RAW pads (no merger); the folder key is the game
        # dir name, matching MAD's on-the-go store (mugen_onthego._folder). No-op docked /
        # feature-off / if the binary ignores config.ini (harmless).
        (cd "$SELF_DIR" && python3 -m lib.mugen_res apply "$(basename "$game_dir")" "$cfg") \
            >> "$log" 2>&1 || true
        cd "$game_dir"
        chmod +x "./$(basename "$target")" 2>/dev/null || true
        # Background (not exec) so mugen.sh survives to restore the resting resolution on exit.
        "./$(basename "$target")" "${@:3}" >>"$log" 2>&1 &
        game_pid=$!
        trap 'kill "$game_pid" 2>/dev/null || true' TERM INT
        wait "$game_pid"; rc=$?
        (cd "$SELF_DIR" && python3 -m lib.mugen_res restore "$cfg") >> "$log" 2>&1 || true
        exit $rc
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
