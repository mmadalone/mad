# GitHub Actions — building/publishing the ES-DE-MAD AppImage (cached findings)

Cached 2026-06-08 while implementing #48 (CI build of the patched ES-DE AppImage).
Sources: GitHub Actions docs + GitHub REST API docs (URLs below), verified against
the local repo/Deck state.

## Runner & glibc (the whole ballgame)
- Pin **`runs-on: ubuntu-22.04`** = glibc **2.35**. SteamOS 3.7 = glibc **2.41**.
  glibc is **backward**-compatible, not forward — a binary linked against an OLDER
  glibc runs on a newer one, so build on the older runner. Matches our local
  `esde-ubuntu` distrobox (also 22.04) exactly.
- **Never `ubuntu-latest`** — it now maps to **24.04 (glibc 2.39)**: smaller margin
  and silently advances over time. (Jammy apt package names in `ubuntu-build.sh`
  also only resolve on 22.04.)
- Runner images: https://github.com/actions/runner-images (date-check the ubuntu-latest mapping)

## Triggers in a TWO-TREE repo (default branch `main`, build tree `deck-patches`)
- **`workflow_dispatch`** (the "Run workflow" button / REST dispatch) only surfaces
  if the workflow file exists on the repo's **DEFAULT branch**. So the file must be
  on `main`. The job can then `actions/checkout` a *different* ref (`deck-patches`)
  and build that.
- **`push:` trigger** reads the workflow definition **from the pushed branch's commit**
  — so auto-build-on-push to `deck-patches` requires the file to also live ON
  `deck-patches`. ⇒ keep an **identical copy on both branches**; the job always pins
  `ref: deck-patches` so it builds the fork regardless of which copy fired.
- Docs: https://docs.github.com/en/actions/reference/events-that-trigger-workflows#workflow_dispatch
- Manual dispatch via REST (no `gh`):
  `curl -X POST -H "Authorization: Bearer $PAT" -H "Accept: application/vnd.github+json" \`
  `  https://api.github.com/repos/OWNER/REPO/actions/workflows/<file>.yml/dispatches -d '{"ref":"main"}'`
  (`ref` = the branch the YAML lives on, e.g. `main`.)

## Publishing the build (Release, not artifact)
- Use a **GitHub Release** asset, not `actions/upload-artifact` (artifacts expire ≤90d,
  count against a 500 MB free quota, download as a zip). Releases are permanent, 2 GiB/file.
- `softprops/action-gh-release@v2` creates the tag/release if absent and **overwrites
  assets on re-run** — perfect for a single **rolling tag** (`latest-steamdeck`) the Deck
  always pulls via `/releases/latest`. Needs job `permissions: contents: write` AND the
  repo Setting **Actions → General → Workflow permissions = "Read and write"** (else the
  publish step 403s). Uses the auto `GITHUB_TOKEN` — no PAT in the workflow.
- Action: https://github.com/softprops/action-gh-release

## Downloading a PRIVATE release asset onto the Deck (curl, NO gh/jq)
- `gh` is not installed; **`jq` IS a pacman package (`/usr/bin/jq`) → wiped by a SteamOS
  update**, the very event that creates the need. So parse JSON with **`python3`** (always
  present), never jq.
- Two steps:
  1. `GET /repos/OWNER/REPO/releases/latest` with `Authorization: Bearer <PAT>` +
     `Accept: application/vnd.github+json` → find the asset **id by name** (python3).
  2. `GET /repos/OWNER/REPO/releases/assets/<id>` with **`Accept: application/octet-stream`**
     → this returns a **302 to a signed URL**. THE GOTCHA: default Accept returns JSON
     metadata instead of the binary. Use the **asset API `url`/`id`**, NOT
     `browser_download_url` (that needs an interactive cookie, not a bearer PAT).
  - SteamOS `curl 8.11.1` correctly **strips the Authorization header on cross-host
    redirect** to S3, so a single `curl -L` form is safe.
- PAT: **fine-grained**, scoped to the one repo, **Contents: Read** only (least privilege).
  Store at `~/.config/mad/gh-token`, `chmod 600` (under /home → survives OS updates).
  Never commit it.
- Docs: https://docs.github.com/en/rest/releases/assets#get-a-release-asset

## Free minutes / cost
- Private-repo Actions free tier ≈ **2000 min/month**. A clean build (SDL from source +
  full ES-DE) ≈ **25–45 min**. Builds happen only a few times a year (after an upstream
  ES-DE rebase) → cost is a non-issue.

## Recipe quirks worth knowing (tools/create_AppImage_SteamDeck.sh)
- It **downloads** appimagetool (pkgforge-dev/uruntime) + linuxdeploy via `wget`, **clones
  + builds SDL** `release-2.32.10`, and **aborts (bare `exit`, status 0!) if SDL lacks
  PipeWire** → CI must assert `test -f ES-DE_x64_SteamDeck.AppImage` after calling it, or a
  recipe abort goes GREEN.
- `linuxdeploy -l` bundles `libOpenGL.so.0`, `libGLdispatch.so.0` (libopengl0 + libglvnd0),
  `libgio-2.0.so.0` (libglib2.0-0) — name these in apt explicitly to stay robust.
- Output AppImage is **DwarFS/uruntime** (not squashfs); first launch may extract to TMPDIR.
- `APPIMAGE_EXTRACT_AND_RUN=1` needed (no usable FUSE on GH runners). `-DSTEAM_DECK=on`
  is inside the recipe.
- **Do NOT cache `external/SDL/build`** with a fixed key: a single existing `.so` is taken
  as proof of a good SDL, but a cache from a PipeWire-less build would be silently bundled.
  Builds are rare → always build clean.
