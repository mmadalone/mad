#!/usr/bin/env bash
# ============================================================================
# deck-fetch-esde.sh — download the CI-built patched ES-DE-MAD AppImage from the
# PRIVATE GitHub repo's rolling release and install it to ~/Applications/.
#
# This is the fast recovery path after a SteamOS/EmuDeck update wipes the local
# build: instead of a ~30-min rebuild in the esde-ubuntu distrobox, pull the
# AppImage that GitHub Actions already built (see .github/workflows/build-appimage.yml).
#
# Uses ONLY curl + python3 — deliberately NOT jq (jq is a pacman package on the
# immutable root, so it's wiped by the very update that makes us need this).
#
# Auth is OPTIONAL: the repo is public, so this works anonymously. If the repo is
# ever made private, drop a fine-grained PAT (Contents:Read on mmadalone/mad) at the
# token path below (chmod 600) and it's used automatically. A file that isn't a GitHub
# token (e.g. a leftover SSH key) is ignored, so it can't break anonymous access.
#
# Never deletes: an existing build is moved to a recoverable _TMP dir (+ RECOVERY.txt)
# before the new one is installed, so a bad CI build can be rolled back instantly.
#
# Usage:  deck-fetch-esde.sh [--force]
#   --force  reinstall even if the latest release matches the installed build.
# Exit: 0 installed / already current · 1 fetch or sanity failure.
# ============================================================================
set -uo pipefail

REPO="mmadalone/mad"
ASSET="ES-DE-MAD.AppImage"
# The patched-AppImage rolling build lives on a FIXED release tag — the C++ in-app
# updater (ApplicationUpdater.cpp) and CI reference it the same way. Fetch by tag,
# NOT /releases/latest, so versioned human releases (v0.2.0, …) can never change
# which build the Deck downloads.
RELEASE_TAG="latest-steamdeck"
TOKEN_FILE="${MAD_GH_TOKEN_FILE:-$HOME/.config/mad/gh-token}"
DEST="$HOME/Applications/ES-DE-MAD.AppImage"
API="https://api.github.com/repos/$REPO"
MIN_BYTES=$((60 * 1024 * 1024))     # sanity floor; a real build is ~120 MB

FORCE=0
[ "${1:-}" = "--force" ] || [ "${1:-}" = "-f" ] && FORCE=1

log(){ echo "[fetch-esde] $*"; }
TMP=""; SHATMP=""
cleanup(){ [ -n "$TMP" ] && rm -f "$TMP" 2>/dev/null; [ -n "$SHATMP" ] && rm -f "$SHATMP" 2>/dev/null; }
trap cleanup EXIT

# --- auth (optional) ---
# Public repo => anonymous works. Use a token ONLY if the file holds a real GitHub
# PAT; ignore anything else (e.g. an SSH key) so it can't 401 the public requests.
TOKEN=""
if [ -r "$TOKEN_FILE" ]; then
  _t="$(tr -d ' \t\r\n' < "$TOKEN_FILE")"
  case "$_t" in
    ghp_*|gho_*|ghs_*|ghu_*|github_pat_*) TOKEN="$_t"; log "using PAT from $TOKEN_FILE" ;;
    "") ;;
    *) log "note: $TOKEN_FILE isn't a GitHub PAT — ignoring it (anonymous access)." ;;
  esac
fi
AUTH=(-H "X-GitHub-Api-Version: 2022-11-28")
[ -n "$TOKEN" ] && AUTH+=(-H "Authorization: Bearer $TOKEN")

# --- rolling-release metadata (by fixed tag, not /releases/latest) ---
log "querying the '$RELEASE_TAG' release of $REPO ..."
REL="$(curl -fsSL "${AUTH[@]}" -H "Accept: application/vnd.github+json" "$API/releases/tags/$RELEASE_TAG")" \
  || { log "release query failed (token scope/expiry? network?)."; exit 1; }

# --- resolve asset ids by NAME via python3 (prints: '<appimage_id>\t<sha256_id>') ---
IDS="$(printf '%s' "$REL" | python3 -c '
import sys, json
d = json.load(sys.stdin)
assets = {a["name"]: a["id"] for a in d.get("assets", [])}
name = sys.argv[1]
print("%s\t%s" % (assets.get(name, ""), assets.get(name + ".sha256", "")))
' "$ASSET" 2>/dev/null)" || { log "could not parse release JSON."; exit 1; }
ASSET_ID="${IDS%%$'\t'*}"
SHA_ID="${IDS##*$'\t'}"

if [ -z "$ASSET_ID" ]; then
  NAMES="$(printf '%s' "$REL" | python3 -c 'import sys,json; print(", ".join(a["name"] for a in json.load(sys.stdin).get("assets",[])) or "(none)")' 2>/dev/null)"
  log "asset '$ASSET' not found in latest release. Available: $NAMES"
  exit 1
fi

# --- download the AppImage (octet-stream header -> 302 to a signed URL; curl 8.x
#     drops the auth header on the cross-host redirect, so this is safe) ---
TMP="$(mktemp "$HOME/Applications/.es-de-mad.XXXXXX")" || { log "mktemp failed."; exit 1; }
log "downloading $ASSET (id $ASSET_ID) ..."
curl -fSL --retry 3 "${AUTH[@]}" -H "Accept: application/octet-stream" \
  "$API/releases/assets/$ASSET_ID" -o "$TMP" \
  || { log "download failed."; exit 1; }

# --- sanity gates ---
SZ="$(stat -c '%s' "$TMP" 2>/dev/null || echo 0)"
[ "$SZ" -ge "$MIN_BYTES" ] || { log "downloaded file too small ($SZ bytes) — aborting."; exit 1; }
file -b "$TMP" | grep -qiE 'ELF|executable' || { log "downloaded file is not an executable — aborting."; exit 1; }

GOT="$(sha256sum "$TMP" | awk '{print $1}')"
if [ -n "$SHA_ID" ]; then
  SHATMP="$(mktemp "$HOME/Applications/.es-de-mad-sha.XXXXXX" 2>/dev/null)"
  if [ -n "$SHATMP" ] && curl -fsSL --retry 3 "${AUTH[@]}" -H "Accept: application/octet-stream" \
       "$API/releases/assets/$SHA_ID" -o "$SHATMP"; then
    WANT="$(awk '{print $1}' "$SHATMP")"
    if [ -n "$WANT" ] && [ "$WANT" != "$GOT" ]; then
      log "sha256 MISMATCH (want $WANT, got $GOT) — corrupt download, aborting."
      exit 1
    fi
    log "sha256 verified ($GOT)"
  else
    log "warning: could not fetch .sha256 companion — proceeding on size+file checks only."
  fi
else
  log "warning: no .sha256 companion asset — proceeding on size+file checks only."
fi

# --- idempotence: skip if the installed build already matches (unless --force) ---
if [ "$FORCE" -eq 0 ] && [ -f "$DEST" ]; then
  CUR="$(sha256sum "$DEST" 2>/dev/null | awk '{print $1}')"
  if [ -n "$CUR" ] && [ "$CUR" = "$GOT" ]; then
    log "installed build already matches the latest release — nothing to do."
    exit 0
  fi
fi

# --- never-delete: back up any existing build, then atomic install ---
if [ -e "$DEST" ]; then
  BK="$HOME/Applications/_TMP-esde-mad-$(date +%Y%m%d-%H%M%S)"
  mkdir -p "$BK"
  mv -f "$DEST" "$BK/ES-DE-MAD.AppImage"
  cat > "$BK/RECOVERY.txt" <<EOF
Previous ~/Applications/ES-DE-MAD.AppImage, replaced by deck-fetch-esde.sh on $(date).
Replacement sha256: $GOT

To roll back:
  mv -f "$BK/ES-DE-MAD.AppImage" "$DEST"
EOF
  log "backed up previous build → $BK (RECOVERY.txt inside)"
fi
chmod 755 "$TMP"        # explicit mode: mktemp makes 600, and `chmod +x` would leave 711
mv -f "$TMP" "$DEST"
TMP=""        # consumed; don't let cleanup remove the installed file
log "installed $DEST ($SZ bytes, sha256 $GOT)"
exit 0
