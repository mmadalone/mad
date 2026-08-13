# MAD / ES-DE update flow - how changes actually reach the Deck

Audited end-to-end 2026-08-06 by a 19-agent workflow with adversarial verification of every
load-bearing claim. Sources are OUR OWN code (not third-party docs), so the citations below are
file:line in the two repos rather than URLs. Upstream ES-DE updater behaviour cross-checked
against the stock 3.4.1 sources in the fork tree (unmodified except where noted "deck-patches").

Repos:
- launchers / scripts: `~/Emulation/tools/launchers` = branch `main` of `mmadalone/mad`
- ES-DE C++ fork:      `~/esde-build/ES-DE`        = branch `deck-patches` of `mmadalone/mad`

Simple-memory: `mad-two-update-paths` (sm `ae4039f6`).

---

## 1. Headline

There are TWO independent delivery paths. Only one of them is automatic, and it carries only
the compiled program.

```
mmadalone/mad
├── deck-patches  -> CI build-appimage.yml -> ES-DE-MAD.AppImage + latest_release.json
│                    -> ES-DE's in-app updater sees it AUTOMATICALLY at startup
└── main          -> tests.yml only. No build, no release, no feed change.
                     -> NOTHING on the Deck ever checks this. Manual git pull only.
```

A script change pushed to `main` produces no artifact the Deck consumes, so the in-app updater
has nothing new to see and never prompts.

---

## 2. Path 1: the C++ fork (automatic)

Trigger and build:
- `.github/workflows/build-appimage.yml` `on:` = `workflow_dispatch` + `push` to `deck-patches`
  with `paths-ignore: '**.md'`. It always checks out `deck-patches` regardless of which branch
  the YAML was read from.
- `MAD_RELEASE_NUMBER` = `github.run_number`, exported as env -> `CMakeLists.txt:332` ->
  `add_compile_definitions`. The upstream AppImage recipe stays verbatim.
- Publishes `ES-DE-MAD.AppImage`, its `.sha256`, and `latest_release.json` to the ROLLING tag
  `latest-steamdeck`. Release body = `git log base/v3.4.1..HEAD` (fork changelog), web only.

Comparison:
- `es-app/src/ApplicationUpdater.cpp:427` -> `mNewVersion = (stoi(releaseNum) > MAD_RELEASE_NUMBER)`.
  Local (non-CI) builds define it as 0, so any CI release supersedes a local build.
- Non-numeric or out-of-range `releaseNum` is skipped with a log warning (deck-patches hardening:
  an uncaught throw on the updater thread would abort at startup).

When the check fires (three gates, all currently pass):
1. compiled in: `es-app/src/main.cpp:1231` `#if defined(APPLICATION_UPDATER)`
2. not started with `--no-update-check` (parsed `main.cpp:370-371`; our wrapper never passes it)
3. `ApplicationUpdaterFrequency != "never"`. Default `"always"` (`es-core/src/Settings.cpp:307`),
   live value on this Deck = `always`, so it checks on EVERY launch. daily/weekly/monthly gate on
   `ApplicationUpdaterLastCheck` (program-internal, `Settings.cpp:414`, written only after a
   SUCCESSFUL check, so a failed check retries next launch).

Called from exactly one site: `main.cpp:1233`, on a `std::thread`. There is NO manual
"check now" anywhere in ES-DE. Cancelling means no updater again until relaunch.

UI controls: Main menu > OTHER SETTINGS > CHECK FOR APPLICATION UPDATES (ALWAYS / DAILY /
WEEKLY / MONTHLY / NEVER) and INCLUDE PRERELEASES IN UPDATE CHECKS (default off). Neither
triggers a check.

### What the user is shown (it does NOT say what changed)

- Popup (`ViewController.cpp:431`, text built `ApplicationUpdater.cpp:488-492`, uppercased :503):
  body is ONE line, `NEW RELEASE AVAILABLE: 3.4.1-MAD.<N>`. Buttons UPDATE / CANCEL, B does nothing.
  The DATE is emitted only on the PRERELEASE branch (:480-487), so stable never shows it.
  Not guaranteed to be the top window: the post-update reapply box, migrated-files dialog and
  input-config dialog are pushed after it.
- APPLICATION UPDATER screen before download (`GuiApplicationUpdater.cpp` constructor, the only
  place grid entries are created): title, "INSTALLATION STEPS:", three steps, "STATUS MESSAGE:",
  an empty status line, an empty changelog line, buttons. No version, date, size or changelog.
  Steps read DOWNLOAD NEW RELEASE / INSTALL PACKAGE / RESTART ES-DE TO COMPLETE THE UPDATE
  (:56-57, :66-68, :74-78; the third is QUIT AND MANUALLY RESTART ES-DE without `$MAD_WRAPPER`).
  CHANGE DIRECTORY is suppressed because the feed declares `LinuxSteamDeckAppImage`.
- Downloading: `DOWNLOADING <n>%` (percent only, no bytes).
- After download: step 1 green tick, button relabels to INSTALL, status =
  `Downloaded ES-DE-MAD.AppImage_3.4.1-mad.<N>` (:372-373, :475-488).
- After install: step 2 green tick, `Successfully installed as ES-DE-MAD.AppImage`, plus the one
  fork-added line `Find the detailed changelog at github.com/mmadalone/mad/releases`
  (deck-patches, `GuiApplicationUpdater.cpp:498-500`), and the whole button row is REPLACED by a
  single RESTART button (`QuitMode::RESTART` re-execs `$MAD_WRAPPER`, same PID so gamescope sees
  a seamless relaunch) or QUIT if the wrapper is absent (:501-516).
- No update available: total silence, only `No application updates available` in `es_log.txt`.

### The unused "what changed" hook (cheapest fix)

The feed's PER-PACKAGE `message` field IS parsed (`ApplicationUpdater.cpp:347-352`), truncated to
280 chars (:463-464) and appended under the version line in the popup (:500-501). CI currently
writes `"message": ""` (`build-appimage.yml:114`), so nothing appears. Filling it would show a
release summary AT ACCEPT TIME with zero C++ changes.

CAUTION: a TOP-LEVEL `message` key in the JSON is treated as a GitHub-API-style server ERROR
(`ApplicationUpdater.cpp:166-171`), which aborts the whole check silently with only a log warning
and no popup. The field must stay INSIDE the package object.

---

## 3. Path 2: the launcher scripts (manual, invisible)

- Pushing to `main` runs `tests.yml` only. No build, no release, no asset the Deck consumes.
- No cron, no systemd timer, no background fetch, no in-app prompt, no runtime version compare.
  `launchers/VERSION` (0.4.0) is decorative: one reader, never compared at runtime. The real
  compatibility gate is the separate protocol integer (`MadBackend.h` PROTO, in lock-step with
  `mad-backend.py`).
- `deck-post-update.sh` and `deck-fetch-esde.sh` never pull git. `deck-fetch-esde.sh` only
  downloads the AppImage from the `latest-steamdeck` tag.
- The only delivery: `git -C ~/Emulation/tools/launchers pull --ff-only`, or `install.sh`
  (which does that pull, then re-runs the deploy steps).

### After a pull, what is actually live

Live immediately (no redeploy):
- every top-level launcher script and `lib/*.py`: `es_systems.xml` invokes them by ABSOLUTE PATH
  into the clone, and each hook shells out to a fresh `python3` per game launch.

Live on the next MAD panel open:
- `lib/madsrv/*.py`. `MadBackend.cpp:74` spawns `~/Emulation/tools/launchers/mad-backend.py`
  from the clone per panel session (:133 logs the PID) and it dies with the panel. Verified
  2026-08-06: no `mad-backend` process exists while the panel is closed. Closing and reopening
  the panel IS the restart. A brand-new `*_cmds.py` must still be registered in BOTH import
  blocks of `mad-backend.py`.

Needs an explicit redeploy step:
- `hooks/**` are a COPY at `~/ES-DE/scripts/`. `deck-post-update.sh` redeploys only the derived
  CORE hooks (`mad_redeploy_core_hooks`, deck-post-update.sh:368). The gated ones (launchscreen,
  sinden, dolphin-wii-mode, wiimote-quit-watcher), `hooks/launchscreen-pack.sh` and
  `hooks/system-select/05-record-view.sh` need `install.sh`.
- `~/Applications/ES-DE.AppImage` (the launch WRAPPER) is GENERATED from a heredoc inside
  `deck-post-update.sh`. Never hand-edit; apply with `deck-post-update.sh --wrapper`.
- `lib/mad_launch_wrap.py` and `lib/es_find_rules.py` only reach ES-DE after the wrap/ensure
  helpers rewrite `~/ES-DE/custom_systems/es_systems.xml` and `es_find_rules.xml`, which ES-DE
  reads AT STARTUP, so that also needs an ES-DE restart.

Never re-delivered at all:
- copy-if-absent seeds: `data/es_systems_sorting.reference.xml`, `controller-policy.example.toml`,
  `sinden.example.conf`. Editing them in git has zero effect on an existing Deck.

---

## 4. State captured 2026-08-06 (for reference, will drift)

- Installed `~/Applications/ES-DE-MAD.AppImage`: 125,895,152 bytes, 2026-08-04 04:13,
  md5 `2e946ad85654d868f38928c02a93c00d`.
- Published feed: version `3.4.1-mad.160`, release `160`, date `2026-08-04`, same md5,
  message empty, prerelease block empty. So installed run 160 == published run 160.
- `main` HEAD `775d417` == origin, clean tree. `deck-patches` HEAD `d92cf6a6c` == origin.
- `~/ES-DE/scripts/` byte-identical to `launchers/hooks/` (31 files each side).

---

## 5. Known gaps / footguns (open, nothing fixed yet)

1. The two halves update independently and nothing enforces they match. An in-app update can
   install a binary whose new RPC or hook the deployed scripts lack. This already happened at
   run-50 (see the release-skew note in `turnkey-install`).
2. The updater never says what is changing: a version number before, a URL after. The `message`
   field that would fix it is wired end to end and fed an empty string.
3. Script updates are completely silent. No prompt, no nag, no on-screen indicator of which
   script revision is deployed. `esde-health-check.sh` nags on a SteamOS BUILD_ID change and
   reports missing components, never a git revision.
4. `install.sh` SKIPS the pull entirely when the clone has local modifications or untracked
   files, and only warns. That is precisely when you would want to be told nothing arrived.
   (Practical consequence: do not drop scratch files into the launchers clone.)
5. Hooks are the sharpest edge, because a successful pull can still leave the OLD hook running,
   and `deck-post-update.sh` covers only the core set.
6. `es_log.txt` reports upstream `ES-DE 3.4.1 (r51)`, which is NOT the MAD run number. The MAD
   number appears only on the main-menu version label (`3.4.1-MAD.<N>`, `GuiMenu.cpp:2838-2843`).
7. Only ONE level of rollback, silently overwritten each update. NOT an upstream bug and not a
   flaw in the update flow: `GuiApplicationUpdater.cpp:427` names the retained file
   `<target>_<PROGRAM_VERSION_STRING>.OLD`, which is stock (the fork's only hunks in that file
   are the changelog URL and the RESTART button) and is documented behaviour
   (USERGUIDE.md:127). Upstream assumes consecutive releases carry DIFFERENT version strings, so
   a normal user accumulates one rollback per version. WE break that assumption: every MAD build
   reports `3.4.1` and is distinguished only by `MAD_RELEASE_NUMBER`, which never reaches
   `PROGRAM_VERSION_STRING` - so all our builds collide on `ES-DE-MAD.AppImage_3.4.1.OLD` (live:
   dated 2026-08-04 02:25 = run 159; every earlier one is gone). The cause and the fix are both
   fork-side: either put the run number in the rollback name (one line, safe) or fold the MAD
   suffix into the version string (riskier - `PROGRAM_VERSION_STRING` is also read by log output,
   the settings-migration check in `main.cpp` and the theme downloader). Neither touches the
   update check, which compares run numbers, not version strings. The manual `.pre-*` copies in
   item 8 are the ad-hoc workaround that has been covering for this.
8. Housekeeping: `~/Applications` holds 17 manual `.pre-*` rollback copies plus the `.OLD`,
   about 2.0 GB, and a stale `ES-DE.AppImage.real` (124 MB, 2026-06-04). The wrapper's
   re-extract step needs roughly 2x the AppImage size free. Nothing has been deleted.

## 6. Candidate follow-ups (not started, not approved)

- Fill the feed `message` from the CI release notes (one line in `build-appimage.yml`, no C++).
- A script-revision check so the MAD panel can say "scripts are behind the binary", using the
  existing PROTO integer or a committed revision stamp rather than `VERSION`.
- Make `install.sh` fail loudly (not warn) when a dirty clone blocks the pull.
- Extend the hook redeploy in `deck-post-update.sh` to the gated hooks, or make `install.sh` the
  single documented "apply everything" entry point.
- Per-build rollback naming, plus a prune policy for `~/Applications/*.pre-*`.
