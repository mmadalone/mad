#!/usr/bin/env bash
# Two ALWAYS-ON drift canaries, run once per ES-DE start from esde-health-check.sh (which runs
# from esde-splash-gen.sh, which every ES-DE.AppImage wrapper calls). Best-effort throughout:
# this script can never block, slow or fail a launch, and always exits 0.
#
# Deliberately SEPARATE from esde-health-check.sh's own job. That script is gated on the SteamOS
# BUILD_ID, so its checks run only after an actual OS update - exactly wrong for these two, whose
# triggers (an EmuDeck run, an emulator update) never touch BUILD_ID. Keeping them here also keeps
# them out of `deck-post-update.sh --check`, which matters more than it looks: ANY unfiltered line
# in that output is read as "a SteamOS update wiped a component" and arms an in-panel offer to run
# a sudo reapply, which cannot fix either of these. That is verbatim the unclearable-nag bug the
# script-skew mechanism had to be rescued from (see lib/version_skew.py and
# esde-health-check.sh's _check_no_skew filter).
#
# CANARY 1 - EmuDeck silently un-patching the emulator launchers.
#   EmuDeck's installEmuBI.sh and helperFunctions.sh flushEmulatorLaunchers() both `rm -f` the
#   deployed launcher and `cp` their own template over it, straight into ${toolsPath}/launchers,
#   which IS this git clone. It happens on any emulator (re)install or update through EmuDeck.
#   MAD's patched stubs lose their lib resolver, their emudeck-shim fallback and mad-paths.sh, so
#   launches quietly lose splash and router integration with no error anywhere. Nothing detected it.
#
# CANARY 2 - an emulator changing its config format under MAD's writers. See lib/emu_fingerprint.py
#   for the reasoning; this half only shows the result and records the acknowledgement.
#
# Test affordances: $HOME (everything is HOME-relative), $MAD_EMUDECK_TEMPLATES (the template dir),
# $MAD_DATA_ROOT (where the fingerprint state lives, via lib/mad_paths.py).
set -uo pipefail
L="$HOME/Emulation/tools/launchers"
[ -d "$L" ] || exit 0
TS="$(date +%Y%m%d-%H%M%S)"
log(){ echo "[drift-check] $*"; }

# Gamepad-navigable fullscreen dialog (30s auto-proceed, so it can never trap a launch). Returns
# the dialog's exit code: 0 = Proceed, 1 = Cancel, 3 = tkinter unavailable.
_ask(){ ( cd "$L" && DISPLAY="${DISPLAY:-:0}" python3 -m lib.warning_dialog "$1" "$2" ) >/dev/null 2>&1; }

# ── canary 1: reverted EmuDeck stubs ────────────────────────────────────────────────────────────
# The candidate set is DERIVED, never a hand-kept list: every EmuDeck template that has a
# same-named file at the clone root. That automatically covers a stub for an emulator installed
# later, and automatically skips the 40-odd MAD-only root scripts EmuDeck knows nothing about.
_stub_check(){
    local tmpl="${MAD_EMUDECK_TEMPLATES:-$HOME/.config/EmuDeck/backend/tools/launchers}"
    [ -d "$tmpl" ] || return 0                 # no EmuDeck on this rig: nothing to revert
    command -v git >/dev/null 2>&1 || return 0 # no git: nothing here is restorable anyway
    local t n names=() cands=() tracked suspect reverted=() failed=() arc
    for t in "$tmpl"/*.sh; do
        [ -e "$t" ] || continue                # unmatched-glob guard
        n="${t##*/}"
        [ -f "$L/$n" ] || continue
        # A tracked SYMLINK (citra.sh and lime3ds.sh point at azahar.sh) is never its own
        # reversion: grep and cmp both read THROUGH it, so it mirrors whatever azahar.sh currently
        # says and would only add a second confusing warning about the same one clobber.
        [ -L "$L/$n" ] && continue
        names+=("$n")
    done
    [ "${#names[@]}" -gt 0 ] || return 0
    # TRACKED-ONLY, and this is the load-bearing half of the candidate test, not a nicety.
    # EmuDeck ships templates for emulators MAD has never patched (citron.sh, eden.sh, yuzu.sh,
    # bigpemu.sh, ...). Install one through EmuDeck and it drops that template into this clone: no
    # _MAD_LIB marker, byte-identical to the template, and UNTRACKED. Without this test it would be
    # classified as a reversion, fail to restore (git has never heard of it), and raise a blocking
    # dialog at EVERY ES-DE start forever, telling the owner routing is broken when it is not and
    # handing them a git command that errors. That is precisely the unclearable-nag failure mode
    # this file's header says the design avoids.
    # ONE git call for the whole set, not one per file: per-file --error-unmatch cost 22 forks and
    # measured 118 ms against 50 ms for this shape, on a path that runs before ES-DE draws.
    tracked="$(git -C "$L" ls-files -z -- "${names[@]}" 2>/dev/null | tr '\0' '\n')"
    for n in "${names[@]}"; do
        case $'\n'"$tracked"$'\n' in
            *$'\n'"$n"$'\n'*) cands+=("$L/$n") ;;
        esac
    done
    [ "${#cands[@]}" -gt 0 ] || return 0
    # ONE grep for the whole set (measured ~7 ms; 22 separate `cmp` calls cost ~56 ms, and this
    # runs on every ES-DE start). `_MAD_LIB` is an exact discriminator, verified both directions:
    # every MAD-patched stub has it and no EmuDeck template does. grep -L prints the files that do
    # NOT contain it, and exits 1 when there are none, hence the `|| true`.
    while IFS= read -r suspect; do
        [ -n "$suspect" ] || continue
        n="${suspect##*/}"
        # Confirm it really is EmuDeck's file and not a half-written or hand-edited one: only a
        # byte-for-byte match with the template proves a reversion. Anything else is left alone and
        # merely reported, because restoring over a file someone is editing would destroy work.
        if ! cmp -s "$suspect" "$tmpl/$n"; then
            log "WARN: $n has no MAD marker but does not match EmuDeck's template either; left alone"
            continue
        fi
        # Rule 5: the bytes we are about to overwrite go to _TMP FIRST, even though they are
        # provably identical to a file EmuDeck still holds. If that archive cannot be written
        # (read-only ~/Downloads, full disk) we do NOT proceed: overwriting without a recoverable
        # copy would void the guarantee this project makes about every destructive step.
        arc="$HOME/Downloads/_TMP/$TS-emudeck-stub-revert"
        if ! mkdir -p "$arc" 2>/dev/null || ! cp -f "$suspect" "$arc/$n" 2>/dev/null; then
            failed+=("$n"); log "WARN: cannot archive $n to $arc; leaving it alone"
            continue
        fi
        if [ ! -s "$arc/RECOVERY.txt" ]; then
            # The whole block runs in a subshell so a redirection failure cannot escape the
            # 2>/dev/null (redirections are evaluated left to right, so a failing `> file` on the
            # OUTER command would print before the discard takes effect).
            (
              {
                printf 'EmuDeck reverted MAD-patched launcher stub(s) to its own templates.\n'
                printf 'Detected %s by mad-drift-check.sh; the MAD versions were restored from git.\n\n' "$(date)"
                printf 'The files here are EmuDeck template copies, kept only so nothing was deleted.\n'
                printf 'To put them back (you almost certainly do not want to):\n'
                printf '  cp "%s"/<name>.sh "%s"/\n' "$arc" "$L"
                printf 'To re-apply the MAD versions again at any time:\n'
                printf '  git -C "%s" checkout -- <name>.sh\n' "$L"
              } > "$arc/RECOVERY.txt"
            ) 2>/dev/null || true
        fi
        # VERIFY THE POST-CONDITION, do not trust the exit code. `git checkout -- <path>` restores
        # from the INDEX, not from HEAD, and exits 0 even when it writes nothing. If the reverted
        # stub was ever staged (a routine `git add -A` sweep does it), the checkout is a silent
        # no-op: the stub stays EmuDeck's, splash and routing stay broken, and the canary would
        # report a successful repair on every single start while archiving another _TMP dir each
        # time. Checking that the marker is actually back is the only honest test.
        # Deliberately NOT `git checkout HEAD -- <path>`: that rewrites the index too and would
        # discard a genuine staged MAD change.
        if git -C "$L" checkout -- "$n" >/dev/null 2>&1 && grep -q -- '_MAD_LIB' "$suspect" 2>/dev/null; then
            reverted+=("$n")
        else
            failed+=("$n")
        fi
    done < <(grep -L -- '_MAD_LIB' "${cands[@]}" 2>/dev/null || true)

    [ "${#reverted[@]}" -eq 0 ] || \
        log "EmuDeck had reverted ${#reverted[@]} launcher stub(s): ${reverted[*]} - restored from git (old copies in $HOME/Downloads/_TMP/$TS-emudeck-stub-revert)"
    if [ "${#failed[@]}" -gt 0 ]; then
        # Self-healing failed, so this is the one case the owner has to know about. Reachable only
        # for a TRACKED, non-symlink stub that git refused to restore or that came back without its
        # marker (an unmerged path mid-conflict, a staged reversion, an unwritable _TMP). An
        # untracked EmuDeck drop can no longer land here, which is what keeps this from becoming a
        # dialog on every start.
        log "ERROR: could not restore ${failed[*]}"
        _ask "Emulator launchers were reset" \
"An EmuDeck update replaced some of MAD's emulator launch scripts
with its own, and they could not be restored automatically:

  ${failed[*]}

Games will still start, but splash screens and controller routing
may not work for them. To fix, open Desktop Mode and run:
  git -C ~/Emulation/tools/launchers checkout -- ${failed[*]}"
    fi
}

# ── canary 2: an emulator binary changed ────────────────────────────────────────────────────────
# Proceed IS the acknowledgement: it records the new fingerprint, so the warning is keyed to actual
# state and re-arms by itself on the next real change. Cancel records nothing and asks again next
# start. Neither answer changes anything else, and a first run records silently (see
# lib/emu_fingerprint.check).
_emu_check(){
    local changed rc
    changed="$( cd "$L" && python3 -m lib.emu_fingerprint check 2>/dev/null )" || return 0
    [ -n "$changed" ] || return 0
    log "emulator binaries changed since the last check: $(echo "$changed" | tr '\n' ' ')"
    _ask "Emulators were updated" \
"These emulators changed since MAD last checked:

$changed

MAD writes their controller and settings files directly, so an
update that changes a file format can make inputs or settings
behave oddly. If something is wrong in one of them, re-check its
controller setup in the MAD panel first.

Press Proceed to acknowledge (you will not be asked again until
the next update)."
    rc=$?
    # 0 = Proceed, 3 = tkinter unavailable (nothing was shown, so nagging forever helps nobody and
    # the log line above already carries the information). 1 = Cancel: ask again next start.
    if [ "$rc" -eq 0 ] || [ "$rc" -eq 3 ]; then
        ( cd "$L" && python3 -m lib.emu_fingerprint record ) >/dev/null 2>&1 || true
    fi
}

_stub_check || true
_emu_check  || true
exit 0
