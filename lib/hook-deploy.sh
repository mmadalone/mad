#!/usr/bin/env bash
# Shared hook-deploy helpers. The ALWAYS-deployed core game-start/end hooks are DERIVED at runtime from
# the masters present in hooks/game-start/ + hooks/game-end/, MINUS the feature-gated hooks listed in
# MAD_GATED_HOOKS. Sourced by install.sh (first install) AND deck-post-update.sh (redeploy after a
# loss). Drop a new always-on hook into hooks/game-{start,end}/ and it is picked up automatically -
# there is NO list to keep in sync.
#
# A hook that must stay OFF unless its feature is enabled MUST be listed in MAD_GATED_HOOKS below, or it
# will start auto-deploying to everyone. install.sh deploys those gated hooks itself, under their
# INSTALL_* component switch. (Trade-off of deriving vs a hand-kept allowlist: a stray / experimental
# .sh left in hooks/game-{start,end}/ would auto-deploy and run on every game launch - so keep those
# two directories curated.)
#
# ES-DE runs EVERY file under ~/ES-DE/scripts/{game-start,game-end}. Each core hook self-filters by
# system and reads its own [policy] switch, so deploying them unconditionally is a harmless no-op when
# the feature is off/absent. Backups go OUT to a _TMP dir, never in-place (a .bak in the scanned tree
# would run as a second hook).

# Feature-gated hooks, each TAGGED with the INSTALL_* switch that owns it. Present in hooks/ but
# deployed only under that gate, so they are EXCLUDED from the derived always-on core set.
# "<INSTALL_GATE>|<rel path>", where rel is relative to BOTH hooks/ and ~/ES-DE/scripts/ (so a
# root-level or system-select/ entry works unchanged - mad_deploy_hook just joins the paths).
#
# ONE source of truth: the exclusion list below AND the gated redeploy both read this, so a hook's
# gate can never again drift from a comment. install.sh reads it too - do not re-list hooks there.
MAD_GATED_HOOK_MAP=(
    # INSTALL_THEME (launch screens). launchscreen-pack.sh is a HARD dependency of both
    # launchscreen hooks (they `source ../launchscreen-pack.sh`), so it ships with them or
    # they break at runtime.
    "INSTALL_THEME|game-start/launchscreen.sh"
    "INSTALL_THEME|game-end/launchscreen.sh"
    "INSTALL_THEME|launchscreen-pack.sh"
    "INSTALL_THEME|system-select/05-record-view.sh"
    # INSTALL_SINDEN (lightgun, incl. the Wii lightgun path)
    "INSTALL_SINDEN|game-start/sinden.sh"
    "INSTALL_SINDEN|game-end/sinden.sh"
    "INSTALL_SINDEN|game-start/dolphin-wii-mode.sh"
    "INSTALL_SINDEN|game-end/wiimote-quit-watcher.sh"
    # The revert half of the transient Classic Controller swap applied by
    # game-start/dolphin-wii-mode.sh, so it shares that hook's gate. It was previously
    # listed as gated but deployed by NOTHING, so the swap was applied and never reverted.
    "INSTALL_SINDEN|game-end/dolphin-wii-cc-restore.sh"
)
# NOTE: game-end/dolphin-gc-restore.sh is deliberately NOT here. It reverts a swap applied by
# controller-router.py (core, unconditional), so gating its revert was simply wrong - it is a
# core hook. It no-ops when no snapshot exists, so deploying it unconditionally is safe.

# Rel paths only, derived - never hand-maintained alongside the map above.
MAD_GATED_HOOKS=()
for _mad_ghe in "${MAD_GATED_HOOK_MAP[@]}"; do MAD_GATED_HOOKS+=("${_mad_ghe#*|}"); done
unset _mad_ghe

# _mad_hook_gated <rel> -> 0 (true) if <rel> is a feature-gated hook, so it is excluded from the core set.
_mad_hook_gated() {
    local rel="$1" g
    for g in "${MAD_GATED_HOOKS[@]}"; do [ "$g" = "$rel" ] && return 0; done
    return 1
}

# mad_gated_hooks [gate] -> print gated hook rel paths, one per line. With a gate name, only that
# gate's hooks; without, all of them. Map order, so output is deterministic.
mad_gated_hooks() {
    local want_gate="${1:-}" e
    for e in "${MAD_GATED_HOOK_MAP[@]}"; do
        [ -z "$want_gate" ] || [ "$want_gate" = "${e%%|*}" ] || continue
        printf '%s\n' "${e#*|}"
    done
}

# mad_core_hooks [hooks_root] -> print the derived always-on core hook rel paths (one per line): every
# hooks/game-{start,end}/*.sh that is NOT feature-gated. hooks_root defaults to this file's sibling
# ../hooks. Glob order (sorted per subdir) so redeploy output and tests are deterministic. A .sh at the
# hooks/ root or in any other subdir (e.g. system-select/) is intentionally NOT a core hook.
mad_core_hooks() {
    local root="${1:-$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)/hooks}"
    local sub f rel
    for sub in game-start game-end; do
        [ -d "$root/$sub" ] || continue
        for f in "$root/$sub"/*.sh; do
            [ -e "$f" ] || continue                 # unmatched-glob guard (empty dir)
            rel="$sub/$(basename "$f")"
            _mad_hook_gated "$rel" || printf '%s\n' "$rel"
        done
    done
}

# mad_deploy_hook <src_root> <rel> <scripts_dir> <bak_root>
# cmp-skip if already current; else back up the live copy OUT to $bak_root (never in-place) and copy
# the master in + chmod +x. Returns: 0 current/deployed, 1 write error, 2 missing source master.
mad_deploy_hook() {
    local src="$1/$2" dst="$3/$2" rel="$2" bak="$4"
    [ -f "$src" ] || return 2
    cmp -s "$src" "$dst" 2>/dev/null && return 0
    mkdir -p "$(dirname "$dst")" || return 1
    if [ -e "$dst" ]; then
        mkdir -p "$bak/$(dirname "$rel")" && cp -f "$dst" "$bak/$rel" || return 1
    fi
    cp -f "$src" "$dst" && chmod +x "$dst"
}

# mad_redeploy_core_hooks <src_root> <scripts_dir> <bak_root>
# (Re)deploy every DERIVED core hook (cmp-skip the current ones). Echoes one line per hook written.
# Returns 1 if any deploy hit a write error (a missing master is a warning, not a hard failure).
mad_redeploy_core_hooks() {
    local src="$1" scripts="$2" bak="$3" h rc=0 n=0
    while IFS= read -r h; do
        [ -n "$h" ] || continue
        cmp -s "$src/$h" "$scripts/$h" 2>/dev/null && continue
        mad_deploy_hook "$src" "$h" "$scripts" "$bak"
        case $? in
            0) echo "redeployed: $h"; n=$((n + 1)) ;;
            2) echo "WARN: hook master missing: $h" ;;
            *) echo "FAILED to redeploy: $h"; rc=1 ;;
        esac
    done < <(mad_core_hooks "$src")
    [ "$n" -eq 0 ] && echo "all core hooks already current"
    return "$rc"
}

# mad_redeploy_gated_hooks <src_root> <scripts_dir> <bak_root>
# (Re)deploy the feature-gated hooks whose INSTALL_* gate is ON, per want(). Echoes one line per
# hook written, plus one "skipped (gate off)" line per disabled gate so a silent no-op is visible.
#
# Deliberately SEPARATE from mad_redeploy_core_hooks: that function's contract - pinned by
# tests/test_hook_redeploy.py - is that a gated hook NEVER appears in its output. Merging the two
# would break that on purpose. Returns 1 if any deploy hit a write error.
mad_redeploy_gated_hooks() {
    local src="$1" scripts="$2" bak="$3" e gate rel rc=0 n=0 skipped=""
    # Fallback FIRST, mirroring deck-post-update.sh: without install-conf.sh sourced, a missing
    # want() must mean "do everything" (legacy). Defining it to fail instead would invert every
    # gate to SKIP, deploying nothing while still reporting success.
    declare -F want >/dev/null 2>&1 || want() { return 0; }
    for e in "${MAD_GATED_HOOK_MAP[@]}"; do
        gate="${e%%|*}"; rel="${e#*|}"
        if ! want "$gate"; then
            case " $skipped " in *" $gate "*) ;; *) skipped="$skipped $gate" ;; esac
            continue
        fi
        cmp -s "$src/$rel" "$scripts/$rel" 2>/dev/null && continue
        mad_deploy_hook "$src" "$rel" "$scripts" "$bak"
        case $? in
            0) echo "redeployed: $rel"; n=$((n + 1)) ;;
            2) echo "WARN: hook master missing: $rel" ;;
            *) echo "FAILED to redeploy: $rel"; rc=1 ;;
        esac
    done
    for gate in $skipped; do echo "skipped (gate off): $gate"; done
    [ "$n" -eq 0 ] && echo "all gated hooks already current or gated off"
    return "$rc"
}
