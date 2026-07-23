#!/usr/bin/env bash
# ============================================================================
# deck-cloud-setup.sh - ONE-TIME setup connecting this Deck to MEGA S4 (S3
# object storage) for cloud backup. Uses your S4 ACCESS KEYS - no interactive
# MEGA password, no browser-login step, no backup password. Re-runnable.
#
# Both tiers are plain BROWSABLE rclone copies into S4 (you can see your files
# in the MEGA web UI):
#   Tier A (saves + configs) -> s4:<bucket>/precious/  (+ a version net)
#   Tier B (ROMs/media/...)  -> s4:<bucket>/library/   (manual "Sync library now")
#
# Prereq: S4 access keys saved (chmod 600) at $S4_CREDS as:
#     aws_access_key_id=YOURKEY
#     aws_secret_access_key=YOURSECRET
# (Create them in MEGA: S4 -> Access keys. The bucket defaults to 'steamdeck'.)
# ============================================================================
set -euo pipefail

BIN="$HOME/Emulation/tools/bin"; RCLONE="$BIN/rclone"
_default_s4_creds(){
    local c a s
    for c in "$HOME/.ssh/credentials-steamdeck" "$HOME/.config/deck-cloud/credentials-steamdeck" \
             "$HOME/.claude/tokens/credentials-steamdeck" "$HOME/Emulation/tools/tokens/mega-steamdeck"; do
        [[ -f "$c" ]] || continue
        a="$(grep -E '^aws_access_key_id' "$c" 2>/dev/null | cut -d= -f2- | tr -d ' \t\r')" || true
        s="$(grep -E '^aws_secret_access_key' "$c" 2>/dev/null | cut -d= -f2- | tr -d ' \t\r')" || true
        [[ -n "$a" && -n "$s" ]] && { printf '%s' "$c"; return; }
    done
    printf '%s' "$HOME/.ssh/credentials-steamdeck"
}
S4_CREDS="${DECK_CLOUD_CREDS:-$(_default_s4_creds)}"
S4_BUCKET="${DECK_CLOUD_BUCKET:-steamdeck}"
S4_ENDPOINT="${DECK_CLOUD_ENDPOINT:-https://s3.g.s4.mega.io}"
S4_REGION="${DECK_CLOUD_REGION:-eu-central-1}"
RCLONE_REMOTE="${DECK_CLOUD_RCLONE_REMOTE:-s4}"
RCLONE_CONF="${RCLONE_CONFIG:-$HOME/.config/rclone/rclone.conf}"
STATE_DIR="${DECK_CLOUD_STATE_DIR:-$HOME/.config/deck-cloud}"
ENGINE="$HOME/Emulation/tools/launchers/deck-cloud.sh"

# Optional: which MEGA S4 server to use. Override with --server <key> or DECK_CLOUD_SERVER
# (global|amsterdam|luxembourg|paris|barcelona|montreal|vancouver|tokyo). Default to the
# ALREADY-SAVED choice (so re-running setup does not clobber a server picked in the MAD
# panel), else 'global'. The choice is saved so the MAD panel + engine use the same one.
_saved_server(){ [[ -f "$STATE_DIR/server" ]] && tr -d ' \t\r\n' < "$STATE_DIR/server" 2>/dev/null || true; }
SERVER="${DECK_CLOUD_SERVER:-$(_saved_server)}"; SERVER="${SERVER:-global}"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --server=*) SERVER="${1#*=}"; shift;;
        --server)   SERVER="${2:?--server needs a key}"; shift 2;;
        *)          shift;;
    esac
done

say(){ printf '\n=== %s ===\n' "$*"; }
ok(){  printf '  [ok] %s\n' "$*"; }
err(){ printf '  [!!] %s\n' "$*" >&2; }

[[ -x "$RCLONE" ]] || { err "rclone not found at $RCLONE"; exit 1; }

cat <<INTRO

  MEGA S4 cloud backup - one-time setup
  -------------------------------------
  Connects this Steam Deck to your MEGA S4 object storage (bucket '$S4_BUCKET')
  using your S4 access keys. Re-runnable; it skips whatever is already done.
INTRO

# --- 1. S4 access keys -------------------------------------------------------
say "Step 1 of 3: S4 access keys"
if [[ ! -f "$S4_CREDS" ]]; then
    err "No S4 credentials at: $S4_CREDS"
    err "Create S4 access keys in MEGA (S4 -> Access keys), save them there as two lines"
    err "  aws_access_key_id=YOURKEY / aws_secret_access_key=YOURSECRET, then chmod 600 it."
    exit 1
fi
chmod 600 "$S4_CREDS" 2>/dev/null || true
# || true so a MISSING key (grep no-match, non-zero under set -o pipefail) doesn't kill the
# script silently before the friendly guard below.
AK="$(grep -E '^aws_access_key_id' "$S4_CREDS" | cut -d= -f2- | tr -d ' \t\r')" || true
SK="$(grep -E '^aws_secret_access_key' "$S4_CREDS" | cut -d= -f2- | tr -d ' \t\r')" || true
[[ -n "$AK" && -n "$SK" ]] || { err "creds file missing aws_access_key_id / aws_secret_access_key"; exit 1; }
export AWS_ACCESS_KEY_ID="$AK" AWS_SECRET_ACCESS_KEY="$SK" AWS_DEFAULT_REGION="$S4_REGION"
ok "found S4 keys (access id ...${AK: -4}), file locked (chmod 600)"

# --- 2. rclone S4 remote. env_auth = the secret is NOT stored in rclone.conf ---
say "Step 2 of 3: rclone S4 remote '$RCLONE_REMOTE'"
mkdir -p "$(dirname "$RCLONE_CONF")"
"$RCLONE" --config "$RCLONE_CONF" config create "$RCLONE_REMOTE" s3 \
    provider=Other env_auth=true endpoint="$S4_ENDPOINT" region="$S4_REGION" >/dev/null
chmod 600 "$RCLONE_CONF" 2>/dev/null || true
ok "rclone remote '$RCLONE_REMOTE' -> $S4_ENDPOINT (creds via env, not stored in config)"

say "Testing the connection to S4 (bucket '$S4_BUCKET')"
if timeout 60 "$RCLONE" --config "$RCLONE_CONF" lsd "${RCLONE_REMOTE}:${S4_BUCKET}" >/dev/null 2>/tmp/deck-s4.err; then
    ok "connected to S4 bucket '$S4_BUCKET'"
else
    err "Could not reach S4 bucket '$S4_BUCKET':"; sed 's/^/       /' /tmp/deck-s4.err >&2 || true
    err "Check the keys and that the bucket exists. To create it:"
    err "    $RCLONE --config $RCLONE_CONF mkdir ${RCLONE_REMOTE}:${S4_BUCKET}"
    exit 1
fi

# --- 3. server choice + defaults --------------------------------------------
say "Step 3 of 3: server + defaults"
mkdir -p "$STATE_DIR"; : > "$STATE_DIR/onexit.enabled"
# Save (and live-test) the chosen MEGA S4 server. 'global' is the endpoint tested above;
# a regional pick is reachability-checked here. All servers reach the same bucket, so this
# only changes the route - never the data.
if ! bash "$ENGINE" set-server "$SERVER"; then
    err "could not select server '$SERVER' - leaving the default (Global auto-route)"
fi
bash "$ENGINE" ensure-units >/dev/null 2>&1 || true
ok "back up saves when you quit a game: ON (change it in the MAD panel any time)"

cat <<DONE

  Setup complete.
  ---------------
  - Saves + configs back up automatically when you quit a game, as BROWSABLE files
    under s4:${S4_BUCKET}/precious/ (overwritten files kept under precious-versions/).
  - In the MAD panel: toggle during-play syncing, "Back up now", "Sync library
    now", or restore.
  - FIRST LIBRARY UPLOAD (ROMs/media, large): run when convenient, plugged in:
        $ENGINE sync-library
DONE
